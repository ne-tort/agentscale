#!/bin/sh
set -eu
# Default: migrate (optional) + uvicorn.
# With args (compose/k8s celery worker): skip default CMD and exec the override.
# RUN_MIGRATIONS=0 for workers so they do not race alembic with the API.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  alembic upgrade head
fi
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
exec uvicorn prodavan.main:app --host 0.0.0.0 --port 8000
