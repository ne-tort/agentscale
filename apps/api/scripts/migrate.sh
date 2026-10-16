#!/bin/sh
# Kubernetes initContainer: apply Alembic head, then verify ORM ↔ DB parity.
# Deploy applies committed revisions only — never autogenerate here.
#
# Boot ordering: после рестарта ноды postgres поднимается дольше, чем api-под
# доходит до init. Раньше первый alembic падал по коннекту → initContainer
# CrashLoop (Error у api на каждом ребуте). Ждём БД до ~5 минут внутри init —
# под остаётся Init:0/N без крашей.
set -eu

echo "alembic: waiting for database"
i=0
until alembic current >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "alembic: ERROR database unreachable after 300s" >&2
    exit 1
  fi
  sleep 5
done

echo "alembic: upgrade head"
alembic upgrade head

echo "alembic: verify current == head"
# `alembic check` alone is not enough: an older image (IfNotPresent cache) can
# upgrade-noop at an outdated head while the main container runs newer ORM.
current="$(alembic current 2>/dev/null | awk '/^[0-9]/{print $1; exit}')"
head_rev="$(alembic heads 2>/dev/null | awk '{print $1; exit}')"
if [ -z "${current}" ] || [ -z "${head_rev}" ] || [ "${current}" != "${head_rev}" ]; then
  echo "alembic: ERROR current=${current:-?} head=${head_rev:-?} (image/schema skew)" >&2
  exit 1
fi
echo "alembic: current=${current} (head)"

echo "alembic: check (models must match database at head)"
alembic check

echo "alembic: ok — database at head and consistent with SQLAlchemy models"
