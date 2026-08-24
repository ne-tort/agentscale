# Prodavan

> **STUB.** `apps/api` и `apps/flutter` — болванка. Канон: [`docs/target/`](docs/target/). См. [`STUB.md`](STUB.md).

Коммерческая платформа: **Flutter Web** + **FastAPI** + **PostgreSQL** + **k3s** (GitOps).

Изолирован от [Commerce](https://github.com/ne-tort/commerce): отдельный репозиторий, отдельный деплой.

## Стек

| Слой | Технология |
|------|------------|
| Frontend | Flutter |
| Backend | Python 3.12+, FastAPI, Alembic |
| DB | PostgreSQL (in-cluster dev) |
| Infra | **k3s + Argo CD + kustomize** |

## Dev cluster (GitOps)

Единственный путь — [`docs/07-infrastructure/runbook.md`](docs/07-infrastructure/runbook.md):

```bash
# k3s + Argo bootstrap (once)
kubectl apply -k infra/argocd/install
kubectl apply -k infra/argocd/sealed-secrets
kubectl apply -f infra/argocd/root-app.yaml

# verify
cd infra/ops && poetry install
poetry run prodavan-ops wait && poetry run prodavan-ops smoke
```

UI: `http://localhost:8088/`.

Day-2: PR → CI Gate → merge → CI Images → Argo sync → Verify Dev.

## Локальная разработка API/Flutter (без кластера)

```bash
cd apps/api && pip install -e ".[dev]" && uvicorn prodavan.main:app --reload --port 8000
cd apps/flutter && flutter pub get && flutter run -d chrome
```

## Документация

- **Канон продукта:** [docs/target/](docs/target/)
- **Ops / GitOps:** [docs/07-infrastructure/runbook.md](docs/07-infrastructure/runbook.md)
- **Stub:** [STUB.md](STUB.md) · [docs/LEGACY.md](docs/LEGACY.md)
