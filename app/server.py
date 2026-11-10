"""HTTP-сервис агента: /chat, /feedback, /metrics, /health.

В docker compose сервис живёт во внутренней сети без выхода в интернет.
Пользователь общается с ним через `docker compose exec agent movie-chat`,
поэтому порт наружу не публикуется.
"""

from __future__ import annotations

import re
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import Response

from app import telemetry as T
from app.config import settings

USER_RE = re.compile(r"^[\w\-]{1,64}$")
OLLAMA_UP = Gauge("ollama_up", "Ollama отвечает (1) или нет (0)")


class ChatIn(BaseModel):
    user_id: str = Field(..., examples=["lev"])
    message: str = Field(..., min_length=1, max_length=2000)
    session: str = "default"


class FeedbackIn(BaseModel):
    request_id: str
    value: int = Field(..., ge=-1, le=1)  # -1 плохо, 1 хорошо


def create_app() -> FastAPI:
    T.setup_logging()
    T.setup_tracing()

    @asynccontextmanager
    async def lifespan(_):
        # Каталог и матрицы RecSys грузятся ~5 с, делаем это до первого запроса.
        from app.catalog import get_catalog
        from app.gate.model import get_gate

        await run_in_threadpool(get_catalog)
        await run_in_threadpool(get_gate)
        T.log_event("ready")
        yield

    app = FastAPI(title="movie-agents", version="1.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        try:
            r = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3)
            models = [m["name"] for m in r.json().get("models", [])]
            OLLAMA_UP.set(1)
        except Exception:
            OLLAMA_UP.set(0)
            return {"status": "degraded", "ollama": False}
        return {"status": "ok", "ollama": True, "models": models}

    @app.post("/chat")
    async def chat(body: ChatIn):
        if not USER_RE.match(body.user_id) or not USER_RE.match(body.session):
            raise HTTPException(422, "user_id и session: латиница, цифры, _ и -")
        from app.graph import ask

        request_id = uuid.uuid4().hex[:12]
        t = time.time()
        try:
            result = await run_in_threadpool(ask, body.user_id, body.message, body.session, request_id)
        except Exception as e:
            raise HTTPException(500, f"Ошибка агента: {type(e).__name__}") from e
        result["seconds"] = round(time.time() - t, 2)
        return result

    @app.post("/feedback")
    def feedback(body: FeedbackIn):
        # Продуктовая метрика: доля ответов, которые понравились.
        T.FEEDBACK.labels({1: "good", 0: "neutral", -1: "bad"}[body.value]).inc()
        T.log_event("feedback", request_id=body.request_id, value=body.value)
        return {"ok": True}

    @app.get("/metrics")
    def metrics():
        try:
            httpx.get(f"{settings.ollama_url}/api/version", timeout=2)
            OLLAMA_UP.set(1)
        except Exception:
            OLLAMA_UP.set(0)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
