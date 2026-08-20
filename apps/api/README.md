# FastAPI backend

Спека: [`docs/05-backend/structure.md`](../../docs/05-backend/structure.md)

## Quick start

```bash
# Infrastructure (from repo root)
docker compose -f infra/docker-compose.dev.yml up -d

# API
cd apps/api
python -m venv .venv
# Windows: .venv\Scripts\activate
# WSL/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn prodavan.main:app --reload --host 0.0.0.0 --port 8000
```

Health: `GET http://localhost:8000/api/v1/health` → `{"status":"ok"}`

## Tests

```bash
pytest tests/integration/test_health.py
```

OpenAPI stub: [`openapi/openapi.yaml`](openapi/openapi.yaml)
