"""Сравнение LLM на задачах нашей системы, а не на общих бенчмарках.

    python -m evals.compare_llms                 -> evals/results/llm_comparison.md
    python -m evals.compare_llms --models qwen3.5:2b gemma4:e2b

Задачи:
1. tools: выбрать правильный инструмент и аргументы (нужно recommender/info);
2. memory: извлечь вкусы в JSON (нужно агенту памяти);
3. grounding: признать, что выдуманного фильма нет (галлюцинации);
4. judge: отличить хороший ответ от ошибочного (нужно валидатору);
5. скорость: загрузка, обработка промпта и генерация, размер в памяти.

Модели запускаются по очереди и выгружаются после своих задач, чтобы на
16 ГБ не держать в памяти две сразу.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx
from langchain_core.messages import HumanMessage, SystemMessage

from app.catalog import get_catalog
from app.config import settings
from app.llm import get_llm
from app.memory import Profile
from app.skills import skill_for_agent, system_prompt
from app.tools import ToolLog, build_tools

OUT = Path(__file__).resolve().parent / "results" / "llm_comparison.md"
# Параметры и квантование из `ollama show`.
INFO = {
    "qwen3.5:0.8b": ("Qwen (Alibaba)", "0.75B", "Q8_0"),
    "qwen3.5:2b": ("Qwen (Alibaba)", "1.9B", "Q8_0"),
    "qwen3.5:4b": ("Qwen (Alibaba)", "4.2B", "Q4_K_M"),
    "gemma4:e2b": ("Gemma (Google)", "4.6B, эффективных ~2B", "Q4_K_M"),
    "gemma4:e4b": ("Gemma (Google)", "7.5B, эффективных ~4B", "Q4_K_M"),
    "llama3.2:3b": ("Llama (Meta)", "3.2B", "Q4_K_M"),
}
MODELS = ["qwen3.5:0.8b", "qwen3.5:2b", "qwen3.5:4b", "gemma4:e2b", "gemma4:e4b", "llama3.2:3b"]

# (агент, запрос, допустимые первые инструменты, проверка аргументов)
TOOL_TASKS = [
    ("recommender", "посоветуй комедию", {"recommend_for_user", "top_movies", "search_movies"},
     lambda a: str(a.get("genre", "")).lower() == "comedy"),
    ("recommender", "что-то похожее на Матрицу", {"search_movies"}, lambda a: "matrix" in str(a.get("query", "")).lower()),
    ("recommender", "лучшие фильмы ужасов 80-х", {"top_movies", "recommend_for_user", "search_movies"},
     lambda a: str(a.get("genre", "")).lower() == "horror"),
    ("info", "какой рейтинг у Титаника?", {"search_movies"}, lambda a: "titanic" in str(a.get("query", "")).lower()),
    ("info", "в каком году вышел Бойцовский клуб?", {"search_movies"}, lambda a: "fight club" in str(a.get("query", "")).lower()),
]

# (сообщение, ожидаемые факты (kind, ключ, sentiment))
MEMORY_TASKS = [
    ("обожаю Интерстеллар, а ужасы терпеть не могу", {("movie", "Interstellar", "like"), ("genre", "Horror", "dislike")}),
    ("я уже смотрел Начало и Матрицу", {("movie", "Inception", "seen"), ("movie", "Matrix", "seen")}),
    ("люблю комедии и фантастику", {("genre", "Comedy", "like"), ("genre", "Sci-Fi", "like")}),
    ("Титаник не понравился", {("movie", "Titanic", "dislike")}),
    ("я фанат Кристофера Нолана", {("person", "christopher nolan", "like")}),
]

# (название, год, есть ли в каталоге): спрашиваем модель без инструментов.
GROUNDING_TASKS = [
    ("Inception", 2010, True),
    ("Songs of the Night", 2015, False),  # такого фильма у Нолана нет
    ("The Matrix", 1999, True),
    ("Interstellar 2: Return", 2019, False),
    ("Pulp Fiction", 1994, True),
    ("The Dark Forest", 2016, False),
]

JUDGE_TASKS = [
    ("посоветуй комедию", "1. Hangover, The (2009): комедия про мальчишник\n2. Superbad (2007): школьная комедия", "pass"),
    ("посоветуй комедию", "1. Shining, The (1980): ужасы\n2. Alien (1979): ужасы и фантастика", "fail"),
    ("что-то как Интерстеллар", "1. Martian, The (2015): фантастика о выживании в космосе", "pass"),
    ("мультфильм для ребёнка", "1. Saw (2004): кровавый триллер", "fail"),
]


def _norm_fact(kind, name, sentiment, catalog):
    if kind == "movie":
        m = catalog.find_by_title(name)
        name = m.title if m else "?" + name
    elif kind == "genre":
        name = next((g for g in catalog.genres if g.lower() == name.lower()), "?" + name)
    else:
        name = name.lower()
    return kind, name, sentiment


def _ollama_stats(meta: dict) -> dict:
    ns = 1e9
    return {
        "load": meta.get("load_duration", 0) / ns,
        "prompt_tokens": meta.get("prompt_eval_count", 0),
        "prompt_s": meta.get("prompt_eval_duration", 0) / ns,
        "gen_tokens": meta.get("eval_count", 0),
        "gen_s": meta.get("eval_duration", 0) / ns,
    }


def unload(model):
    httpx.post(f"{settings.ollama_url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)


def loaded_size_gb(model) -> float:
    for m in httpx.get(f"{settings.ollama_url}/api/ps", timeout=10).json().get("models", []):
        if m["name"] == model:
            return m.get("size", 0) / 1e9
    return 0.0


def evaluate(model: str, catalog) -> dict:
    stats = []
    res = {"model": model}

    def call(msgs, agent, json_mode=False, tools=None):
        get_llm.cache_clear()
        llm = get_llm("main", f"compare-{agent}", json_mode=json_mode, model=model)
        if tools:
            llm = llm.bind_tools(tools)
        t = time.perf_counter()
        try:
            r = llm.invoke(msgs)
        except Exception as e:
            stats.append({"error": str(e)[:80], "wall": time.perf_counter() - t})
            return None
        s = _ollama_stats(r.response_metadata)
        s["wall"] = time.perf_counter() - t
        stats.append(s)
        return r

    # Холодный старт отдельно: первый вызов включает загрузку весов.
    unload(model)
    first = call([HumanMessage("Скажи «готов».")], "warmup")
    res["load_s"] = _ollama_stats(first.response_metadata)["load"] if first is not None else None
    res["ram_gb"] = loaded_size_gb(model)

    tools = build_tools(catalog, Profile([], []), ToolLog())
    ok = args_ok = 0
    for agent, q, allowed, check in TOOL_TASKS:
        skill = skill_for_agent(agent)
        r = call([SystemMessage(system_prompt(agent)), HumanMessage(q)], agent, tools=[tools[n] for n in skill.tools])
        tc = (r.tool_calls if r is not None else []) or []
        if tc and tc[0]["name"] in allowed:
            ok += 1
            args_ok += bool(check(tc[0]["args"]))
    res["tools"] = ok / len(TOOL_TASKS)
    res["tool_args"] = args_ok / len(TOOL_TASKS)

    valid = 0
    tp = fp = fn = 0
    for msg, gold in MEMORY_TASKS:
        r = call([SystemMessage(system_prompt("memory")), HumanMessage(msg)], "memory", json_mode=True)
        try:
            facts = json.loads(r.content)["facts"]
            valid += 1
        except Exception:
            facts = []
        got = {_norm_fact(f.get("kind"), str(f.get("name", "")), f.get("sentiment"), catalog) for f in facts if isinstance(f, dict)}
        want = {_norm_fact(k, n, s, catalog) for k, n, s in gold}
        tp, fp, fn = tp + len(got & want), fp + len(got - want), fn + len(want - got)
    res["json_valid"] = valid / len(MEMORY_TASKS)
    res["memory_f1"] = 2 * tp / max(2 * tp + fp + fn, 1)

    correct = 0
    for title, year, exists in GROUNDING_TASKS:
        q = (f"Существует ли фильм «{title}» ({year})? Если не уверен, отвечай false. "
             'Ответь только JSON: {"exists": true или false}')
        r = call([HumanMessage(q)], "grounding", json_mode=True)
        try:
            correct += bool(json.loads(r.content).get("exists")) == exists
        except Exception:
            pass
    res["grounding"] = correct / len(GROUNDING_TASKS)

    correct = 0
    for q, answer, verdict in JUDGE_TASKS:
        prompt = f"Запрос пользователя:\n{q}\n\nРезультаты инструментов:\nвсе фильмы из ответа найдены в каталоге\n\nОтвет агента:\n{answer}"
        r = call([SystemMessage(system_prompt("validator")), HumanMessage(prompt)], "judge", json_mode=True)
        try:
            correct += str(json.loads(r.content).get("verdict", "")).lower() == verdict
        except Exception:
            pass
    res["judge"] = correct / len(JUDGE_TASKS)

    good = [s for s in stats[1:] if "error" not in s]
    res["errors"] = sum("error" in s for s in stats)
    res["prompt_tps"] = sum(s["prompt_tokens"] for s in good) / max(sum(s["prompt_s"] for s in good), 1e-9)
    res["gen_tps"] = sum(s["gen_tokens"] for s in good) / max(sum(s["gen_s"] for s in good), 1e-9)
    walls = sorted(s["wall"] for s in good) or [0]
    res["median_call_s"] = walls[len(walls) // 2]
    unload(model)
    return res


def render(rows: list[dict]) -> str:
    head = ("| Модель | Инструмент | Аргументы | JSON | Память F1 | Без выдумок | Судья | Загрузка, с | "
            "RAM, ГБ | Промпт, ток/с | Генерация, ток/с | Медиана вызова, с |")
    lines = ["# Сравнение LLM на задачах киноассистента", "",
             "Железо: Intel i3-7100 (2 ядра), 16 ГБ RAM, без GPU (Radeon RX 580 не поддерживается ROCm). "
             "Ollama 0.40.7, модели из библиотеки Ollama.", "",
             "| Модель | Семейство | Параметры | Квантование |", "|---|---|---|---|",
             *[f"| {r['model']} | {' | '.join(INFO.get(r['model'], ('?', '?', '?')))} |" for r in rows], "",
             head, "|" + "---|" * 12]
    for r in rows:
        lines.append(
            f"| {r['model']} | {r['tools']:.0%} | {r['tool_args']:.0%} | {r['json_valid']:.0%} | {r['memory_f1']:.2f} | "
            f"{r['grounding']:.0%} | {r['judge']:.0%} | {r['load_s'] or 0:.1f} | {r['ram_gb']:.1f} | "
            f"{r['prompt_tps']:.0f} | {r['gen_tps']:.1f} | {r['median_call_s']:.1f} |"
        )
    lines += ["", "Столбцы:", "",
              f"- **Инструмент**: доля из {len(TOOL_TASKS)} запросов, где первым вызван подходящий инструмент;",
              "- **Аргументы**: из них ещё и аргументы правильные (жанр, английское название);",
              f"- **JSON / Память F1**: агент памяти, {len(MEMORY_TASKS)} сообщений, F1 по фактам после сверки с каталогом;",
              f"- **Без выдумок**: из {len(GROUNDING_TASKS)} вопросов «существует ли фильм» без инструментов, половина фильмов выдумана;",
              f"- **Судья**: из {len(JUDGE_TASKS)} пар верно ли поставлен pass/fail;",
              "- скорость взята из счётчиков Ollama, RAM из `ollama ps` после загрузки. Для Gemma 4 это число меньше "
              "размера на диске: судя по всему, `ollama ps` учитывает не все веса (часть эмбеддингов по слоям), "
              "поэтому сравнивать RAM между семействами по этому столбцу не стоит.",
              "",
              "Задач в каждой группе от 4 до 6, поэтому разница в одну задачу это 17–25 процентных пунктов. "
              "Таблица годится, чтобы отсеять явно неподходящие модели, а не чтобы различать близкие."]
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--models", nargs="*", default=MODELS)
    p.add_argument("--render-only", action="store_true", help="пересобрать таблицу из сохранённого JSON")
    args = p.parse_args()
    if args.render_only:
        OUT.write_text(render(json.loads(OUT.with_suffix(".json").read_text())))
        print(OUT.read_text())
        return
    catalog = get_catalog()
    rows = []
    for m in args.models:
        print(f"== {m}", flush=True)
        rows.append(evaluate(m, catalog))
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
        (OUT.with_suffix(".json")).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        OUT.write_text(render(rows))
    print(OUT.read_text())


if __name__ == "__main__":
    main()
