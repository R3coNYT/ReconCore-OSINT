#!/usr/bin/env bash
# Development startup (outside Docker): API plus worker.
# Requirements: PostgreSQL and Redis reachable, `.env` filled in.
set -euo pipefail

cd "$(dirname "$0")/../backend"

if [ ! -d .venv ]; then
  python -m venv .venv
  ./.venv/bin/pip install -r requirements-dev.txt
  ./.venv/bin/pip install -e .
fi

export $(grep -v '^#' ../.env | grep -v '^$' | xargs -d '\n') 2>/dev/null || true

./.venv/bin/python -c "import app.models; from app.db.base import Base; from app.db.session import engine; Base.metadata.create_all(engine); print('schema ready')"

./.venv/bin/celery -A app.workers.celery_app.celery_app worker --queues=default,sherlock,holehe,phoneinfoga,websearch --concurrency=2 --loglevel=INFO &
WORKER_PID=$!
trap 'kill $WORKER_PID 2>/dev/null || true' EXIT

./.venv/bin/uvicorn app.main:app --reload --port 8000
