#!/bin/sh
set -eu
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  alembic upgrade head
fi
exec uvicorn prodavan.main:app --host 0.0.0.0 --port 8000
