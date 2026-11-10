"""Observability: метрики (Prometheus), логи (JSON в stdout) и трейсы (Phoenix).

Трейсы и траектории собирает OpenInference-инструментация LangChain:
каждый узел графа, вызов LLM и инструмента становится спаном в Phoenix.
Метрики считаем сами, потому что Prometheus нужны агрегаты для алертов,
а Phoenix хранит отдельные запросы.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram

from app.config import settings

# --- Метрики ------------------------------------------------------------------

REQUESTS = Counter("agent_requests_total", "Запросы к ассистенту", ["intent", "tier"])
REQUEST_LATENCY = Histogram(
    "agent_request_latency_seconds", "Время ответа целиком", ["intent"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 240, 480),
)
ERRORS = Counter("agent_errors_total", "Ошибки при обработке запроса", ["stage"])

GATE_DECISIONS = Counter("gate_decisions_total", "Решения Jev-like модели", ["intent", "tier", "blocked"])
GATE_CONFIDENCE = Histogram("gate_confidence", "Уверенность Jev-like модели", buckets=(0.2, 0.3, 0.45, 0.6, 0.75, 0.9, 1.0))
GATE_LATENCY = Histogram("gate_latency_seconds", "Время решения Jev-like модели", buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1))

LLM_CALLS = Counter("llm_calls_total", "Вызовы LLM", ["model", "agent", "status"])
LLM_LATENCY = Histogram(
    "llm_latency_seconds", "Время одного вызова LLM", ["model", "agent"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 240),
)
LLM_TOKENS = Counter("llm_tokens_total", "Токены", ["model", "direction"])
LLM_COST = Counter("llm_cost_units_total", "Условная стоимость (см. llm.COST_PER_1K)", ["model"])

TOOL_CALLS = Counter("tool_calls_total", "Вызовы инструментов", ["tool", "status"])
VALIDATOR_VERDICTS = Counter("validator_verdicts_total", "Вердикты валидатора", ["verdict", "check"])
HALLUCINATIONS = Counter("hallucinated_titles_total", "Фильмы в ответе, которых нет в каталоге")
REVISIONS = Counter("agent_revisions_total", "Возвраты ответа на доработку")
ESCALATIONS = Counter("agent_escalations_total", "Переключения с дешёвой модели на основную", ["reason"])
MEMORY_WRITES = Counter("memory_writes_total", "Записи в долговременную память", ["kind"])
MEMORY_FACTS = Gauge("memory_facts", "Фактов о вкусах у пользователя", ["user"])
FEEDBACK = Counter("user_feedback_total", "Оценки ответов пользователем", ["value"])


@contextmanager
def timed(hist, **labels):
    t = time.perf_counter()
    try:
        yield
    finally:
        (hist.labels(**labels) if labels else hist).observe(time.perf_counter() - t)


# --- Логи ---------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Поля из extra={"fields": {...}}: request_id, intent, модель и т.д.
        payload.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
    for noisy in ("httpx", "httpcore", "urllib3", "opentelemetry"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


log = logging.getLogger("agent")


def log_event(msg: str, **fields):
    log.info(msg, extra={"fields": fields})


# --- Трейсы -------------------------------------------------------------------

_tracing_on = False


def setup_tracing():
    """Подключает Phoenix, если задан адрес коллектора."""
    global _tracing_on
    if _tracing_on or not settings.phoenix_endpoint:
        return
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from phoenix.otel import register

    # Провайдер не делаем глобальным: иначе FastAPI пишет спаны на каждый
    # /metrics и /health, и в Phoenix за ними не видно запросов к агентам.
    provider = register(project_name="movie-agents", endpoint=settings.phoenix_endpoint, batch=True,
                        verbose=False, set_global_tracer_provider=False)
    LangChainInstrumentor().instrument(tracer_provider=provider)
    _tracing_on = True
    log_event("tracing enabled", endpoint=settings.phoenix_endpoint)
