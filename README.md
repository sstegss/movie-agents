# Киноассистент: мультиагентная система на локальных LLM

ПрИнж, семестр 1, задание С1СЗ4. Делал один.

Ассистент советует фильмы, отвечает на вопросы о них и запоминает вкусы собеседника. Внутри несколько агентов в одном графе LangGraph. Маршрут выбирает Jev-like модель (классификатор без генерации), ответы проверяет валидатор на модели другого семейства. Рекомендации даёт RecSys из [С1СЗ3](https://github.com/sstegss/recsys-movielens). Все модели локальные, через Ollama. Агент работает в песочнице gVisor без доступа к интернету и к хосту.

Подробности:

- [docs/REPORT.md](docs/REPORT.md): отчёт с обоснованием всех решений и ответами на вопросы задания;
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): 7 диаграмм C4, граф агентов и sequence-диаграммы;
- [docs/TZ.md](docs/TZ.md): техническое задание;
- [evals/results/](evals/results/): сравнение моделей, оценка Jev-like модели, сквозные сценарии.

## Как устроено

```
сообщение -> Jev-like модель -> память -> агент (recommender | info | chat | memory)
                 |                              <-> инструменты (каталог + RecSys)
                 |                         -> валидатор (правила + gemma4:e2b)
                 |                              fail -> доработка на qwen3.5:4b -> ответ из инструментов
                 +-> атака или не про кино: отказ без LLM
```

| Роль | Модель | Когда |
|---|---|---|
| Jev-like | свой классификатор (scikit-learn) | каждый запрос, около 6 мс |
| tiny | qwen3.5:0.8b (Q8_0) | болтовня |
| cheap | qwen3.5:2b (изначально маленькая, Q8_0) | простые запросы с инструментами, извлечение вкусов |
| main | qwen3.5:4b (квантованная Q4_K_M) | сложные запросы, доработка после валидатора |
| validator | gemma4:e2b | проверка ответов |

## Запуск

Нужны Docker, gVisor (`runsc`, зарегистрированный в Docker) и Ollama на хосте со скачанными моделями. Ollama хоста нужна только для того, чтобы скачать веса: в compose работает своя Ollama, которая читает их с диска.

```bash
# модели (один раз)
for m in qwen3.5:0.8b qwen3.5:2b qwen3.5:4b gemma4:e2b; do ollama pull $m; done

# gVisor (Arch): yay -S gvisor-bin, затем в /etc/docker/daemon.json
#   "runtimes": {"runsc": {"path": "/usr/bin/runsc"}}

docker compose up -d --build
./deploy/check_isolation.sh                     # проверка песочницы
docker compose exec agent movie-chat --user lev -v
```

Если модели лежат не в `/var/lib/ollama`, укажите путь: `OLLAMA_MODELS_DIR=/path docker compose up -d`.

Интерфейсы (только на 127.0.0.1):

| Что | Адрес |
|---|---|
| Phoenix: трейсы и траектории | http://127.0.0.1:6006 |
| Grafana: дашборд «Киноассистент» | http://127.0.0.1:3000 |
| Prometheus: метрики и алерты | http://127.0.0.1:9090 |
| Alertmanager | http://127.0.0.1:9093 |

Полученные алерты пишутся в журнал: `docker compose logs alert-sink`.

### Без Docker

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.cli --local -v        # граф в этом процессе, Ollama на localhost:11434
.venv/bin/pytest                               # тесты, LLM не нужна
```

## Оценка

```bash
python -m evals.gate_eval       # Jev-like модель, секунды
python -m evals.compare_llms    # 6 моделей на задачах системы, около часа на CPU
python -m evals.e2e             # 13 сценариев через весь граф, около получаса
```

## Структура

```
agent/          SOUL.md, AGENTS.md, TOOLS.md, MEMORY.md и системные промпты агентов
skills/         навыки: recommend-movies, movie-facts, remember-taste, validate-answer
app/
  gate/         Jev-like модель: датасет, обучение, решения
  graph.py      граф LangGraph: узлы, переходы, ask()
  catalog.py    каталог MovieLens и fold-in рекомендации поверх RecSys
  tools.py      инструменты агентов
  memory.py     три слоя памяти
  validator.py  правила по каталогу и LLM-судья
  llm.py        реестр моделей и метрики вызовов
  telemetry.py  Prometheus, JSON-логи, трейсы в Phoenix
  server.py     FastAPI
  cli.py        консольный чат
recsys/         рекомендательная система из С1СЗ3 (без изменений)
evals/          датасеты, скрипты оценки и результаты
deploy/         Prometheus, алерты, Alertmanager, Grafana, проверка изоляции
docs/           отчёт, архитектура, ТЗ
tests/          pytest
```

Данные MovieLens принадлежат GroupLens Research (University of Minnesota). В репозиторий они не входят, образ скачивает их при сборке.
