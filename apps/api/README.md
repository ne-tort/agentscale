# FastAPI backend

Спека: [`docs/05-backend/structure.md`](../../docs/05-backend/structure.md)

## Quick start

```bash
# Infrastructure (from repo root)
docker compose -f infra/docker-compose.dev.yml up -d

# Migrations (superuser — один раз после docker up)
cd apps/api
pip install -e ".[dev]"
DATABASE_URL=postgresql+asyncpg://prodavan:prodavan@localhost:5432/prodavan alembic upgrade head

# API (prodavan_app — RLS enforced)
cp .env.example .env
uvicorn prodavan.main:app --reload --host 0.0.0.0 --port 8000
```

Endpoints (I1):
- `POST /api/v1/auth/register` — tenant + user + default cabinet
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `GET /api/v1/me` — Bearer token

Health: `GET http://localhost:8000/api/v1/health` → `{"status":"ok"}`

## Tests

```bash
DATABASE_URL=postgresql+asyncpg://prodavan_app:prodavan@localhost:5432/prodavan pytest tests/ -q
```

OpenAPI stub: [`openapi/openapi.yaml`](openapi/openapi.yaml)
