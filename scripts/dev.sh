#!/usr/bin/env bash
# Dev script: launches FastAPI backend (port 8000) + Vite frontend (port 5173)
# concurrently. Vite is configured to proxy /api -> localhost:8000.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Pick python: prefer backend/.venv, else system python3
if [ -x "$REPO_ROOT/backend/.venv/bin/python" ]; then
  PY="$REPO_ROOT/backend/.venv/bin/python"
else
  PY="$(command -v python3 || command -v python)"
  if [ -z "$PY" ]; then
    echo "ERROR: python3 not found. Install Python 3.11+." >&2
    exit 1
  fi
fi

BACKEND_CMD="cd $REPO_ROOT/backend && $PY -m uvicorn app.main:app --reload --port 8000"
FRONTEND_CMD="cd $REPO_ROOT/frontend && pnpm dev --host"

# Use the workspace-root concurrently (devDependency in root package.json)
exec pnpm -w exec concurrently --kill-others-on-fail \
  --names "backend,frontend" \
  --prefix-colors "cyan,magenta" \
  "$BACKEND_CMD" \
  "$FRONTEND_CMD"
