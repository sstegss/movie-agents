"""Jev-like модель: принимает решения по запросу, но не пишет текст.

Идея взята у Jev от TypeSafe AI: решение о маршруте принимает быстрая
модель-классификатор, а не LLM. LLM для этого дорогая и медленная (на CPU
это секунды против миллисекунд) и может ответить не по формату.

Три головы на общих признаках (TF-IDF по символам и словам + длина):
- intent: что хочет пользователь;
- complexity: простой или сложный запрос, от этого зависит выбор LLM;
- pref: есть ли в сообщении вкусы, которые надо записать в память.

Поверх классификатора стоят правила для явных атак (prompt injection,
доступ к файлам). Классификатор можно обмануть перефразированием,
регулярное выражение на «системный промпт» обмануть труднее. Поэтому одно
дополняет другое.
"""

from __future__ import annotations

import json
import pickle
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import FunctionTransformer

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "gate.pkl"
TRAIN_PATH = HERE / "train.jsonl"

INTENTS = ["recommend", "movie_info", "preference", "chitchat", "off_topic", "unsafe"]

# Ниже этой уверенности по intent решение считается сомнительным,
# и запрос уходит основной модели, а не дешёвой.
CONFIDENCE_THRESHOLD = 0.45

INJECTION_PATTERNS = re.compile(
    r"(игнорир\w*|забудь)\s+(все\s+)?(предыдущ\w*\s+)?(инструкц|правил)"
    r"|ignore\s+(all\s+)?(previous\s+)?(instructions|rules)"
    r"|(системн\w*|system)\s*(промпт|prompt)"
    r"|developer\s+mode|\bDAN\b"
    r"|/etc/|\.env\b|rm\s+-rf|переменн\w+\s+окружени|environment\s+variables"
    r"|друг\w*\s+пользовател|other\s+users",
    re.IGNORECASE,
)


def _length_features(texts):
    rows = []
    for t in texts:
        words = t.split()
        rows.append(
            [
                min(len(words), 40) / 40,
                t.count(",") / 5,
                len(re.findall(r"\b(но|и|а|без|кроме|не|but|and|without|not)\b", t.lower())) / 5,
            ]
        )
    return np.array(rows)


def _features():
    return FeatureUnion(
        [
            ("chars", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True, min_df=1)),
            ("words", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True)),
            ("length", FunctionTransformer(_length_features)),
        ]
    )


def _head():
    return Pipeline([("features", _features()), ("clf", LogisticRegression(C=8.0, max_iter=2000, class_weight="balanced"))])


@dataclass
class GateDecision:
    intent: str
    confidence: float
    complexity: str
    has_preference: bool
    blocked: bool
    model_tier: str  # none | tiny | cheap | main
    needs_validation: bool
    reason: str

    def as_dict(self):
        return asdict(self)


class Gate:
    def __init__(self, heads: dict[str, Pipeline]):
        self.heads = heads

    @classmethod
    def train(cls, rows: list[dict]) -> "Gate":
        texts = [r["text"] for r in rows]
        heads = {}
        for name in ("intent", "complexity", "pref"):
            heads[name] = _head().fit(texts, [str(r[name]) for r in rows])
        return cls(heads)

    def save(self, path: Path = MODEL_PATH):
        with open(path, "wb") as f:
            pickle.dump(self.heads, f)

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "Gate":
        with open(path, "rb") as f:
            return cls(pickle.load(f))

    def predict_raw(self, text: str) -> dict:
        out = {}
        for name, head in self.heads.items():
            proba = head.predict_proba([text])[0]
            i = int(np.argmax(proba))
            out[name] = (head.classes_[i], float(proba[i]))
        return out

    def decide(self, text: str) -> GateDecision:
        raw = self.predict_raw(text)
        intent, conf = raw["intent"]
        complexity = raw["complexity"][0]
        has_pref = raw["pref"][0] == "1"

        if INJECTION_PATTERNS.search(text):
            intent, conf, reason = "unsafe", 1.0, "правило: похоже на prompt injection или доступ к системе"
        else:
            reason = f"классификатор: {intent} ({conf:.2f}), {complexity}"

        if intent in ("unsafe", "off_topic"):
            return GateDecision(intent, conf, complexity, False, True, "none", False, reason)

        if conf < CONFIDENCE_THRESHOLD:
            # Не уверены, что понимаем запрос: отдаём сильной модели.
            tier, reason = "main", reason + "; низкая уверенность, эскалация на основную модель"
        elif intent == "chitchat":
            tier = "tiny"
        elif intent == "preference":
            tier = "cheap"  # агент памяти работает на 2b
        else:
            tier = "main" if complexity == "complex" else "cheap"

        needs_validation = intent in ("recommend", "movie_info")
        return GateDecision(intent, conf, complexity, has_pref or intent == "preference", False, tier, needs_validation, reason)


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


_gate: Gate | None = None


def get_gate() -> Gate:
    global _gate
    if _gate is None:
        if not MODEL_PATH.exists():
            train_and_save()
        _gate = Gate.load()
    return _gate


def train_and_save() -> Gate:
    if not TRAIN_PATH.exists():
        from app.gate.build_dataset import main as build

        build()
    gate = Gate.train(load_rows(TRAIN_PATH))
    gate.save()
    return gate


if __name__ == "__main__":
    # Импорт по полному имени, иначе pickle запомнит функции из __main__
    # и модель не загрузится из другого модуля.
    from app.gate.model import train_and_save as _train

    _train()
    print(f"Модель сохранена в {MODEL_PATH}")
