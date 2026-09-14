#!/usr/bin/env bash
set -e

echo "Resetting database..."
.venv/bin/python -m apps.worker.cli reset-db
echo "Applying migrations..."
.venv/bin/python -m alembic upgrade head
echo "Database reset complete."
