#!/bin/sh
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BACKEND_HOST=${BACKEND_HOST:-127.0.0.1}
BACKEND_PORT=${BACKEND_PORT:-8000}
FRONTEND_HOST=${FRONTEND_HOST:-127.0.0.1}
FRONTEND_PORT=${FRONTEND_PORT:-5173}
PYTHON_BIN=${PYTHON_BIN:-python3}
VITE_API_TARGET=${VITE_API_TARGET:-http://${BACKEND_HOST}:${BACKEND_PORT}}
export VITE_API_TARGET

backend_pid=
frontend_pid=
cleaned_up=0

cleanup() {
  [ "$cleaned_up" -eq 0 ] || return
  cleaned_up=1
  trap - INT TERM EXIT
  [ -z "$frontend_pid" ] || kill "$frontend_pid" 2>/dev/null || true
  [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
  [ -z "$frontend_pid" ] || wait "$frontend_pid" 2>/dev/null || true
  [ -z "$backend_pid" ] || wait "$backend_pid" 2>/dev/null || true
}

trap cleanup INT TERM EXIT

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  printf 'Python executable not found: %s\n' "$PYTHON_BIN" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  printf 'npm is required but was not found on PATH.\n' >&2
  exit 1
fi
if [ ! -d "$REPO_DIR/web/node_modules" ]; then
  printf 'Frontend dependencies are missing. Run: cd "%s/web" && npm ci\n' "$REPO_DIR" >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import fastapi, multipart, sqlalchemy, uvicorn' >/dev/null 2>&1; then
  printf 'Backend dependencies are missing. Run: %s -m pip install -r "%s/requirements.txt"\n' "$PYTHON_BIN" "$REPO_DIR" >&2
  exit 1
fi

printf 'FastAPI: http://%s:%s (docs: /docs)\n' "$BACKEND_HOST" "$BACKEND_PORT"
printf 'React:  http://%s:%s (proxy -> %s)\n' "$FRONTEND_HOST" "$FRONTEND_PORT" "$VITE_API_TARGET"
printf 'Press Ctrl-C to stop both servers.\n'

(
  cd "$REPO_DIR"
  exec "$PYTHON_BIN" -m uvicorn server.main:app --reload --host "$BACKEND_HOST" --port "$BACKEND_PORT"
) &
backend_pid=$!

(
  cd "$REPO_DIR/web"
  exec npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort
) &
frontend_pid=$!

while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done

status=0
if ! kill -0 "$backend_pid" 2>/dev/null; then
  wait "$backend_pid" || status=$?
  printf 'FastAPI exited with status %s; stopping React.\n' "$status" >&2
else
  wait "$frontend_pid" || status=$?
  printf 'React exited with status %s; stopping FastAPI.\n' "$status" >&2
fi
exit "$status"
