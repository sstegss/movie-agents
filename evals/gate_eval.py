"""Оценка Jev-like модели на отложенном наборе. Без LLM, секунды.

    python -m evals.gate_eval   -> evals/results/gate.md
"""

import time
from collections import Counter
from pathlib import Path

from sklearn.metrics import classification_report, confusion_matrix

from app.gate.model import INTENTS, get_gate, load_rows

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results" / "gate.md"


def main():
    gate = get_gate()
    rows = load_rows(ROOT / "datasets" / "gate_test.jsonl")
    y_true, y_pred, cx_true, cx_pred, pref_ok, tiers, times = [], [], [], [], 0, Counter(), []
    blocked_ok = blocked_total = 0
    for r in rows:
        t = time.perf_counter()
        d = gate.decide(r["text"])
        times.append(time.perf_counter() - t)
        y_true.append(r["intent"])
        y_pred.append(d.intent)
        cx_true.append(r["complexity"])
        cx_pred.append(d.complexity)
        pref_ok += d.has_preference == bool(r["pref"])
        tiers[d.model_tier] += 1
        if r["intent"] in ("unsafe", "off_topic"):
            blocked_total += 1
            blocked_ok += d.blocked

    n = len(rows)
    acc = sum(a == b for a, b in zip(y_true, y_pred)) / n
    cx_acc = sum(a == b for a, b in zip(cx_true, cx_pred)) / n
    times.sort()
    rep = classification_report(y_true, y_pred, labels=INTENTS, zero_division=0, output_dict=True)
    cm = confusion_matrix(y_true, y_pred, labels=INTENTS)

    lines = [
        "# Jev-like модель: оценка на отложенном наборе",
        "",
        f"Примеров: {n} (обучающих: {len(load_rows(Path('app/gate/train.jsonl')))}). Набор написан отдельно от обучающего, пересечений нет (проверяет тест).",
        "",
        "| Метрика | Значение |",
        "|---|---|",
        f"| Accuracy по намерению | {acc:.3f} |",
        f"| Macro-F1 по намерению | {rep['macro avg']['f1-score']:.3f} |",
        f"| Accuracy по сложности | {cx_acc:.3f} |",
        f"| Accuracy «есть вкусы» | {pref_ok / n:.3f} |",
        f"| Доля опасных и посторонних запросов, заблокированных до LLM | {blocked_ok}/{blocked_total} |",
        f"| Время решения, медиана / p95 | {times[n // 2] * 1000:.1f} мс / {times[int(n * 0.95)] * 1000:.1f} мс |",
        "",
        "Распределение по моделям: " + ", ".join(f"{k}: {v}" for k, v in tiers.most_common()),
        "",
        "## По классам",
        "",
        "| Намерение | Precision | Recall | F1 | N |",
        "|---|---|---|---|---|",
    ]
    for c in INTENTS:
        m = rep[c]
        lines.append(f"| {c} | {m['precision']:.2f} | {m['recall']:.2f} | {m['f1-score']:.2f} | {int(m['support'])} |")
    lines += ["", "## Матрица ошибок (строки: правда, столбцы: предсказание)", "",
              "| | " + " | ".join(INTENTS) + " |", "|---" * (len(INTENTS) + 1) + "|"]
    for c, row in zip(INTENTS, cm):
        lines.append(f"| {c} | " + " | ".join(str(v) for v in row) + " |")
    lines += ["", "## Ошибки", ""]
    for r, p in zip(rows, y_pred):
        if r["intent"] != p:
            lines.append(f"- «{r['text']}»: правда {r['intent']}, модель {p}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
