"""Генерирует дашборд Grafana. Запуск: python deploy/grafana/make_dashboard.py"""

import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "prom"}

# (заголовок, [(запрос, подпись)], тип, единицы, ширина)
ROWS = [
    ("Продукт", [
        ("Запросы в минуту по намерениям", [("sum by (intent) (rate(agent_requests_total[5m])) * 60", "{{intent}}")], "timeseries", "short", 12),
        ("Доля хороших оценок (/good)", [("sum(user_feedback_total{value=\"good\"}) / clamp_min(sum(user_feedback_total), 1)", "good")], "stat", "percentunit", 6),
        ("Ответы-заглушки (агент не справился)", [("sum(increase(agent_errors_total{stage=\"validation_exhausted\"}[1h]))", "fallback/час")], "stat", "short", 6),
    ]),
    ("Агенты и роутинг", [
        ("Решения Jev-like модели по моделям", [("sum by (tier) (increase(gate_decisions_total[1h]))", "{{tier}}")], "piechart", "short", 8),
        ("Заблокировано до LLM", [("sum by (intent) (increase(gate_decisions_total{blocked=\"True\"}[1h]))", "{{intent}}")], "timeseries", "short", 8),
        ("Эскалации и доработки", [("sum(increase(agent_escalations_total[1h]))", "эскалации"), ("sum(increase(agent_revisions_total[1h]))", "доработки")], "timeseries", "short", 8),
        ("Вызовы инструментов", [("sum by (tool, status) (increase(tool_calls_total[1h]))", "{{tool}} {{status}}")], "timeseries", "short", 12),
        ("Вердикты валидатора", [("sum by (check, verdict) (increase(validator_verdicts_total[1h]))", "{{check}}: {{verdict}}")], "timeseries", "short", 12),
    ]),
    ("LLM", [
        ("p50 / p95 вызова LLM по моделям", [
            ("histogram_quantile(0.5, sum by (le, model) (rate(llm_latency_seconds_bucket[15m])))", "p50 {{model}}"),
            ("histogram_quantile(0.95, sum by (le, model) (rate(llm_latency_seconds_bucket[15m])))", "p95 {{model}}")], "timeseries", "s", 12),
        ("Токены по моделям", [("sum by (model, direction) (rate(llm_tokens_total[5m])) * 60", "{{model}} {{direction}}")], "timeseries", "short", 12),
        ("Условная стоимость по моделям", [("sum by (model) (increase(llm_cost_units_total[1h]))", "{{model}}")], "timeseries", "short", 12),
        ("Выдуманные фильмы (пойманы валидатором)", [("sum(increase(hallucinated_titles_total[1h]))", "за час")], "stat", "short", 6),
        ("Ошибки LLM", [("sum by (model) (increase(llm_calls_total{status=\"error\"}[1h]))", "{{model}}")], "stat", "short", 6),
    ]),
    ("Инфраструктура", [
        ("p50 / p95 ответа целиком", [
            ("histogram_quantile(0.5, sum by (le) (rate(agent_request_latency_seconds_bucket[15m])))", "p50"),
            ("histogram_quantile(0.95, sum by (le) (rate(agent_request_latency_seconds_bucket[15m])))", "p95")], "timeseries", "s", 12),
        ("Агент жив", [("up{job=\"agent\"}", "agent")], "stat", "short", 4),
        ("Ollama жива", [("ollama_up", "ollama")], "stat", "short", 4),
        ("Время решения Jev-like, p95", [("histogram_quantile(0.95, sum by (le) (rate(gate_latency_seconds_bucket[15m])))", "p95")], "stat", "s", 4),
        ("Записи в память", [("sum by (kind) (increase(memory_writes_total[1h]))", "{{kind}}")], "timeseries", "short", 12),
        ("Ошибки по этапам", [("sum by (stage) (increase(agent_errors_total[1h]))", "{{stage}}")], "timeseries", "short", 12),
    ]),
]


def build():
    panels, y, pid = [], 0, 1
    for title, items in ROWS:
        panels.append({"type": "row", "title": title, "id": pid, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "collapsed": False})
        pid += 1
        y += 1
        x = 0
        for name, queries, kind, unit, w in items:
            if x + w > 24:
                x, y = 0, y + 8
            panels.append({
                "id": pid, "type": kind, "title": name, "datasource": DS,
                "gridPos": {"x": x, "y": y, "w": w, "h": 8},
                "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
                "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "reduceOptions": {"calcs": ["lastNotNull"]}},
                "targets": [{"refId": chr(65 + i), "expr": q, "legendFormat": leg, "datasource": DS} for i, (q, leg) in enumerate(queries)],
            })
            pid += 1
            x += w
        y += 8
    return {
        "uid": "movie-agents", "title": "Киноассистент", "schemaVersion": 39, "version": 1,
        "refresh": "30s", "time": {"from": "now-6h", "to": "now"}, "panels": panels, "tags": ["agents"],
    }


out = Path(__file__).with_name("dashboards") / "movie-agents.json"
out.write_text(json.dumps(build(), ensure_ascii=False, indent=1))
print(out)
