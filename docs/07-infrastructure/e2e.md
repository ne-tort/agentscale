# E2E testing strategy

Единая трёхуровневая пирамида на **одном dev-контуре** (k3s + Argo `prodavan-dev`). Отдельного test-кластера нет.

**Legacy (удалено, не использовать):**

- `tools/_live_containers_e2e.py` и `tools/generated/_e2e_*.py` — заменены pytest L3b/L2
- Ручные `kubectl apply` overlay для тестов — только `prodavan-ops e2e run/cleanup`
- Ad-hoc скрипты в `tools/_*.sh` — не часть тестовой системы (не коммитить)

---

## Уровни

| Уровень | Marker | Где | Зависимости | CI |
|---------|--------|-----|-------------|-----|
| **L1 Unit** | — | `tests/unit/` | — | CI Gate (каждый PR) |
| **L2 Integration** | `integration` | `tests/integration/` | Docker Postgres, stub pods | Nightly + opt-in E2E |
| **L3a K8s runtime** | `k8s` | `tests/e2e/k8s/` | in-cluster Job, `prodavan-sandboxes` | opt-in E2E |
| **L3b Live API** | `live` | `tests/e2e/live/` | Traefik `:8088` | opt-in E2E |

`prodavan-api` на dev **всегда** `POD_RUNTIME_MODE=stub`. K8s pod-тесты — in-process в ephemeral Job (`infra/k3s/overlays/e2e/`), без переключения боевого Deployment.

### Ответственность слоёв

| Слой | Что проверяет | Чего не делает |
|------|---------------|----------------|
| **L2** | Бизнес-логика API, PG, stub pod lifecycle | Real k8s, ingress, OIDC |
| **L3a** | Real Pod create/pause/kill в `prodavan-sandboxes` | Не трогает `prodavan-api` Deployment |
| **L3b** | Deployed stack: Traefik, auth config, agent chat | Не подменяет L2 unit/integration |
| **Verify Dev** | HTTP smoke `/health/*`, auth config | Не pytest, не pod lifecycle |

---

## Локальный запуск

### L1 — unit (быстро)

```bash
cd apps/api
poetry run pytest tests/unit/ -q
```

### L2 — integration (stub runtime)

```bash
cd apps/api
export DATABASE_URL=postgresql+asyncpg://prodavan_app:prodavan@127.0.0.1:5432/prodavan
alembic upgrade head
pytest -m integration -q
# точечно:
pytest tests/integration/test_e2e_smoke.py -m integration -q
pytest tests/integration/test_pod_service_e2e.py -m integration -q
```

**Auth в L2:** JWT `sub` = `admin_employee.keycloak_sub` из ответа `POST /companies`. Хелпер: [`tests/integration/support.py`](../../apps/api/tests/integration/support.py).

### L3b — live HTTP (dev cluster up)

```bash
cd apps/api
export PRODAVAN_E2E_BASE_URL=http://127.0.0.1:8088
export PRODAVAN_E2E_JWT_SECRET=k3s-dev-change-me-in-production-32b  # dev cluster secret
pytest tests/e2e/live -m live -q
```

### L3a — k8s pods (in-cluster Job)

```bash
export KUBECONFIG=~/.kube/prodavan-dev.yaml
cd infra/ops && poetry install
poetry run prodavan-ops e2e run --suite k8s
poetry run prodavan-ops e2e cleanup
```

Job создаёт БД `prodavan_e2e` на in-cluster Postgres (не трогает dev `prodavan`).

### Все маркеры

```bash
pytest -m integration          # L2
pytest -m k8s                  # L3a (local: нужен kubeconfig + sandboxes NS)
pytest -m live                 # L3b
pytest -m "integration or k8s or live"
```

---

## CI

| Workflow | Когда | Что гоняет |
|----------|-------|------------|
| [`ci-gate.yml`](../../.github/workflows/ci-gate.yml) | каждый PR | L1 unit + kustomize validate (incl. `overlays/e2e`) |
| [`ci-nightly.yml`](../../.github/workflows/ci-nightly.yml) | cron 02:00 UTC | L2 `pytest -m integration` |
| [`ci-e2e.yml`](../../.github/workflows/ci-e2e.yml) | **opt-in** | L2 + L3a + L3b |
| [`verify-dev.yml`](../../.github/workflows/verify-dev.yml) | post-merge | HTTP smoke (не pytest) |

### Opt-in E2E (`ci-e2e.yml`)

Триггеры:

1. **Label `e2e`** на PR — основной способ
2. **`[e2e]`** в заголовке PR
3. **`workflow_dispatch`** — suite: `all` | `integration` | `k8s` | `live`

CI Gate **не** замедляется e2e по умолчанию.

---

## K8s overlay (ephemeral)

```
infra/k3s/overlays/e2e/
├── job.yaml          # prodavan-e2e-runner — pytest -m k8s
├── configmap.yaml    # POD_RUNTIME_MODE=k8s, DATABASE_URL→prodavan_e2e
├── rbac.yaml         # SA prodavan-e2e-runner
└── rbac-sandboxes.yaml
```

Argo Application [`prodavan-e2e`](../../infra/argocd/apps/prodavan-e2e.yaml) — **manual sync only**. Runner: `prodavan-ops e2e run`.

## Cleanup

- Job: `ttlSecondsAfterFinished: 3600`
- K8s tests: teardown Pods по `prodavan.io/project-id` в `prodavan-sandboxes`
- `prodavan-ops e2e cleanup` — удаляет Job и overlay resources

---

## Связанные docs

- [`local-cluster-e2e.md`](local-cluster-e2e.md) — bootstrap кластера + smoke
- [`github-actions.md`](github-actions.md) — все workflows
- [`L09-vertical-integration.md`](../target/12-layer-docs/L09-vertical-integration.md)
- [`patterns-references.md`](../target/14-project-containers/k3s-runtime/patterns-references.md)
