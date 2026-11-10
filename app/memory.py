"""Память ассистента: три слоя.

1. Краткосрочная (рабочая): сообщения текущего диалога. Хранит чекпоинтер
   LangGraph (SQLite), в LLM уходят только последние N сообщений, чтобы
   маленькое контекстное окно не забивалось.
2. Долговременная семантическая: факты о вкусах («любит фантастику»,
   «не понравился Титаник», «уже видел Начало»). LangGraph Store (SQLite),
   ключ факта однозначен (movie:<id>, genre:<name>, person:<name>), поэтому
   новый факт о том же объекте заменяет старый, а не копится рядом.
3. Эпизодическая: что ассистент уже советовал и когда. Нужна, чтобы не
   предлагать одно и то же и отвечать на «что ты мне советовал».

Актуальность поддерживается так:
- противоречия решаются в пользу нового факта (тот же ключ);
- вес факта затухает со временем (период полураспада HALF_LIFE_DAYS),
  факты с весом ниже MIN_WEIGHT не попадают в профиль;
- эпизоды старше EPISODE_TTL_DAYS удаляются при чтении;
- «забудь ...» удаляет факт явно.

Векторного поиска здесь нет намеренно: фактов у одного человека десятки,
они короткие и структурированные, их проще и надёжнее читать целиком.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from functools import lru_cache

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

from app import telemetry as T
from app.config import settings

HALF_LIFE_DAYS = 180
MIN_WEIGHT = 0.15
EPISODE_TTL_DAYS = 90
MAX_EPISODES = 20
DAY = 86400


def _connect(name: str) -> sqlite3.Connection:
    path = settings.data_dir / name
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@lru_cache(maxsize=1)
def get_checkpointer() -> SqliteSaver:
    saver = SqliteSaver(_connect("short_term.sqlite"))
    saver.setup()
    return saver


@lru_cache(maxsize=1)
def get_store() -> SqliteStore:
    store = SqliteStore(_connect("long_term.sqlite"))
    store.setup()
    return store


@dataclass
class Fact:
    key: str  # movie:79132 | genre:Horror | person:nolan | note:<hash>
    kind: str  # movie | genre | person | note
    name: str  # как показывать человеку и LLM
    sentiment: str  # like | dislike | seen
    movie_id: int | None = None
    source: str = ""  # исходная фраза пользователя
    updated_at: float = field(default_factory=time.time)
    confidence: float = 1.0

    def weight(self, now: float | None = None) -> float:
        age_days = ((now or time.time()) - self.updated_at) / DAY
        return self.confidence * 0.5 ** (age_days / HALF_LIFE_DAYS)


@dataclass
class Profile:
    facts: list[Fact]
    episodes: list[dict]

    def liked_movies(self) -> list[int]:
        return [f.movie_id for f in self.facts if f.kind == "movie" and f.sentiment == "like" and f.movie_id]

    def disliked_movies(self) -> list[int]:
        return [f.movie_id for f in self.facts if f.kind == "movie" and f.sentiment == "dislike" and f.movie_id]

    def seen_movies(self) -> list[int]:
        return [f.movie_id for f in self.facts if f.kind == "movie" and f.movie_id]

    def recommended_before(self) -> list[int]:
        return [m for e in self.episodes for m in e.get("movie_ids", [])]

    def genres(self, sentiment: str) -> list[str]:
        return [f.name for f in self.facts if f.kind == "genre" and f.sentiment == sentiment]

    def as_text(self) -> str:
        """Профиль для промпта: коротко, чтобы не тратить контекст."""
        if not self.facts and not self.episodes:
            return "Пока ничего не известно о вкусах пользователя."
        groups = {"like": [], "dislike": [], "seen": []}
        for f in sorted(self.facts, key=lambda f: -f.weight()):
            groups[f.sentiment].append(f.name)
        lines = []
        if groups["like"]:
            lines.append("Нравится: " + "; ".join(groups["like"]))
        if groups["dislike"]:
            lines.append("Не нравится: " + "; ".join(groups["dislike"]))
        if groups["seen"]:
            lines.append("Уже смотрел: " + "; ".join(groups["seen"]))
        if self.episodes:
            last = self.episodes[0]
            lines.append(f"Последний раз советовали: {', '.join(last.get('titles', [])[:5])}")
        return "\n".join(lines)


class LongTermMemory:
    def __init__(self, store: SqliteStore | None = None):
        self.store = store or get_store()

    @staticmethod
    def _ns(user_id: str, kind: str):
        return ("users", user_id, kind)

    def load(self, user_id: str) -> Profile:
        now = time.time()
        facts = []
        for item in self.store.search(self._ns(user_id, "prefs"), limit=500):
            f = Fact(**item.value)
            if f.weight(now) >= MIN_WEIGHT:
                facts.append(f)
        episodes = []
        for item in self.store.search(self._ns(user_id, "episodes"), limit=200):
            if now - item.value["ts"] > EPISODE_TTL_DAYS * DAY:
                self.store.delete(self._ns(user_id, "episodes"), item.key)
                continue
            episodes.append(item.value)
        episodes.sort(key=lambda e: -e["ts"])
        T.MEMORY_FACTS.labels(user_id).set(len(facts))
        return Profile(facts, episodes[:MAX_EPISODES])

    def upsert(self, user_id: str, fact: Fact) -> None:
        # Тот же ключ = тот же объект: новое мнение заменяет старое.
        fact.updated_at = time.time()
        self.store.put(self._ns(user_id, "prefs"), fact.key, fact.__dict__)
        T.MEMORY_WRITES.labels(fact.kind).inc()

    def forget(self, user_id: str, key: str) -> bool:
        ns = self._ns(user_id, "prefs")
        if self.store.get(ns, key) is None:
            return False
        self.store.delete(ns, key)
        T.MEMORY_WRITES.labels("forget").inc()
        return True

    def add_episode(self, user_id: str, query: str, movie_ids: list[int], titles: list[str]) -> None:
        ts = time.time()
        self.store.put(
            self._ns(user_id, "episodes"),
            f"{ts:.6f}",
            {"ts": ts, "query": query[:200], "movie_ids": movie_ids, "titles": titles},
        )
        T.MEMORY_WRITES.labels("episode").inc()

    def wipe(self, user_id: str) -> None:
        for kind in ("prefs", "episodes"):
            for item in self.store.search(self._ns(user_id, kind), limit=1000):
                self.store.delete(self._ns(user_id, kind), item.key)
