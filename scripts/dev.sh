#!/usr/bin/env bash
set -e

# Change to project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Market Radar..."
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Ensure python venv
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

# Ensure database is seeded
if [ ! -f "gridscout.db" ]; then
  echo "Initializing and seeding database..."
  .venv/bin/python scripts/seed.py
fi

echo "Running FastAPI backend on port 8000..."
.venv/bin/python -m uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload &
API_PID=$!

echo "Starting React frontend on port 5173..."
cd apps/web && npm run dev &
WEB_PID=$!

trap "kill $API_PID $WEB_PID 2>/dev/null || true" EXIT
wait
