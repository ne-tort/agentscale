# Prodavan

> **STUB.** `apps/api` и `apps/flutter` очищены до болванки. Канон продукта: [`docs/target/`](docs/target/). См. [`STUB.md`](STUB.md).

Коммерческая платформа автономного агента: **Flutter Web** + **FastAPI** + **PostgreSQL** + **k3s**.

Изолирован от [Commerce](https://github.com/ne-tort/commerce) (Telegram MVP): отдельный репозиторий, отдельный деплой.

## Стек (целевой)

| Слой | Технология |
|------|------------|
| Frontend | Flutter (mobile-first, см. `docs/target/07-ui-mobile-core`) |
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2, Alembic |
| DB | PostgreSQL |
| Agent | Cursor / Codex / Claude — см. `docs/target/08-agent-providers` |
| Infra | k3s, Terraform, Argo CD, GitHub Actions |

## Quick start (local)

```bash
# Infrastructure only (PG + MinIO + Redis)
docker compose -f infra/docker-compose.dev.yml up -d

# Full stack (PG + API stub + Flutter stub web)
bash infra/scripts/stack-up.sh
# UI http://127.0.0.1:8080  ·  API http://127.0.0.1:8000/health

# API
cd apps/api && pip install -e ".[dev]" && uvicorn prodavan.main:app --reload --port 8000

# Flutter
cd apps/flutter && flutter pub get && flutter run -d chrome
```

После cutover со stub на уже существующей БД — сбросить схему (см. [alembic.md](docs/07-infrastructure/alembic.md)).

## Документация

- **Канон:** [docs/target/](docs/target/)
- **Stub / политика:** [STUB.md](STUB.md) · [docs/LEGACY.md](docs/LEGACY.md)
- **Ops:** [docs/07-infrastructure/](docs/07-infrastructure/) · [Alembic](docs/07-infrastructure/alembic.md)
