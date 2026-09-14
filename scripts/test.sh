#!/usr/bin/env bash
set -e

echo "=== 1. Running Backend Unit & Integration Tests ==="
.venv/bin/pytest tests/

echo "=== 2. Running Frontend Tests & Typecheck ==="
cd apps/web
npm test -- --run
npm run build
cd ../..

echo "=== ALL QUALITY GATES PASSED SUCCESSFULLY ==="
