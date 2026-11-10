"""Сквозная оценка системы на сценариях: настоящие модели, настоящий граф.

    python -m evals.e2e                 -> evals/results/e2e.md

Метрики по категориям:
- продуктовые: задача решена (проверки сценария), вкусы учтены;
- агентные: верный маршрут, доля ответов с доработкой, заглушки, шаги;
- LLM: выдумки в первом черновике и в итоговом ответе, оценка судьи
  (LLM-as-a-judge на третьем семействе моделей, Llama);
- инфраструктурные: время ответа, токены, условная стоимость и экономия
  от роутинга по сравнению с «всё на основной модели».
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results" / "e2e.md"
JUDGE_MODEL = "llama3.2:3b"

# Отдельная память под прогон, чтобы не смешивать с рабочей.
_tmp = tempfile.mkdtemp(prefix="movie-agents-e2e-")
os.environ["DATA_DIR"] = _tmp

from langchain_core.messages import HumanMessage  # noqa: E402
from prometheus_client import REGISTRY  # noqa: E402

from app.catalog import get_catalog  # noqa: E402
from app.graph import ask  # noqa: E402
from app.llm import COST_PER_1K, get_llm  # noqa: E402
from app.memory import LongTermMemory  # noqa: E402
from app.validator import extract_mentions  # noqa: E402


def tokens_by_model() -> dict[str, float]:
    out = {}
    for metric in REGISTRY.collect():
        if metric.name == "llm_tokens":
            for s in metric.samples:
                if s.name == "llm_tokens_total":
                    out[s.labels["model"]] = out.get(s.labels["model"], 0) + s.value
    return out


def judge(query: str, answer: str) -> int | None:
    llm = get_llm("main", "judge", json_mode=True, model=JUDGE_MODEL)
    prompt = (
        "Оцени ответ киноассистента по шкале от 1 до 5: насколько он полезен и отвечает на запрос. "
        'Верни JSON {"score": число, "reason": "коротко"}.\n\n'
        f"Запрос: {query}\n\nОтвет: {answer}"
    )
    try:
        return int(json.loads(llm.invoke([HumanMessage(prompt)]).content)["score"])
    except Exception:
        return None


def run_checks(sc: dict, r: dict, catalog) -> list[str]:
    c, fails = sc["checks"], []
    mentions = [m for m in extract_mentions(r["answer"], catalog)]
    movies = [catalog.get(m.movie_id) for m in mentions if m.movie_id]
    profile = LongTermMemory().load(sc["user"])
    if c.get("blocked") and r["model"] is not None:
        fails.append("запрос дошёл до LLM")
    if "remembered_min" in c and len(r["remembered"]) < c["remembered_min"]:
        fails.append(f"запомнено {len(r['remembered'])} фактов из {c['remembered_min']}")
    if "min_movies" in c and len(movies) < c["min_movies"]:
        fails.append(f"фильмов в ответе {len(movies)}")
    if "genre" in c and any(c["genre"] not in m.genres for m in movies):
        fails.append(f"не все фильмы жанра {c['genre']}")
    if "no_genre" in c and any(c["no_genre"] in m.genres for m in movies):
        fails.append(f"есть жанр {c['no_genre']}")
    if "year_to" in c and any((m.year or 0) > c["year_to"] for m in movies):
        fails.append(f"есть фильмы новее {c['year_to']}")
    if "title_not_contains" in c and any(c["title_not_contains"] in m.title for m in movies):
        fails.append(f"есть {c['title_not_contains']}")
    if "not_movie" in c:
        ref = catalog.find_by_title(c["not_movie"]).movie_id
        if any(m.movie_id == ref for m in movies):
            fails.append("посоветован сам образец")
    if c.get("exclude_seen") and set(profile.seen_movies()) & {m.movie_id for m in movies}:
        fails.append("посоветовано уже просмотренное")
    if "answer_contains" in c and c["answer_contains"] not in r["answer"]:
        fails.append(f"нет «{c['answer_contains']}» в ответе")
    if "answer_contains_any" in c and not any(s in r["answer"] for s in c["answer_contains_any"]):
        fails.append("нет ожидаемого факта в ответе")
    if any(m.movie_id is None for m in mentions):
        fails.append("в итоговом ответе выдуманный фильм")
    return fails


def main():
    catalog = get_catalog()
    scenarios = [json.loads(l) for l in (ROOT / "datasets" / "scenarios.jsonl").read_text().splitlines() if l.strip()]
    rows = []
    for sc in scenarios:
        before = tokens_by_model()
        r = ask(sc["user"], sc["query"])
        after = tokens_by_model()
        used = {m: after.get(m, 0) - before.get(m, 0) for m in after if after.get(m, 0) - before.get(m, 0) > 0}
        fails = run_checks(sc, r, catalog)
        score = judge(sc["query"], r["answer"]) if r["model"] else None
        row = {
            "id": sc["id"], "intent_ok": r["gate"].get("intent") == sc["intent"], "fails": fails,
            "model": r["model"], "seconds": r["seconds"], "revisions": r["revisions"],
            "first_draft_failed": "validate:fail" in r["trajectory"],
            "fallback": "fallback" in r["trajectory"],
            "tool_steps": sum(t.startswith("tools:") for t in r["trajectory"]),
            "tokens": used, "judge": score, "answer": r["answer"], "trajectory": r["trajectory"],
        }
        rows.append(row)
        print(json.dumps({k: row[k] for k in ("id", "intent_ok", "fails", "model", "seconds", "revisions", "judge")}, ensure_ascii=False), flush=True)

    n = len(rows)
    llm_rows = [r for r in rows if r["model"]]
    tool_rows = [r for r in rows if r["tool_steps"] or r["first_draft_failed"]]
    secs = sorted(r["seconds"] for r in llm_rows)
    cost = sum(t / 1000 * COST_PER_1K.get(m, 4) for r in rows for m, t in r["tokens"].items())
    # Сколько стоило бы, если бы все генерирующие вызовы шли на основную модель.
    # Токены валидатора не трогаем: он нужен в обоих вариантах.
    cost_all_main = sum(t / 1000 * (COST_PER_1K.get(m, 4) if m.startswith("gemma") else COST_PER_1K["qwen3.5:4b"])
                        for r in rows for m, t in r["tokens"].items())
    judged = [r["judge"] for r in rows if r["judge"]]

    lines = [
        "# Сквозная оценка (e2e)",
        "",
        f"Сценариев: {n}. Настоящие модели через Ollama на CPU, граф целиком, отдельная память под прогон. "
        f"Судья: {JUDGE_MODEL} (третье семейство моделей, не участвует в ответах).",
        "",
        "## Сводка",
        "",
        "| Категория | Метрика | Значение |",
        "|---|---|---|",
        f"| продукт | задача решена (все проверки сценария) | {sum(not r['fails'] for r in rows)}/{n} |",
        f"| продукт | средняя оценка судьи (1-5) | {sum(judged) / max(len(judged), 1):.2f} по {len(judged)} ответам |",
        f"| агенты | верный маршрут Jev-like модели | {sum(r['intent_ok'] for r in rows)}/{n} |",
        f"| агенты | первый черновик отклонён валидатором | {sum(r['first_draft_failed'] for r in tool_rows)}/{len(tool_rows)} ответов с инструментами |",
        f"| агенты | ответ-заглушка после неудачной доработки | {sum(r['fallback'] for r in rows)}/{n} |",
        f"| агенты | вызовов инструментов на ответ (среднее) | {sum(r['tool_steps'] for r in tool_rows) / max(len(tool_rows), 1):.1f} |",
        f"| LLM | выдуманные фильмы в итоговых ответах | {sum('в итоговом ответе выдуманный фильм' in r['fails'] for r in rows)} |",
        f"| инфра | время ответа p50 / max (с LLM) | {secs[len(secs) // 2] if secs else 0:.0f} с / {secs[-1] if secs else 0:.0f} с |",
        f"| инфра | заблокировано без LLM (0 токенов) | {n - len(llm_rows)}/{n} |",
        f"| инфра | условная стоимость прогона | {cost:.1f} (если всё на qwen3.5:4b: {cost_all_main:.1f}, экономия {1 - cost / max(cost_all_main, 1e-9):.0%}) |",
        "",
        "## По сценариям",
        "",
        "| Сценарий | Маршрут | Модель | Доработки | Шаги инстр. | Время, с | Судья | Проблемы |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['id']} | {'ok' if r['intent_ok'] else 'ошибка'} | {r['model'] or 'без LLM'} | {r['revisions']} | "
                     f"{r['tool_steps']} | {r['seconds']:.0f} | {r['judge'] or '-'} | {'; '.join(r['fails']) or '-'} |")
    lines += ["", "## Ответы", ""]
    for r in rows:
        lines += [f"**{r['id']}** ({' -> '.join(r['trajectory'])})", "", "> " + r["answer"].replace("\n", "\n> "), ""]
    OUT.write_text("\n".join(lines) + "\n")
    (OUT.with_suffix(".json")).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(OUT.read_text())
    shutil.rmtree(_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
