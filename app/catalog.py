"""Каталог фильмов и рекомендации поверх RecSys из С1СЗ3.

Агенты не придумывают фильмы сами: всё, что они советуют, берётся отсюда.
Пользователь ассистента не входит в MovieLens, поэтому рекомендации
считаются fold-in способом: профиль собирается на лету из фильмов, которые
человек назвал любимыми, а обученные матрицы сходства не пересчитываются.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize

from recsys import models as M
from recsys.data import load

# Веса гибрида из С1СЗ3 (подобраны там на валидации).
W_ITEMKNN, W_CONTENT, W_POPULAR = 0.4, 0.4, 0.2
# Фильмы с малым числом оценок в выдачу не берём: по ним мало данных
# и пользователь их почти наверняка не найдёт.
MIN_RATINGS = 10


def normalize_title(title: str) -> str:
    """Название без года, артикля и знаков препинания, в нижнем регистре.

    В MovieLens артикль переносится в конец: "Matrix, The (1999)".
    """
    t = re.sub(r"[*«»\"“”_`]", " ", title.lower()).strip()
    t = re.sub(r"\(\d{4}\)\s*$", "", t)
    t = re.sub(r",\s*(the|a|an)\s*$", "", t.strip())
    t = re.sub(r"^(the|a|an)\s+", "", t)
    t = re.sub(r"\([^)]*\)", "", t)  # альтернативные названия в скобках
    return re.sub(r"[^\w]+", " ", t).strip()


@dataclass
class Movie:
    movie_id: int
    title: str
    year: int | None
    genres: list[str]

    def short(self) -> str:
        return f"{self.title} [id={self.movie_id}; {', '.join(self.genres)}]"


class Catalog:
    def __init__(self):
        data = load()
        self.data = data
        movies = data.movies.copy()
        movies["norm"] = movies["title"].map(normalize_title)
        self.movies = movies.set_index("movieId")

        stats = data.ratings.groupby("movieId")["rating"].agg(["mean", "count"])
        self.movies = self.movies.join(stats).fillna({"mean": 0.0, "count": 0})

        tags = data.tags.assign(tag=data.tags["tag"].str.lower())
        self.top_tags = tags.groupby("movieId")["tag"].agg(lambda s: s.value_counts().index[:6].tolist())

        # Учимся на всех оценках: это продакшен-режим, а не оценка качества.
        self.content = M.ContentBased().fit(data.ratings, data)
        self.itemknn = M.ItemKNN().fit(data.ratings, data)
        self.toprated = M.TopRated().fit(data.ratings, data)
        self.item_ids = self.itemknn.item_ids
        self.item_index = self.itemknn.item_index
        # Индекс для валидатора: (нормализованное название, год) -> id.
        self.by_title_year: dict[tuple[str, int], int] = {}
        self.by_title: dict[str, list[int]] = {}
        for mid, row in self.movies.iterrows():
            if not pd.isna(row["year"]):
                self.by_title_year.setdefault((row["norm"], int(row["year"])), int(mid))
            self.by_title.setdefault(row["norm"], []).append(int(mid))
        self.genres = sorted(
            {g for gs in movies["genres"] for g in gs.split("|")} - {"(no genres listed)", "IMAX"}
        )

    # --- Поиск ---------------------------------------------------------------

    def get(self, movie_id: int) -> Movie | None:
        if movie_id not in self.movies.index:
            return None
        row = self.movies.loc[movie_id]
        year = row["year"]
        return Movie(int(movie_id), row["title"], None if pd.isna(year) else int(year), row["genres"].split("|"))

    def search(
        self,
        query: str = "",
        genre: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 10,
    ) -> list[Movie]:
        df = self.movies
        if genre:
            df = df[df["genres"].str.contains(genre, case=False, regex=False)]
        if year_from:
            df = df[df["year"].fillna(0) >= year_from]
        if year_to:
            df = df[df["year"].fillna(9999) <= year_to]
        if query:
            q = normalize_title(query)
            if not q:
                return []
            exact = df[df["norm"] == q]
            contains = df[df["norm"].str.contains(q, regex=False) & (df["norm"] != q)]
            hits = pd.concat([exact, contains])
            if hits.empty:
                # Опечатки и неполные названия: нечёткое сравнение.
                ratio = df["norm"].map(lambda t: SequenceMatcher(None, q, t).ratio())
                # Колонку добавляем до фильтра: присваивание серии пустой
                # таблице в pandas раздувает её до индекса серии.
                hits = df.assign(r=ratio)
                hits = hits[hits["r"] >= 0.75].sort_values("r", ascending=False)
            else:
                # Среди совпадений выше то, что больше смотрели.
                hits = pd.concat([hits.iloc[: len(exact)], hits.iloc[len(exact) :].sort_values("count", ascending=False)])
            df = hits
        else:
            df = df.sort_values("count", ascending=False)
        return [self.get(int(i)) for i in df.index[:limit]]

    def find_by_title(self, title: str) -> Movie | None:
        """Самый вероятный фильм по названию или None, если такого нет."""
        hits = self.search(title, limit=1)
        return hits[0] if hits else None

    def details(self, movie_id: int) -> dict | None:
        m = self.get(movie_id)
        if m is None:
            return None
        row = self.movies.loc[movie_id]
        return {
            "movie_id": m.movie_id,
            "title": m.title,
            "year": m.year,
            "genres": m.genres,
            "avg_rating": round(float(row["mean"]), 2),
            "num_ratings": int(row["count"]),
            "tags": self.top_tags.get(movie_id, []),
        }

    # --- Рекомендации --------------------------------------------------------

    def similar(self, movie_id: int, k: int = 10) -> list[Movie]:
        """Похожие фильмы: смесь «смотрят вместе» и «похожи по описанию»."""
        if movie_id not in self.item_index:
            return []
        return self.recommend(liked=[movie_id], k=k)

    def recommend(
        self,
        liked: list[int],
        disliked: list[int] | None = None,
        exclude: list[int] | None = None,
        genre: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        k: int = 10,
    ) -> list[Movie]:
        """Fold-in гибрид из С1СЗ3 для пользователя, которого нет в обучении."""
        disliked = disliked or []
        liked_idx = [self.item_index[m] for m in liked if m in self.item_index]
        disliked_idx = [self.item_index[m] for m in disliked if m in self.item_index]

        pop = np.log1p(self.itemknn.popularity.astype(float))
        if liked_idx:
            knn = np.asarray(self.itemknn.S[liked_idx].sum(axis=0)).ravel()
            X = self.content.X
            profile = X[liked_idx].sum(axis=0)
            if disliked_idx:
                profile = profile - X[disliked_idx].sum(axis=0)
            profile = normalize(np.asarray(profile))
            content = np.asarray(X @ profile.T).ravel()
            scores = W_ITEMKNN * M._minmax(knn) + W_CONTENT * M._minmax(content) + W_POPULAR * M._minmax(pop)
        else:
            # Холодный старт: о вкусах ничего не знаем, берём лучшие по оценкам.
            scores = M._minmax(self.toprated.bayes) + 0.3 * M._minmax(pop)

        mask = self.movies.loc[self.item_ids, "count"].to_numpy() >= MIN_RATINGS
        if genre:
            mask &= self.movies.loc[self.item_ids, "genres"].str.contains(genre, case=False, regex=False).to_numpy()
        years = self.movies.loc[self.item_ids, "year"].astype("float").to_numpy()
        if year_from:
            mask &= np.nan_to_num(years, nan=0) >= year_from
        if year_to:
            mask &= np.nan_to_num(years, nan=9999) <= year_to
        scores = np.where(mask, scores, -np.inf)
        for m in [*liked, *disliked, *(exclude or [])]:
            if m in self.item_index:
                scores[self.item_index[m]] = -np.inf

        k = min(k, int(np.isfinite(scores).sum()))
        if k <= 0:
            return []
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [self.get(int(self.item_ids[i])) for i in top]

    def top(self, genre: str | None = None, year_from: int | None = None, year_to: int | None = None, k: int = 10):
        return self.recommend(liked=[], genre=genre, year_from=year_from, year_to=year_to, k=k)


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return Catalog()
