#!/usr/bin/env bash
set -e

echo "Seeding deterministic synthetic dataset..."
.venv/bin/python -m apps.worker.cli seed-fixtures
echo "Synthetic dataset seeded."
