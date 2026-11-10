"""Общие фикстуры. Тесты не ходят в Ollama: LLM заменяется фальшивой."""

import os
import tempfile

# Отдельная папка под SQLite, чтобы тесты не трогали рабочую память.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="movie-agents-test-"))

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class FakeLLM(BaseChatModel):
    """Отдаёт заранее заданные ответы по очереди и запоминает, что ему прислали."""

    responses: list
    calls: list = []

    @property
    def _llm_type(self):
        return "fake"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        self.calls.append(messages)
        resp = self.responses.pop(0) if self.responses else AIMessage("")
        if isinstance(resp, str):
            resp = AIMessage(resp)
        return ChatResult(generations=[ChatGeneration(message=resp)])


@pytest.fixture(scope="session")
def catalog():
    from app.catalog import get_catalog

    return get_catalog()


@pytest.fixture
def fake_llms(monkeypatch):
    """Подменяет LLM по ролям: {"tiny": FakeLLM, "cheap": ..., "main": ..., "validator": ...}."""
    llms = {}

    def make(tier, responses):
        llms[tier] = FakeLLM(responses=list(responses), calls=[])
        return llms[tier]

    def get_llm(tier, agent, json_mode=False, model=None):
        return llms[tier]

    import app.graph
    import app.validator

    monkeypatch.setattr(app.graph, "get_llm", get_llm)
    monkeypatch.setattr(app.validator, "get_llm", get_llm)
    return make
