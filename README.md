# Prodavan

Коммерческая платформа автономного агента закупок: **Flutter Web** (→ Android/Windows/Linux) + **FastAPI** + **PostgreSQL** + **k3s**.

Изолирован от [Commerce](https://github.com/ne-tort/commerce) (Telegram MVP): отдельный репозиторий, отдельный деплой, без общих volumes.

## Изоляция данных

```text
Tenant → Cabinet → Project
```

- **Cabinet profile** (`electronics-procurement` в v1) задаёт UI, seed-данные, MCP tools.
- **S4B** — только кабинет электроники; другие профили S4B не получают.

## Стек

| Слой | Технология |
|------|------------|
| Frontend | Flutter, feature-based + Clean Architecture |
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2, Alembic |
| DB | PostgreSQL (RLS `tenant_id` + `cabinet_id`) |
| Object store | S3-compatible (MinIO) |
| Agent | Cursor SDK (primary), Codex CLI, Claude Code CLI |
| Infra | k3s, Terraform, Argo CD, GitHub Actions, local GH runner |

## Quick start (local)

```bash
# Infrastructure only (PG + MinIO + Redis)
docker compose -f infra/docker-compose.dev.yml up -d

# Buildx local cache (required for cache_to type=local)
bash infra/scripts/docker-build-cached.sh ensure-builder
export BUILDX_BUILDER=prodavan DOCKER_BUILDKIT=1

# Full stack (PG + API + Flutter web) — без k3s
# На Windows используй Docker Desktop (не Kali/WSL docker с Amnezia —
# он часто рестартится и даёт ERR_CONNECTION_REFUSED на localhost:8080).
docker context use desktop-linux
bash infra/scripts/stack-up.sh
# UI http://127.0.0.1:8080  ·  API http://127.0.0.1:8000/api/v1/health

# API (dev reload)
cd apps/api && pip install -e ".[dev]" && uvicorn prodavan.main:app --reload --port 8000

# Flutter (Chrome)
cd apps/flutter && flutter pub get && flutter run -d chrome
```

Health: `GET /api/v1/health` и probes `GET /health/live` · `GET /health/ready`.

## k3s (single-node)

Манифесты: [`infra/k3s/`](infra/k3s/) (канон; `infra/k8s/` — только redirect).

```bash
kubectl apply -k infra/k3s/overlays/dev
kubectl -n prodavan rollout status deploy/prodavan-api
# hosts: prodavan.local → node IP
curl -sS http://prodavan.local/api/v1/health
```

Образы: GHCR via [`.github/workflows/ci-images.yml`](.github/workflows/ci-images.yml). Пробелы: [`docs/09-checklists/CLUSTER-GAPS.md`](docs/09-checklists/CLUSTER-GAPS.md).

Terraform skeleton (без cloud apply): [`infra/terraform/`](infra/terraform/).

## Документация

Карта: [`docs/README.md`](docs/README.md)

Прогресс: [`docs/09-checklists/PROGRESS.md`](docs/09-checklists/PROGRESS.md)

Roadmap кода: [`docs/10-implementation/roadmap.md`](docs/10-implementation/roadmap.md)

## Структура репозитория

```text
prodavan/
├── apps/flutter/            # Flutter client
├── apps/api/                # FastAPI backend
├── packages/cabinet-packs/  # Profile packs + seed data
├── packages/schemas/        # JSON Schema (cabinet-profile)
├── tools/                   # validate_schemas.py
├── docs/                    # Модульная документация
└── infra/
    ├── docker-compose.dev.yml
    ├── docker-compose.stack.yml
    ├── k3s/                 # канон манифестов
    └── terraform/           # cloud primitives (skeleton)
```

## Commerce submodule

В монорепо Commerce подключён как git submodule `prodavan/` (ветка `prodavan/platform-foundation`).
