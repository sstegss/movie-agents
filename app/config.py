"""Настройки из переменных окружения. Значения по умолчанию для запуска на хосте."""

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Models:
    # Основная: 4.2B, квантованная Q4_K_M.
    main: str = _env("MODEL_MAIN", "qwen3.5:4b")
    # Дешёвая: изначально маленькая (1.9B, Q8_0), стабильно вызывает инструменты.
    cheap: str = _env("MODEL_CHEAP", "qwen3.5:2b")
    # Самая маленькая (0.75B, Q8_0): только болтовня.
    # Инструменты вызывает нестабильно: с длинным промптом и профилем
    # пользователя перестаёт их вызывать совсем, поэтому агентам с
    # инструментами не даётся (см. evals/results/llm_comparison.md).
    tiny: str = _env("MODEL_TINY", "qwen3.5:0.8b")
    # Валидатор: другое семейство (Google), чтобы не повторять ошибки Qwen.
    validator: str = _env("MODEL_VALIDATOR", "gemma4:e2b")


@dataclass(frozen=True)
class Settings:
    ollama_url: str = _env("OLLAMA_URL", "http://127.0.0.1:11434")
    models: Models = field(default_factory=Models)
    data_dir: Path = Path(_env("DATA_DIR", str(ROOT / "data" / "runtime")))
    agent_dir: Path = ROOT / "agent"
    skills_dir: Path = ROOT / "skills"
    # Сколько последних сообщений диалога видит LLM (краткосрочная память).
    short_term_window: int = int(_env("SHORT_TERM_WINDOW", "8"))
    # Сколько раз валидатор может вернуть ответ на доработку.
    max_revisions: int = int(_env("MAX_REVISIONS", "1"))
    # Максимум шагов агента с инструментами за один ответ.
    max_tool_steps: int = int(_env("MAX_TOOL_STEPS", "4"))
    llm_timeout: float = float(_env("LLM_TIMEOUT", "240"))
    phoenix_endpoint: str = _env("PHOENIX_COLLECTOR_ENDPOINT", "")
    log_level: str = _env("LOG_LEVEL", "INFO")


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
