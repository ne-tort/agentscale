# Agentscale

SaaS: **управление Pod'ами через UI**, внутри Pod — **AI-агенты с файлами** (не ChatGPT-обёртка).

Продукт: [`docs/PRODUCT.md`](docs/PRODUCT.md) · Ops: [`docs/07-infrastructure/runbook.md`](docs/07-infrastructure/runbook.md)

Коммерческая платформа: **TypeScript/React** + **FastAPI** + **PostgreSQL** + **k3s** (GitOps).

Изолирован от [Commerce](https://github.com/ne-tort/commerce): отдельный репозиторий, отдельный деплой.

## Стек

| Слой | Технология |
|------|------------|
| Frontend | React 19 + TypeScript + Vite (`apps/web`) |
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

## Локальная разработка (без кластера)

```bash
# API
cd apps/api && pip install -e ".[dev]" && uvicorn prodavan.main:app --reload --port 8000

# Web: Vite dev-сервер на :5173, /api проксируется на локальный API (:8000)
pnpm install
pnpm --filter @agentscale/web dev
```

`pnpm --filter @agentscale/web build` — то же, что CI: `tsc --noEmit && vite build`.
`pnpm --filter @agentscale/web test` — vitest.

## Документация

- **Current product / S1:** [docs/PRODUCT.md](docs/PRODUCT.md)
- **Current state и STOP-SHIP:** [docs/migration/plugin-migration-status.md](docs/migration/plugin-migration-status.md)
- **Target / accepted ADR:** [docs/architecture-migration/README.md](docs/architecture-migration/README.md)
- **Plugin authoring (matrix, не quickstart):** [docs/PLUGIN-AUTHORING.md](docs/PLUGIN-AUTHORING.md)
- **As-built:** текущий код и манифесты; [12-layer-docs](docs/target/12-layer-docs/) — только исторический snapshot до перерасчёта
- **Ops / GitOps:** [docs/07-infrastructure/](docs/07-infrastructure/)
- **Категории и история:** [docs/LEGACY.md](docs/LEGACY.md)
