#!/usr/bin/env bash
set -euo pipefail

BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="$BACKEND/.venv/bin/python3.12"
PORT="${ASHARE_BACKEND_PORT:-8765}"
HOST="${ASHARE_BACKEND_HOST:-127.0.0.1}"

cd "$BACKEND"
export PYTHONUNBUFFERED=1
exec "$VENV_PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
