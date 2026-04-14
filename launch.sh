#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt >/dev/null

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Set VANDERBILT_BEARER before using AI support."
fi

PORT="${PORT:-8080}"
if [ "${RELOAD:-0}" = "1" ]; then
  exec python -m uvicorn app:app --host 0.0.0.0 --port "$PORT" --reload
fi

exec python -m uvicorn app:app --host 0.0.0.0 --port "$PORT"
