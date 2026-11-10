"""Граф мультиагентной системы на LangGraph.

    START -> gate -> (blocked) -> refuse ------------------------------> finalize -> END
                  -> load_memory -> (есть вкусы) -> remember -+
                                 -------------------------------+-> intent:
        preference -> ack ---------------------------------------------> finalize
        chitchat   -> chat --------------------------------------------> finalize
        recommend  -> recommender <-> tools -> validate -> pass -------> finalize
        movie_info -> info        <-> tools -> validate -> fail -> revise (main) или fallback

Агенты не вызывают друг друга, все переходы заданы здесь. Поэтому
траектория запроса (какие узлы прошёл, какие инструменты вызвал) видна в
трейсе Phoenix целиком.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app import telemetry as T
from app.catalog import get_catalog
from app.config import settings
from app.gate.model import get_gate
from app.llm import get_llm, model_for
from app.memory import Fact, LongTermMemory, Profile, get_checkpointer
from app.skills import skill_for_agent, system_prompt
from app.tools import ToolLog, build_tools
from app.validator import fallback_answer, validate


class State(TypedDict, total=False):
    # Краткосрочная память: весь диалог, хранится чекпоинтером.
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    request_id: str
    query: str
    started: float
    gate: dict
    tier: str
    agent: str
    # Рабочие сообщения агента с инструментами внутри одного запроса.
    scratch: list[AnyMessage]
    tool_calls: list[dict]
    tool_movie_ids: list[int]
    tool_steps: int
    answer: str
    validation: dict
    revisions: int
    remembered: list[str]
    trajectory: list[str]


REFUSALS = {
    "off_topic": "Я помогаю только с кино: могу посоветовать фильм или рассказать о фильме из каталога. "
                 "С этим вопросом, к сожалению, не помогу.",
    "unsafe": "Это я сделать не могу. Могу посоветовать фильм или рассказать о фильме из каталога.",
}


def _step(state: State, name: str) -> list[str]:
    return [*state.get("trajectory", []), name]


def _window(state: State) -> list[AnyMessage]:
    """Последние сообщения диалога без текущего запроса."""
    history = [m for m in state.get("messages", []) if isinstance(m, (HumanMessage, AIMessage))]
    return history[:-1][-settings.short_term_window:]


def _profile(state: State) -> Profile:
    return LongTermMemory().load(state["user_id"])


def _tool_log(state: State) -> ToolLog:
    return ToolLog(set(state.get("tool_movie_ids", [])), list(state.get("tool_calls", [])))


# --- Узлы ---------------------------------------------------------------------


def start_turn(state: State) -> State:
    query = state["messages"][-1].content
    # Поля одного запроса обнуляем: в чекпоинте лежат значения прошлого.
    return {
        "query": query,
        "request_id": state.get("request_id") or uuid.uuid4().hex[:12],
        "started": time.time(),
        "gate": {},
        "tier": "",
        "agent": "",
        "scratch": [],
        "tool_calls": [],
        "tool_movie_ids": [],
        "tool_steps": 0,
        "answer": "",
        "validation": {},
        "revisions": 0,
        "remembered": [],
        "trajectory": [],
    }


def gate_node(state: State) -> State:
    t = time.perf_counter()
    decision = get_gate().decide(state["query"])
    T.GATE_LATENCY.observe(time.perf_counter() - t)
    T.GATE_CONFIDENCE.observe(decision.confidence)
    T.GATE_DECISIONS.labels(decision.intent, decision.model_tier, str(decision.blocked)).inc()
    T.log_event("gate decision", request_id=state["request_id"], **decision.as_dict())
    return {"gate": decision.as_dict(), "tier": decision.model_tier, "trajectory": _step(state, "gate")}


def refuse_node(state: State) -> State:
    return {"answer": REFUSALS[state["gate"]["intent"]], "trajectory": _step(state, "refuse")}


def remember_node(state: State) -> State:
    """Агент памяти: извлекает факты о вкусах и пишет их в профиль."""
    # Извлечение на 2b: в сравнении моделей F1 по фактам у 0.8b был 0.25,
    # у 2b 1.0 (evals/results/llm_comparison.md). Лишние ~6 с того стоят.
    llm = get_llm("cheap", "memory", json_mode=True)
    raw = llm.invoke([SystemMessage(system_prompt("memory")), HumanMessage(state["query"])]).content
    try:
        facts = json.loads(raw).get("facts", [])
    except (json.JSONDecodeError, AttributeError):
        T.ERRORS.labels("memory_parse").inc()
        facts = []

    catalog, mem = get_catalog(), LongTermMemory()
    saved = []
    for f in facts if isinstance(facts, list) else []:
        if not isinstance(f, dict):
            continue
        kind, name, sentiment = f.get("kind"), str(f.get("name", "")).strip(), f.get("sentiment")
        if not name or kind not in ("movie", "genre", "person") or sentiment not in ("like", "dislike", "seen", "forget"):
            continue
        movie_id = None
        if kind == "movie":
            movie = catalog.find_by_title(name)
            if movie is None:
                continue  # фильма нет в каталоге: запоминать бесполезно
            movie_id, name = movie.movie_id, movie.title
            key = f"movie:{movie_id}"
        elif kind == "genre":
            match = next((g for g in catalog.genres if g.lower() == name.lower()), None)
            if match is None:
                continue
            name, key = match, f"genre:{match}"
        else:
            key = f"person:{name.lower()}"
        if sentiment == "forget":
            if mem.forget(state["user_id"], key):
                saved.append(f"забыл: {name}")
            continue
        mem.upsert(state["user_id"], Fact(key, kind, name, sentiment, movie_id, source=state["query"][:200]))
        saved.append({"like": "нравится", "dislike": "не нравится", "seen": "видел"}[sentiment] + f": {name}")
    T.log_event("memory updated", request_id=state["request_id"], facts=saved)
    return {"remembered": saved, "trajectory": _step(state, "remember")}


def ack_node(state: State) -> State:
    """Ответ на сообщение о вкусах. Шаблон, без LLM: тут нечего генерировать."""
    saved = state.get("remembered", [])
    if saved:
        answer = "Запомнил. " + "; ".join(saved) + ". Учту, когда буду советовать."
    else:
        answer = ("Понял. Не нашёл в сообщении конкретных фильмов из каталога или жанров, "
                  "поэтому ничего не записал. Можете назвать фильм или жанр точнее.")
    return {"answer": answer, "trajectory": _step(state, "ack")}


MEMORY_QUESTION = re.compile(r"(что|чего)\s+ты\s+(обо|про)\s+мне|помнишь|мои\s+вкус|what\s+do\s+you\s+(know|remember)", re.I)


def chat_node(state: State) -> State:
    profile = _profile(state)
    if MEMORY_QUESTION.search(state["query"]):
        # Пересказ профиля шаблоном: в e2e 0.8b проигнорировала профиль в
        # промпте и ответила, что ничего не помнит. Здесь нечего генерировать.
        known = profile.as_text()
        answer = ("Вот что я о вас помню:\n" + known + "\n\nЕсли что-то из этого неверно, скажите «забудь ...».") \
            if profile.facts or profile.episodes else "Пока я ничего о вас не знаю. Расскажите, какие фильмы и жанры вам нравятся."
        return {"answer": answer, "trajectory": _step(state, "chat:profile")}
    llm = get_llm(state["tier"] or "tiny", "chat")
    msgs = [SystemMessage(system_prompt("chat", profile.as_text())), *_window(state), HumanMessage(state["query"])]
    answer = llm.invoke(msgs).content.strip()
    return {"answer": answer, "trajectory": _step(state, "chat")}


def _agent_node(agent: str):
    def node(state: State) -> State:
        profile = _profile(state)
        skill = skill_for_agent(agent)
        tools = build_tools(get_catalog(), profile, ToolLog())
        allowed = [tools[name] for name in skill.tools]
        llm = get_llm(state["tier"], agent).bind_tools(allowed)

        scratch = list(state.get("scratch", []))
        if not scratch:
            scratch = [SystemMessage(system_prompt(agent, profile.as_text())), *_window(state), HumanMessage(state["query"])]
        if state.get("tool_steps", 0) >= settings.max_tool_steps:
            # Лимит шагов: просим ответить тем, что уже есть.
            scratch.append(HumanMessage("Лимит вызовов инструментов исчерпан. Ответь по уже полученным результатам."))
            llm = get_llm(state["tier"], agent)
        reply = llm.invoke(scratch)
        scratch.append(reply)
        update = {"scratch": scratch, "agent": agent, "trajectory": _step(state, f"{agent}[{model_for(state['tier'])}]")}
        if not getattr(reply, "tool_calls", None):
            update["answer"] = (reply.content or "").strip()
        return update

    node.__name__ = f"{agent}_node"
    return node


def tools_node(state: State) -> State:
    profile = _profile(state)
    log = _tool_log(state)
    tools = build_tools(get_catalog(), profile, log)
    allowed = set(skill_for_agent(state["agent"]).tools)
    scratch = list(state["scratch"])
    names = []
    for call in scratch[-1].tool_calls:
        name = call["name"]
        if name not in allowed:
            # Агент попросил чужой инструмент: не выполняем.
            result = f"Инструмент {name} недоступен этому агенту."
            T.TOOL_CALLS.labels(name, "denied").inc()
        else:
            result = tools[name].invoke(call["args"])
        names.append(name)
        scratch.append(ToolMessage(content=str(result), tool_call_id=call["id"], name=name))
    return {
        "scratch": scratch,
        "tool_calls": log.calls,
        "tool_movie_ids": sorted(log.movie_ids),
        "tool_steps": state.get("tool_steps", 0) + 1,
        "trajectory": _step(state, "tools:" + ",".join(names)),
    }


def validate_node(state: State) -> State:
    profile = _profile(state)
    result = validate(state["query"], state["answer"], state["gate"]["intent"], get_catalog(), profile, _tool_log(state))
    T.log_event("validation", request_id=state["request_id"], **result.as_dict())
    return {"validation": result.as_dict(), "trajectory": _step(state, f"validate:{'pass' if result.passed else 'fail'}")}


def revise_node(state: State) -> State:
    """Возврат ответа агенту с замечаниями валидатора, уже на основной модели."""
    T.REVISIONS.inc()
    if state["tier"] != "main":
        T.ESCALATIONS.labels("validation_failed").inc()
    problems = "\n".join(f"- {p}" for p in state["validation"]["problems"])
    scratch = [*state["scratch"], HumanMessage(
        "Проверка нашла проблемы в ответе:\n" + problems +
        "\nИсправь ответ. Используй только фильмы из результатов инструментов, при необходимости вызови инструмент ещё раз."
    )]
    return {"scratch": scratch, "tier": "main", "revisions": state.get("revisions", 0) + 1,
            "tool_steps": 0, "answer": "", "trajectory": _step(state, "revise")}


def fallback_node(state: State) -> State:
    T.ERRORS.labels("validation_exhausted").inc()
    answer = fallback_answer(_tool_log(state), get_catalog(), state["gate"]["intent"])
    return {"answer": answer, "trajectory": _step(state, "fallback")}


def finalize_node(state: State) -> State:
    intent = state.get("gate", {}).get("intent", "unknown")
    if intent == "recommend" and state.get("validation", {}).get("mentioned"):
        catalog = get_catalog()
        ids = state["validation"]["mentioned"]
        LongTermMemory().add_episode(state["user_id"], state["query"], ids, [catalog.get(i).title for i in ids])
    T.REQUESTS.labels(intent, state.get("tier") or "none").inc()
    T.REQUEST_LATENCY.labels(intent).observe(time.time() - state["started"])
    T.log_event("request done", request_id=state["request_id"], user=state["user_id"], intent=intent,
                tier=state.get("tier"), trajectory=state.get("trajectory", []),
                seconds=round(time.time() - state["started"], 2))
    return {"messages": [AIMessage(state["answer"])], "trajectory": _step(state, "finalize")}


# --- Переходы -----------------------------------------------------------------


def after_gate(state: State) -> str:
    return "refuse" if state["gate"]["blocked"] else "load_memory"


def after_memory(state: State) -> str:
    return "remember" if state["gate"]["has_preference"] else route_intent(state)


def route_intent(state: State) -> str:
    return {"preference": "ack", "chitchat": "chat", "recommend": "recommender", "movie_info": "info"}[state["gate"]["intent"]]


def after_agent(state: State) -> str:
    last = state["scratch"][-1]
    if getattr(last, "tool_calls", None) and state.get("tool_steps", 0) < settings.max_tool_steps + 1:
        return "tools"
    return "validate"


def after_validate(state: State) -> str:
    if state["validation"]["passed"]:
        return "finalize"
    return "revise" if state.get("revisions", 0) < settings.max_revisions else "fallback"


def build_graph(checkpointer=None):
    g = StateGraph(State)
    g.add_node("start_turn", start_turn)
    g.add_node("gate", gate_node)
    g.add_node("refuse", refuse_node)
    g.add_node("load_memory", lambda s: {"trajectory": _step(s, "load_memory")})
    g.add_node("remember", remember_node)
    g.add_node("ack", ack_node)
    g.add_node("chat", chat_node)
    g.add_node("recommender", _agent_node("recommender"))
    g.add_node("info", _agent_node("info"))
    g.add_node("tools", tools_node)
    g.add_node("validate", validate_node)
    g.add_node("revise", revise_node)
    g.add_node("fallback", fallback_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "start_turn")
    g.add_edge("start_turn", "gate")
    g.add_conditional_edges("gate", after_gate, ["refuse", "load_memory"])
    g.add_conditional_edges("load_memory", after_memory, ["remember", "ack", "chat", "recommender", "info"])
    g.add_conditional_edges("remember", route_intent, ["ack", "chat", "recommender", "info"])
    for agent in ("recommender", "info"):
        g.add_conditional_edges(agent, after_agent, ["tools", "validate"])
    g.add_conditional_edges("tools", lambda s: s["agent"], ["recommender", "info"])
    g.add_conditional_edges("validate", after_validate, ["finalize", "revise", "fallback"])
    g.add_conditional_edges("revise", lambda s: s["agent"], ["recommender", "info"])
    for node in ("refuse", "ack", "chat", "fallback"):
        g.add_edge(node, "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph(get_checkpointer())
    return _graph


def ask(user_id: str, text: str, session: str = "default", request_id: str | None = None) -> dict[str, Any]:
    """Один ход диалога. Возвращает ответ и служебные данные для API и evals."""
    config = {"configurable": {"thread_id": f"{user_id}:{session}"}, "recursion_limit": 40,
              "metadata": {"user_id": user_id, "session": session}}
    try:
        state = get_graph().invoke(
            {"messages": [HumanMessage(text)], "user_id": user_id, "request_id": request_id or uuid.uuid4().hex[:12]},
            config,
        )
    except Exception:
        T.ERRORS.labels("graph").inc()
        T.log.exception("request failed", extra={"fields": {"user": user_id}})
        raise
    return {
        "answer": state["answer"],
        "request_id": state["request_id"],
        "gate": state.get("gate", {}),
        "tier": state.get("tier"),
        "model": model_for(state["tier"]) if state.get("tier") in ("tiny", "cheap", "main") else None,
        "validation": state.get("validation", {}),
        "revisions": state.get("revisions", 0),
        "remembered": state.get("remembered", []),
        "tool_calls": state.get("tool_calls", []),
        "trajectory": state.get("trajectory", []),
        "seconds": round(time.time() - state["started"], 2),
    }
