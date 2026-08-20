# Prodavan

Коммерческая платформа автономного агента закупок: **Flutter Web** (→ Android/Windows/Linux) + **FastAPI** + **PostgreSQL** + **k3s**.

Изолирован от [Commerce](../Commerce) (Telegram MVP): отдельный репозиторий, отдельный деплой, без общих volumes.

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

## Документация

Карта: [`docs/README.md`](docs/README.md)

Прогресс реализации: [`docs/09-checklists/PROGRESS.md`](docs/09-checklists/PROGRESS.md)

## Структура репозитория

```text
prodavan/
├── apps/flutter/          # Flutter client (placeholder)
├── apps/api/              # FastAPI (placeholder)
├── packages/cabinet-packs/  # Profile packs + seed data
├── docs/                  # Модульная документация
└── infra/                 # Terraform, k3s, Argo CD, runner
```

## Commerce submodule

В монорепо Commerce подключён как git submodule `prodavan/` (ветка `prodavan/platform-foundation`).
