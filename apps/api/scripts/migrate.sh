#!/bin/sh
# Kubernetes initContainer: apply Alembic head, then verify ORM ↔ DB parity.
# Deploy applies committed revisions only — never autogenerate here.
set -eu

echo "alembic: upgrade head"
alembic upgrade head

echo "alembic: check (models must match database at head)"
alembic check

echo "alembic: ok — database at head and consistent with SQLAlchemy models"
