# Prodavan

SaaS: **управление Pod'ами через UI**, внутри Pod — **AI-агенты с файлами** (не ChatGPT-обёртка).

Продукт: [`docs/PRODUCT.md`](docs/PRODUCT.md) · Ops: [`docs/07-infrastructure/runbook.md`](docs/07-infrastructure/runbook.md)

Коммерческая платформа: **Flutter** + **FastAPI** + **PostgreSQL** + **k3s** (GitOps).

Изолирован от [Commerce](https://github.com/ne-tort/commerce): отдельный репозиторий, отдельный деплой.

## Стек

| Слой | Технология |
|------|------------|
| Frontend | Flutter |
| Backend | Python 3.12+, FastAPI, Alembic |
| Runtime | k8s Pods (`prodavan-sandboxes`) + agent SDK |
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

- **Продукт:** [docs/PRODUCT.md](docs/PRODUCT.md)
- **As-built (код):** [docs/target/12-layer-docs/](docs/target/12-layer-docs/)
- **Ops / GitOps:** [docs/07-infrastructure/](docs/07-infrastructure/)
- **Legacy (в т.ч. AI-канон `target/`):** [docs/LEGACY.md](docs/LEGACY.md)
