# Образ агента. Запускается под gVisor (runtime: runsc), см. docker-compose.yml.
FROM python:3.12.16-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY recsys recsys
COPY app app
COPY agent agent
COPY skills skills
COPY evals evals

# Датасет и Jev-like модель готовим при сборке: у работающего контейнера
# интернета нет, а файловая система только для чтения.
RUN python -c "from recsys.data import download; download()" \
 && python -m app.gate.model

# Непривилегированный пользователь без shell-доступа к чему-либо кроме /data.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin agent \
 && mkdir /data && chown agent /data \
 && printf '#!/bin/sh\nexec python -m app.cli "$@"\n' > /usr/local/bin/movie-chat && chmod +x /usr/local/bin/movie-chat
USER agent
ENV DATA_DIR=/data

EXPOSE 8000
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
