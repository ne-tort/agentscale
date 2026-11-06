# E2E testing strategy

Единая трёхуровневая пирамида на **одном dev-контуре** (k3s + Argo `agentscale-dev`). Отдельного test-кластера нет.

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
| **L3a K8s runtime** | `k8s` | `tests/e2e/k8s/` | in-cluster Job, `agentscale-dev-sandboxes` | opt-in E2E |
| **L3b Live API** | `live` | `tests/e2e/live/` | Traefik `:8088` | opt-in E2E |

API на dev работает с **`POD_RUNTIME_MODE=sandbox`** (overlay `infra/k3s/overlays/dev/`). K8s pod-тесты — in-process в ephemeral Job с `POD_RUNTIME_MODE=k8s` (`infra/k3s/overlays/e2e/`), без переключения боевого Deployment отдельно.

### Ответственность слоёв

| Слой | Что проверяет | Чего не делает |
|------|---------------|----------------|
| **L2** | Бизнес-логика API, PG, stub pod lifecycle | Real k8s, ingress, OIDC |
| **L3a** | Real Pod create/pause/kill в `agentscale-dev-sandboxes` | Не трогает `agentscale-api` Deployment |
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
export DATABASE_URL=postgresql+asyncpg://agentscale_app:agentscale@127.0.0.1:5432/agentscale
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
# на VM (раннеры/оператор): Traefik и Keycloak доступны на 127.0.0.1;
# с Windows-хоста — http://172.31.156.203:8088 / :8089
export PRODAVAN_E2E_BASE_URL=http://127.0.0.1:8088
export PRODAVAN_E2E_KC_URL=http://127.0.0.1:8089
pytest tests/e2e/live -m live -q
```

**Auth в L3b:** реальные OIDC-токены Keycloak (не HS256 mint). Platform Admin — ROPC `admin`/`admin` через client `agentscale-flutter`. Owner/peer — `POST /companies/{id}/employees` с `login`/`password`, затем `POST /auth/login`. Хелпер: [`tests/e2e/live/keycloak_auth.py`](../../apps/api/tests/e2e/live/keycloak_auth.py).

### L3a — k8s pods (in-cluster Job)

```bash
export KUBECONFIG=~/.kube/prodavan-dev.yaml
cd infra/ops && poetry install
poetry run prodavan-ops e2e run --suite k8s
poetry run prodavan-ops e2e cleanup
```

Job создаёт БД `agentscale_e2e` на in-cluster Postgres (не трогает dev `agentscale`).

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
| [`ci-gate.yml`](../../.github/workflows/ci-gate.yml) | каждый PR | пять job'ов: `infra` (`prodavan-ops validate` — kustomize render dev + `overlays/e2e` + argocd, пины образов), `api` (L1 unit + PG-сьюты), `web`, `flutter`, `schemas`; `gate` требует все зелёными |
| [`ci-nightly.yml`](../../.github/workflows/ci-nightly.yml) | cron 02:00 UTC | L2: `pytest tests/integration -m integration` (эфемерный Postgres, порт 55433) |
| [`ci-e2e.yml`](../../.github/workflows/ci-e2e.yml) | **opt-in** | L2 + L3a + L3b (+ `bridge`, non-blocking) |
| [`ci-images.yml`](../../.github/workflows/ci-images.yml) | post-merge / dispatch | GHCR `:latest` + SHA |
| [`verify-dev.yml`](../../.github/workflows/verify-dev.yml) | post-merge | HTTP smoke (не pytest) |
| [`auto-merge.yml`](../../.github/workflows/auto-merge.yml) | после **CI Gate** | squash-merge + dispatch Images |

### Где бегут раннеры

**Все** workflows (`ci-gate`, `ci-e2e`, `verify-dev`, `auto-merge`) — `runs-on: [self-hosted, linux, docker]`.

Это **systemd-раннеры на dev-VM** (`vm-*`, лейблы `self-hosted,linux,docker,ci-vm`;
[`infra/github-runner-vm/`](../../infra/github-runner-vm/README.md)), не pods в k3s. Они **не должны** жить в кластере. Раннеры делят VM с кластером, поэтому kubectl/Traefik/Keycloak доступны на `127.0.0.1`. Исторический вариант «Docker Desktop runners на Windows-хосте» ([`infra/github-runner/`](../../infra/github-runner/README.md)) заменён.

Доступ к кластеру с runner:

- `/home/runner/.kube/prodavan-dev.yaml` — кладёт terraform apply (`runner_kubeconfig_path`), server `127.0.0.1:6443`
- HTTP smoke/live: `SMOKE_ADDR=127.0.0.1` → `:8088` (Traefik) / `:8089` (Keycloak)
- rewrite kubeconfig на `host.docker.internal` ([`prodavan_ops/k8s.py`](../../infra/ops/src/prodavan_ops/k8s.py)) включается только при `PRODAVAN_CI_HOST=host.docker.internal` — легаси Docker Desktop-раннеров, на VM не используется

**L3a k8s** — runner **снаружи** кластера: `kubectl apply` ephemeral Job, pytest **in-cluster** в Job pod (код из образа `ghcr.io/ne-tort/agentscale-api:latest` + pip install pytest). Runner только оркестрирует Job и читает логи. Перед запуском CI дополнительно кладёт **исходники тестов из PR** в ConfigMaps `agentscale-e2e-conftest` / `agentscale-e2e-k8s-tests` — Job копирует их поверх `/app/tests/e2e/…` из образа, поэтому тестовый код — из PR, а код приложения — из `:latest` (main).

**L3b live** — pytest **на runner**, HTTP в **уже задеплоенный** dev (`:8088`). Код PR **не** деплоится перед этим шагом.

### Порядок и что проверяет какой код

```text
PR opened
  ├─ CI Gate (L1)          ← код из PR checkout, блокирует auto-merge
  ├─ CI E2E (opt-in)       ← параллельно Gate, merge НЕ блокирует
  │    ├─ L2 integration   ← код PR + ephemeral Postgres на runner
  │    ├─ L3a k8s Job      ← pytest in-cluster; образ API = последний GHCR (main), не PR diff*
  │    └─ L3b live         ← deployed dev stack (main/previous deploy), не PR diff
  └─ auto-merge            ← только после green CI Gate (E2E не ждёт)

