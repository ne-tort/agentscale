# 07 — План реализации (фазы, PR-разрез)

Формат поставки — канон репо: **PR → CI Gate → auto-merge → CI Images → Argo → Verify Dev**. Каждая фаза = отдельные PR'ы, фиче-флаг `POD_RUNTIME_MODE=sandbox` переключает адаптер; откат = revert env.

## Wave 0 — Инфраструктура (devops)

**PR-1: vendored agent-sandbox + App**
- `infra/agent-sandbox/install/sandbox-with-extensions-v1.0.2.yaml` (vendored upstream) + router deploy (patches: cache-enabled, replicas, resources)
- `infra/argocd/apps/agent-sandbox.yaml` (App, wave 0), AppProject destinations
- Terraform gitops-bootstrap: ожидание Healthy agent-sandbox
- Verify: `kubectl get pods -n agent-sandbox-system` через ops validate; CRD applied
- Не трогает prodavan-стек — **безопасный PR**

**PR-2: SandboxTemplate + WarmPool + RBAC**
- `infra/k3s/base/prodavan-sandboxes-ks/`: template (image pinned tag), warmpool (replicas 2 dev), Role `prodavan-sandbox-claims` + RoleBinding SA prodavan-api
- NetworkPolicy новая (ns-wide selectors, ingress from agent-sandbox-system + prodavan)
- Удалить: rbac-sandboxes (pods-CRUD Role), probe-job, hydrate-secret, cluster-heal
- ConfigMap: `POD_SANDBOX_ROUTER_URL`, `POD_SANDBOX_WARMPOOL`, `POD_SANDBOX_SHUTDOWN_TTL_SEC`
- AppProject whitelist: + Sandbox/Claim/Template/WarmPool; orphaned ignore
- Verify: `kubectl apply claim` (вручную ops-CLI dev) → Ready ~2s; PVC создан; NetworkPolicy блокирует egress кроме :8001
- **Rollback**: удалить слой — старый рантайм (k8s-режим) не задет

## Wave 1 — Backend: адаптер за фиче-флагом

**PR-3: SDK-клиент + ресурс lifespan**
- `pyproject.toml`: `k8s-agent-sandbox[async]`
- `core/infra/sandbox_client.py`: `SandboxClientResource` (AsyncSandboxClient, DirectConnectionConfig(router_url), cleanup); wiring (main + main_pod: pod-surface НЕ нужен k8s — router доступен из ns prodavan)
- settings: `POD_RUNTIME_MODE` enum {stub,k8s,sandbox}, `POD_SANDBOX_ROUTER_URL`, `POD_SANDBOX_WARMPOOL`
- Unit: мок SDK; e2e k8s-марк: create/terminate claim через адаптер

**PR-4: AgentSandboxRuntimeAdapter (Runtime)**
- `adapters/sandbox/{runtime.py,conditions.py}` (04.2); factory: mode=sandbox
- `runtime_ref` → sandbox_name; `list/get_status/terminate/pause/force_kill/ensure_running`
- Проверки: e2e `tests/e2e/k8s/test_sandbox_lifecycle.py`: ensure→Ready, suspend→Suspended, resume→Ready, terminate→gone(PVC deleted)
- **Токен-механика пока**: `PodRuntimeContext.pod_auth_token` остаётся (env-путь мёртв — Disallowed; bootstrap-пуш PR-5)

**PR-5: Identity push (02) + openclaw_bridge через router**
- agent-runtime: эндпоинт `POST /v1/identity` + refresh (токен в памяти; **prodavan-claw репо — координация: PR в claw, bump submodule**)
- API: `_bootstrap_identity` post-Ready; bridge-token не в Pod-спеке (env уже не пишем)
- `openclaw_bridge.py`: base-url=router + `X-Sandbox-*` headers (проверить Authorization-стрип роутера — 04.6)
- e2e: чат «hello» через sandbox-под; 401→refresh-cycle

**PR-6: observation + command-правки**
- `_observe_sandbox` (conditions→ObservedState); `PodCommand._apply_absent`=suspend; `_prepare_reload`=terminate+recreate+seed; reconcile: вырезать reaper/drift
- frontend-контракт: `runtime.stub=false` всегда; двойное чтение полей сохранить
- Verify Dev: полный сценарий из UI (launch → чат → pause → resume → files)

## Wave 2 — Workspace + чистка

**PR-7: workspace seed/snapshot**
- `adapters/sandbox/seed.py` (MinIO tar → files upload, переиспользуем workspace_tar_download), `snapshot.py` (files read_to → tar → MinIO)
- checkpoint-логика: перед terminate/reload + периодический
- Миграция live-проектов: script/ops `prodavan-ops pod-migrate --project X` (pause → mode=sandbox → resume → seed check)
- Удалить (после Verify): `api/internal/pods.py::workspace-archive`, `runtime/hydrate.py`, `workspace_tar_download.py` — **в этом же PR нельзя** (seed их использует) → отдельный PR-8 после стабилизации
- conversation_rehydrate-удаление: отдельный PR

**PR-8: удаление легаси-клиента (после 2+ недель стабилизации)**
- Удалить: `infrastructure/k8s/{auth, sandbox/client, sandbox/pod_spec}.py`, `adapters/k8s/*`, `k8s_manager`, `POD_RUNTIME_MODE=k8s` ветку, `pod_spec`-зависимые settings
- settings: sandbox=default; документация env-matrix обновить

## Wave 3 — Frontend

**PR-9: контейнерный контракт + ошибки-коды** (05.3)
- типизированная `ContainerRuntime` модель, удалить object-ws/stub-логику, `code`-матчинги 409/422
- chat auto-retry (1 повтор) + l10n `chatReconnecting`
- poll timeout 12→3 мин

**PR-10: чистка prodavan_api.dart** (05.4, опционально после PR-9)

## Wave 4 — Опциональные усиления (после стабилизации)

**PR-11: квоты per-company** — шаблоны `prodavan-agent-{small,standard,large}` + пулы (06.3)
**PR-12: масштабирование** — ops-CLI pool-autoscale CronJob / HPA (06.4)
**PR-13: idle-expire** (`idle_expire_days` → terminate+snapshot)

## Порядок и параллельность

```
PR-1 ─┬─► PR-2 ─┬─► PR-3 ─► PR-4 ─► PR-5 ─► PR-6 ─► PR-7 ─► (стабилизация) ─► PR-8
      │         │                              │
      │         └─ frontend может стартовать после PR-6 (контракт стабильный)
      └─ claw PR (identity endpoint) параллельно PR-3..4
```

## Откат

| Фаза | Откат |
|---|---|
| Wave 0 | удалить App/слой; старый рантайм работает |
| Wave 1 | `POD_RUNTIME_MODE=k8s` env → старый адаптер (до PR-8) |
| После PR-8 | миграция необратима; откат = revert PR-8 + redeploy |
| Frontend | revert PR; контракт совместим в обе стороны (double-read) |

## DoD волны (Verify Dev чек-лист)

- Wave 0: claim из ops-CLI → Ready ≤ 5 с; egress-политика: `wget minio:9000` из сандбокса — Timeout (запрещено), `curl :8001` — 401 (достигается)
- Wave 1: launch из UI ≤ 5 с warm; pause→resume ≤ 10 с; чат стабилен 100 сообщений; `kubectl get pods prodavan-sandboxes` — нет orphan-подов после terminate
- Wave 2: файл агента в /workspace выживает pause; MinIO-снапшот виден; миграция тест-проекта зелёная
- Wave 3: UI-сценарии 05.7
