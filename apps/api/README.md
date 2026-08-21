# Prodavan API — STUB

Это **болванка** платформы. Доменную логику не восстанавливать из git history —
реализовывать по канону [`docs/target/`](../../docs/target/).

Что оставлено для k3s / CI:

- `GET /health`, `/health/live`, `/health/ready` (ready = Postgres `SELECT 1`)
- `GET /api/v1/stub` — маркер stub
- Alembic: единственная ревизия `stub_bootstrap` (таблица `stub_meta`)
- Entrypoint: `alembic upgrade head` → uvicorn

Миграции: [docs/07-infrastructure/alembic.md](../../docs/07-infrastructure/alembic.md).