merge → CI Images → Argo sync → Verify Dev (HTTP smoke на deployed dev)
```

\* L3a Job использует `:latest` образ (код приложения — из main), но **тестовый код берётся из PR**: job `k8s` кладёт `conftest.py` и `tests/e2e/k8s` в ConfigMaps, Job копирует их поверх образа. То есть проверяются PR-тесты против main-кода; полноценная проверка diff'а приложения в k8s — только post-merge. L2 integration закрывает бизнес-логику на коде PR.

### Когда нужен post-merge E2E

| Слой | Pre-merge (PR + label `e2e`) | Post-merge (после Verify Dev) |
|------|------------------------------|-------------------------------|
| L2 integration | ✅ код PR | nightly / dispatch |
| L3a k8s | ⚠️ in-cluster, образ main | ✅ после Images — свежий код в Job |
| L3b live | ⚠️ dev ещё без PR | ✅ **имеет смысл здесь** — проверяет задеплоенный diff |

**L3b live на PR** — sanity «dev жив», не «PR безопасен для деплоя». Для регрессии **после** деплоя: `workflow_dispatch` ci-e2e suite `live` на main, или отдельный `workflow_run` после Verify Dev (TODO).

**Отдельный merge для live-e2e не нужен** — merge один (после Gate). Live regression — **после** Images + Argo sync, не до merge.

### Opt-in E2E (`ci-e2e.yml`)

Триггеры:

1. **Label `e2e`** на PR — основной способ
2. **`[e2e]`** в заголовке PR
3. **`workflow_dispatch`** — suite: `all` | `integration` | `k8s` | `live` | `bridge`

Job'ы `ci-e2e.yml`: `gate` (определяет, что запускать) → `integration`, `dev-preflight`
(только для k8s/live) → `k8s`, `live`, `bridge` → `done` (сводка; падает, если
запрошенный и упавший слой не `success`). `bridge` помечен `continue-on-error: true`
и в сводку не входит: он собирает образ `prodavan-agent-runtime:e2e` из клона
`prodavan-claw` и гоняет `tests/e2e/bridge -m bridge_e2e` против внешнего
OpenAI-совместимого провайдера.

CI Gate **не** замедляется e2e по умолчанию.

---

## K8s overlay (ephemeral)

```
infra/k3s/overlays/e2e/
├── job.yaml                  # agentscale-e2e-runner — pytest -m k8s
├── configmap.yaml            # POD_RUNTIME_MODE=k8s, DATABASE_URL→agentscale_e2e
├── rbac.yaml                 # SA agentscale-e2e-runner (ns agentscale-dev)
├── rbac-sandboxes.yaml       # SA agentscale-project-pod (ns agentscale-dev-sandboxes)
├── sandboxes-bootstrap.yaml  # NS + SA/secret для sandbox-подов (применяется до overlay)
├── namespace-transformer.yaml
└── kustomization.yaml
```

Argo Application [`agentscale-e2e`](../../infra/argocd/apps/agentscale-e2e.yaml) — **manual sync only**. Runner: `prodavan-ops e2e run` (применяет `sandboxes-bootstrap.yaml`, затем `overlays/e2e`, ждёт Job, печатает логи при провале).

## Cleanup

- Job: `ttlSecondsAfterFinished: 3600`
- `prodavan-ops e2e cleanup` — удаляет Job и `kubectl delete -k overlays/e2e`; с `--unsync` дополнительно удаляет Argo Application `agentscale-e2e`. Бутстрап sandbox-NS (`sandboxes-bootstrap.yaml`) cleanup намеренно **не** трогает.
- CI-шаг `Cleanup k8s e2e` (`if: always()`) делает то же самое плюс удаляет ConfigMaps с PR-тестами (`agentscale-e2e-conftest`, `agentscale-e2e-k8s-tests`).

---

## Связанные docs

- [`local-cluster-e2e.md`](local-cluster-e2e.md) — bootstrap кластера + smoke
- [`github-actions.md`](github-actions.md) — все workflows
- [`L09-vertical-integration.md`](../target/12-layer-docs/L09-vertical-integration.md)
- [`patterns-references.md`](../target/14-project-containers/k3s-runtime/patterns-references.md)
