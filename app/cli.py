"""Консольный чат с ассистентом.

    python -m app.cli                 # через HTTP-сервис (по умолчанию localhost:8000)
    python -m app.cli --local         # граф прямо в этом процессе, без сервиса
    python -m app.cli --user lev -v   # -v: показать маршрут, модель и проверку

Команды в чате: /good и /bad оценивают последний ответ, /exit выходит.
"""

from __future__ import annotations

import argparse
import os

import httpx


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--user", default=os.environ.get("USER_ID", "guest"))
    p.add_argument("--session", default="default")
    p.add_argument("--url", default=os.environ.get("AGENT_URL", "http://127.0.0.1:8000"))
    p.add_argument("--local", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    if args.local:
        from app.graph import ask

        def send(text):
            return ask(args.user, text, args.session)
    else:
        client = httpx.Client(base_url=args.url, timeout=900)

        def send(text):
            r = client.post("/chat", json={"user_id": args.user, "message": text, "session": args.session})
            r.raise_for_status()
            return r.json()

    print("Киноассистент. Ответ на CPU может идти пару минут. /good, /bad, /exit")
    last_id = None
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/exit":
            break
        if text in ("/good", "/bad"):
            if last_id and not args.local:
                client.post("/feedback", json={"request_id": last_id, "value": 1 if text == "/good" else -1})
                print("Спасибо, оценка записана.")
            continue
        try:
            r = send(text)
        except Exception as e:
            print(f"Ошибка: {e}")
            continue
        last_id = r["request_id"]
        print("\n" + r["answer"])
        if args.verbose:
            v = r.get("validation") or {}
            print(f"\n  [{r['gate'].get('intent')} -> {r.get('model') or 'без LLM'} | "
                  f"проверка: {v.get('stage', '-')}/{'ok' if v.get('passed', True) else 'fail'} | "
                  f"доработок: {r['revisions']} | {r['seconds']} с]")
            print("  путь: " + " -> ".join(r["trajectory"]))


if __name__ == "__main__":
    main()
