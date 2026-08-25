#!/bin/sh
# Kubernetes initContainer: apply Alembic head and fail if DB is not at head.
set -eu

alembic upgrade head

current="$(alembic current 2>/dev/null | awk 'NF && $1 !~ /^(INFO|Context|Will)/ { print $1; exit }')"
head="$(alembic heads 2>/dev/null | awk 'NF && $1 !~ /^(INFO|Rev)/ { print $1; exit }')"

if [ -z "$current" ] || [ -z "$head" ] || [ "$current" != "$head" ]; then
  echo "alembic: migration incomplete (current=${current:-?} head=${head:-?})" >&2
  exit 1
fi

echo "alembic: database at head ${head}"
