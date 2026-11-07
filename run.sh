#!/usr/bin/env bash
# Start the SAGE backend and frontend together.
# Usage: ./run.sh   (Ctrl-C stops both)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${SAGE_PYTHON:-python3}"

if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
  echo "==> creating virtualenv"
  "$PYTHON" -m venv "$ROOT/backend/.venv"
  "$ROOT/backend/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"
fi

if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "==> installing frontend dependencies"
  (cd "$ROOT/frontend" && npm install)
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "==> backend  http://127.0.0.1:8000/docs"
(cd "$ROOT/backend" && exec .venv/bin/uvicorn app.main:app --port 8000)

echo "==> frontend http://localhost:5173"
(cd "$ROOT/frontend" && exec npm run dev)

wait
