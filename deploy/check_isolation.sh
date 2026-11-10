#!/usr/bin/env bash
# Проверка изоляции контейнера агента. Запуск: ./deploy/check_isolation.sh
# Каждая проверка печатает OK или FAIL. Код выхода 1, если хоть одна не прошла.

set -u
fail=0
run() { docker compose exec -T agent python -c "$1" 2>&1; }
check() {  # $1 описание, $2 команда python, $3 ожидаемая подстрока
  out=$(run "$2")
  if grep -q -- "$3" <<<"$out"; then echo "OK    $1"; else echo "FAIL  $1: $out"; fail=1; fi
}

echo "Runtime контейнера: $(docker inspect -f '{{.HostConfig.Runtime}}' movie-agents-agent-1)"

check "работает под gVisor (своё ядро, не ядро хоста)" \
  "import os; print(os.uname().release)" "4.4.0"
check "процесс не root" \
  "import os; print('uid', os.getuid())" "uid 10001"
check "нет capabilities" \
  "print([l for l in open('/proc/self/status') if l.startswith('CapEff')][0])" "0000000000000000"
check "корневая ФС только для чтения" \
  "
try:
    open('/app/pwned', 'w'); print('writable')
except OSError as e: print('read-only', e.errno)" "read-only"
check "нет интернета" \
  "
import socket
try:
    socket.create_connection(('1.1.1.1', 443), timeout=5); print('internet')
except OSError: print('no-internet')" "no-internet"
check "DNS наружу не работает" \
  "
import socket
try:
    socket.gethostbyname('example.com'); print('resolved')
except OSError: print('no-dns')" "no-dns"
check "хост (шлюз docker) недоступен" \
  "
import socket
for host in ('172.17.0.1', '172.28.0.1'):
    try:
        socket.create_connection((host, 22), timeout=3); print('reachable', host); break
    except OSError: pass
else: print('host-unreachable')" "host-unreachable"
check "файлов хоста не видно" \
  "import os; print('home:', os.listdir('/home'), 'ollama-dir' if os.path.exists('/var/lib/ollama') else 'no-ollama-dir')" "home: \[\] no-ollama-dir"
check "Ollama доступна (нужна для работы)" \
  "import urllib.request; print(urllib.request.urlopen('http://ollama:11434/api/version', timeout=5).status)" "200"
check "Phoenix доступен (нужен для трейсов)" \
  "import urllib.request; print(urllib.request.urlopen('http://phoenix:6006/healthz', timeout=5).status)" "200"

exit $fail
