# syntax=docker/dockerfile:1
# Build context = prodavan/ repo root.
# Layering: OS → pyproject deps (cached) → app sources (invalidate often).

FROM python:3.12-slim-bookworm AS base

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STORAGE_ROOT=/data/storage \
    PACKS_ROOT=/packages/cabinet-packs \
    RUN_MIGRATIONS=1

# 1) Dependency metadata only — cache until pyproject.toml changes
COPY apps/api/pyproject.toml apps/api/README.md ./
RUN mkdir -p src/prodavan \
  && printf '%s\n' '"""Prodavan API package stub for layer-cached pip install."""' > src/prodavan/__init__.py

# 2) Install deps into BuildKit pip cache + image layer
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -e .

# 3) App sources — code edits do not re-download wheels
COPY apps/api/src ./src
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./
COPY apps/api/docker-entrypoint.sh /docker-entrypoint.sh
COPY packages/cabinet-packs /packages/cabinet-packs
COPY packages/schemas /packages/schemas

# Editable install already points at /app/src; PYTHONPATH reinforces imports.
RUN chmod +x /docker-entrypoint.sh \
  && mkdir -p /data/storage

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
