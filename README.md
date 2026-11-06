# Agentscale

SaaS: **управление Pod'ами через UI**, внутри Pod — **AI-агенты с файлами** (не ChatGPT-обёртка).

Продукт: [`docs/PRODUCT.md`](docs/PRODUCT.md) · Ops: [`docs/07-infrastructure/runbook.md`](docs/07-infrastructure/runbook.md)

Коммерческая платформа: **Flutter** + **FastAPI** + **PostgreSQL** + **k3s** (GitOps).

Изолирован от [Commerce](https://github.com/ne-tort/commerce): отдельный репозиторий, отдельный деплой.

## Стек

| Слой | Технология |
|------|------------|
| Frontend | Flutter |
| Backend | Python 3.12+, FastAPI, Alembic |
| Runtime | k8s Pods (`agentscale-dev-sandboxes`) + agent SDK |
| DB | PostgreSQL (in-cluster dev) |
| Infra | **k3s + Argo CD + kustomize** |

## Dev cluster (GitOps)

Кластер живёт на VM `www@172.31.156.203`; Terraform выполняется **на VM** (SSH на
localhost), Windows-хост — только клиент (SSH/scp/браузер). Единственный путь —
[`docs/07-infrastructure/runbook.md`](docs/07-infrastructure/runbook.md):

```bash
# bootstrap / полный цикл «снести → поставить с нуля» — runbook §2
ssh www@172.31.156.203
cd ~/git/agentscale && git pull --ff-only
export TF_VAR_ghcr_token='<токен>'   # ОБЯЗАТЕЛЕН; значение — с аутентифицированной машины (Windows: gh auth token)
terraform -chdir=infra/terraform/environments/vm init
terraform -chdir=infra/terraform/environments/vm apply -auto-approve

# verify (на VM)
export KUBECONFIG=~/.kube/prodavan-dev.yaml
cd infra/ops && poetry install
poetry run prodavan-ops wait && poetry run prodavan-ops smoke
```

UI: `http://172.31.156.203:8088/` (HTTPS `https://172.31.156.203/` — после импорта
локального CA в браузер). Keycloak: `http://172.31.156.203:8089/`.

Day-2: PR → CI Gate → merge → CI Images → Argo sync → Verify Dev — **не** через terraform.

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
