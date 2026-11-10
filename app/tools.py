"""Инструменты агентов поверх каталога и RecSys.

Инструменты собираются на каждый запрос: им нужен профиль текущего
пользователя (чтобы убрать просмотренное и учесть вкусы) и общий журнал,
куда записываются все фильмы, которые вернули инструменты. По этому
журналу валидатор потом проверяет, что агент не выдумал фильм.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from langchain_core.tools import StructuredTool

from app import telemetry as T
from app.catalog import Catalog, Movie
from app.memory import Profile


@dataclass
class ToolLog:
    movie_ids: set[int] = field(default_factory=set)
    calls: list[dict] = field(default_factory=list)

    def record(self, name: str, args: dict, movies: list[Movie]):
        self.movie_ids.update(m.movie_id for m in movies)
        self.calls.append({"tool": name, "args": args, "result": [m.title for m in movies]})


def _fmt(movies: list[Movie]) -> str:
    if not movies:
        return "Ничего не найдено."
    return "\n".join(f"- {m.short()}" for m in movies)


def build_tools(catalog: Catalog, profile: Profile, log: ToolLog) -> dict[str, StructuredTool]:
    exclude = list(set(profile.seen_movies()) | set(profile.recommended_before()))

    def wrap(name, fn):
        def run(**kwargs):
            try:
                out = fn(**kwargs)
                T.TOOL_CALLS.labels(name, "ok").inc()
                return out
            except Exception as e:  # ошибку отдаём модели текстом, пусть исправит аргументы
                T.TOOL_CALLS.labels(name, "error").inc()
                return f"Ошибка инструмента: {e}"

        return run

    def search_movies(query: str = "", genre: str | None = None, year_from: int | None = None,
                      year_to: int | None = None, limit: int = 8) -> str:
        """Find a movie by its English title (typos allowed), e.g. query="Matrix".
        query is a title only, not a genre or description: to browse a genre leave query empty and set genre.
        Genre must be a MovieLens genre, e.g. Comedy, Sci-Fi, Horror."""
        movies = catalog.search(query, genre, year_from, year_to, min(limit, 15))
        log.record("search_movies", {"query": query, "genre": genre}, movies)
        return _fmt(movies)

    def movie_details(movie_id: int) -> str:
        """Get year, genres, average rating (0.5-5), number of ratings and popular tags for a movie id."""
        d = catalog.details(int(movie_id))
        if d is None:
            return f"Фильма с id={movie_id} нет в каталоге."
        log.record("movie_details", {"movie_id": movie_id}, [catalog.get(int(movie_id))])
        return json.dumps(d, ensure_ascii=False)

    def similar_movies(movie_id: int, k: int = 6) -> str:
        """Movies similar to the given movie id (watched together + similar genres and tags)."""
        movies = catalog.recommend(liked=[int(movie_id)], disliked=profile.disliked_movies(), exclude=exclude, k=min(k, 10))
        log.record("similar_movies", {"movie_id": movie_id}, movies)
        return _fmt(movies)

    def recommend_for_user(genre: str | None = None, year_from: int | None = None,
                           year_to: int | None = None, k: int = 6) -> str:
        """Personal recommendations from the user's taste profile (liked/disliked movies from memory).
        Optional filters: MovieLens genre, year range."""
        liked = profile.liked_movies()
        # Нелюбимые жанры отсеиваем после рекомендаций, поэтому берём с
        # запасом. Если человек сам попросил этот жанр, запрос важнее профиля.
        movies = catalog.recommend(liked, profile.disliked_movies(), exclude, genre, year_from, year_to, k=min(k, 10) * 3)
        bad = set(profile.genres("dislike"))
        movies = [m for m in movies if not bad.intersection(m.genres) or (genre and genre in m.genres)][: min(k, 10)]
        log.record("recommend_for_user", {"genre": genre, "liked": len(liked)}, movies)
        note = "" if liked else "(Профиль пуст, это лучшие по оценкам фильмы.)\n"
        return note + _fmt(movies)

    def top_movies(genre: str | None = None, year_from: int | None = None, year_to: int | None = None, k: int = 6) -> str:
        """Best rated movies with optional genre and year filters."""
        movies = catalog.recommend([], exclude=exclude, genre=genre, year_from=year_from, year_to=year_to, k=min(k, 10))
        log.record("top_movies", {"genre": genre}, movies)
        return _fmt(movies)

    fns = [search_movies, movie_details, similar_movies, recommend_for_user, top_movies]
    return {f.__name__: _make_tool(f, wrap(f.__name__, f)) for f in fns}


def _make_tool(fn, runner) -> StructuredTool:
    # Схему аргументов берём из сигнатуры исходной функции, а выполняем
    # обёртку с метриками и перехватом ошибок.
    tool = StructuredTool.from_function(fn, name=fn.__name__, description=fn.__doc__)
    tool.func = runner
    return tool
