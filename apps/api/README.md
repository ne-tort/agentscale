# Prodavan API

FastAPI backend платформы Prodavan. Канон домена — [`docs/target/`](../../docs/target/).

## Локально

```bash
cd apps/api
pip install -e ".[dev]"
export DATABASE_URL=postgresql+asyncpg://prodavan:prodavan@127.0.0.1:5432/prodavan
alembic upgrade head
uvicorn prodavan.main:app --reload
```

## Миграции

- **Источник истины:** SQLAlchemy models → `alembic revision --autogenerate` в PR.
- **Деплой:** k8s initContainer `./scripts/migrate.sh` (`upgrade head` + `alembic check`).
- **Autogenerate при деплое запрещён.**

Подробно: [docs/07-infrastructure/alembic.md](../../docs/07-infrastructure/alembic.md).

## Docker / k3s

- Образ: `apps/api/Dockerfile` (build context = корень `prodavan/`).
- Main process: `uvicorn` only; миграции — initContainer, не entrypoint.
