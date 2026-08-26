#!/bin/sh
# Kubernetes initContainer: apply Alembic head, then verify ORM ↔ DB parity.
# Deploy applies committed revisions only — never autogenerate here.
set -eu

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
