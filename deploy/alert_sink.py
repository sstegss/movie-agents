"""Приёмник алертов от Alertmanager: печатает их и дописывает в /alerts/alerts.jsonl.

В реальной системе здесь был бы Telegram, почта или PagerDuty. Для учебного
стенда хватает файла: видно, что алерт дошёл, и его можно прочитать.
"""

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        with open("/alerts/alerts.jsonl", "a") as f:
            for a in body.get("alerts", []):
                rec = {"received": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": a.get("status"),
                       "alert": a.get("labels", {}).get("alertname"), "summary": a.get("annotations", {}).get("summary")}
                print(json.dumps(rec, ensure_ascii=False), flush=True)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
