"""Реестр моделей и роутинг по ним.

Роли:
- tiny: qwen3.5:0.8b, болтовня (без инструментов);
- cheap: qwen3.5:2b, простые запросы с инструментами и извлечение вкусов;
- main: qwen3.5:4b (квантованная Q4_K_M), сложные запросы и эскалации;
- validator: gemma4:e2b, другое семейство моделей для проверки ответов.

Какую роль взять для запроса, решает Jev-like модель (app/gate).
"""

from __future__ import annotations

import time
from functools import lru_cache

from langchain_core.callbacks import BaseCallbackHandler
from langchain_ollama import ChatOllama

from app import telemetry as T
from app.config import settings

# Условная стоимость 1000 токенов. Локальные модели денег не стоят, но
# тратят время CPU и память примерно пропорционально числу активных
# параметров (в миллиардах). Эта метрика нужна, чтобы видеть, сколько
# экономит роутинг, так же как это делают со стоимостью API.
COST_PER_1K = {
    "qwen3.5:0.8b": 0.8,
    "qwen3.5:2b": 2.0,
    "qwen3.5:4b": 4.0,
    "gemma4:e2b": 2.0,
    "gemma4:e4b": 4.0,
    "llama3.2:3b": 3.0,
}


class MetricsCallback(BaseCallbackHandler):
    """Пишет в Prometheus время, токены и стоимость каждого вызова LLM."""

    def __init__(self, model: str, agent: str):
        self.model, self.agent = model, agent
        self._start: dict = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
        self._start[run_id] = time.perf_counter()

    def on_llm_end(self, response, *, run_id, **kw):
        dt = time.perf_counter() - self._start.pop(run_id, time.perf_counter())
        T.LLM_LATENCY.labels(self.model, self.agent).observe(dt)
        T.LLM_CALLS.labels(self.model, self.agent, "ok").inc()
        usage = {}
        for gen in response.generations:
            for g in gen:
                msg = getattr(g, "message", None)
                if msg is not None and getattr(msg, "usage_metadata", None):
                    usage = msg.usage_metadata
        tin, tout = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        T.LLM_TOKENS.labels(self.model, "input").inc(tin)
        T.LLM_TOKENS.labels(self.model, "output").inc(tout)
        T.LLM_COST.labels(self.model).inc((tin + tout) / 1000 * COST_PER_1K.get(self.model, 4.0))
        T.log_event("llm call", model=self.model, agent=self.agent, seconds=round(dt, 2), tokens_in=tin, tokens_out=tout)

    def on_llm_error(self, error, *, run_id, **kw):
        self._start.pop(run_id, None)
        T.LLM_CALLS.labels(self.model, self.agent, "error").inc()


def model_for(tier: str) -> str:
    return {"tiny": settings.models.tiny, "cheap": settings.models.cheap, "main": settings.models.main, "validator": settings.models.validator}[tier]


@lru_cache(maxsize=32)
def get_llm(tier: str, agent: str, json_mode: bool = False, model: str | None = None) -> ChatOllama:
    name = model or model_for(tier)
    return ChatOllama(
        model=name,
        base_url=settings.ollama_url,
        # Режим размышлений у Qwen3.5 на CPU стоит минуты, а для наших
        # коротких ответов почти ничего не даёт.
        reasoning=False,
        # Служебные ответы (JSON для памяти и валидатора) должны быть стабильными.
        temperature=0.0 if json_mode or tier == "validator" else 0.2,
        num_ctx=8192,
        num_predict=600,
        keep_alive="10m",
        format="json" if json_mode else None,
        client_kwargs={"timeout": settings.llm_timeout},
        callbacks=[MetricsCallback(name, agent)],
        metadata={"agent": agent, "tier": tier},
    )
