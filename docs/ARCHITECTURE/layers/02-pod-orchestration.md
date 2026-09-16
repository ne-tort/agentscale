# 02 — Оркестрация контейнеров (Pod)

## Контекст

Проект = workspace + один (или сменяемый) Pod с AI-агентом. Оркестрация — **не** k8s operator: app-layer desired/observed + reconcile поверх bare Pod API (k3s). GitOps для платформы (Argo); sandbox Pod'ы создаёт API.

Сравнение с идеалом cloud-agent (E2B / Modal / Cursor Cloud sandboxes): CRD+controller, stable DNS, PVC, NetworkPolicy, durable async — у нас app-reconcile + emptyDir + pod IP.

## Текущая реализация (as-is)

### Domain

[`domain/pods/types.py`](../../../apps/api/src/prodavan/domain/pods/types.py):

- `PodStatus` — intent/DB: pending → provisioning → running → pausing/paused → failed → terminating/terminated.
- `PodDesiredState` — `absent` | `running` (пишет приложение).
- `ObservedState` — derived из live k8s (не intent): absent/preparing/…/running/degraded/failed/unknown/paused.
- `runtime_ref_for(workspace_key)` → DNS-имя `pod-<sanitized>` в k8s mode.

### Application: command / query / reconcile / observation

| Файл | Роль |
|------|------|
| [`pod_service/command.py`](../../../apps/api/src/prodavan/application/pod_service/command.py) | `sync_desired`, launch/pause/terminate/reload |
| [`pod_service/query.py`](../../../apps/api/src/prodavan/application/pod_service/query.py) | `runtime_view`, orphans |
| [`pod_service/reconcile.py`](../../../apps/api/src/prodavan/application/pod_service/reconcile.py) | Drift fix + zombie reaper |
| [`pod_service/runtime_observation.py`](../../../apps/api/src/prodavan/application/pod_service/runtime_observation.py) | observe / promote_or_demote / wait_for_running |
| [`container_env_loader.py`](../../../apps/api/src/prodavan/application/pod_service/container_env_loader.py) | env/secrets из module meta |
| [`workspace_service.py`](../../../apps/api/src/prodavan/application/pod_service/workspace_service.py) | FS в `/workspace` |

**`_apply_running`:** commit PG **до** k8s create (чтобы zombie reaper видел row) → `ensure_running` → hydrate (k8s: noop, реальная работа в initContainer) → статус остаётся PROVISIONING до async promote.

**Reload:** checkpoint → bump bridge gen → terminate → reset PENDING → `sync_desired(RUNNING)`.

**Reconcile:** drift sync; reprovision ACTIVE без live pod; `_reap_zombies` по label `prodavan.io/managed-by=pod-service` с grace на provisioning.

**Observation:** dual-budget pull vs ready; flap grace для ABSENT/UNKNOWN; metrics-server display-only.

### k8s adapters

- Port: [`ports/pod_runtime.py`](../../../apps/api/src/prodavan/application/pod_service/ports/pod_runtime.py)
- Adapter: [`adapters/k8s/pod_runtime.py`](../../../apps/api/src/prodavan/application/pod_service/adapters/k8s/pod_runtime.py) — recreate при смене hydrate_generation / Failed.
- Client: [`infrastructure/k8s/sandbox/client.py`](../../../apps/api/src/prodavan/infrastructure/k8s/sandbox/client.py) — httpx REST, без python k8s SDK.
- Spec: [`pod_spec.py`](../../../apps/api/src/prodavan/infrastructure/k8s/sandbox/pod_spec.py) — initContainer `hydrate` + main `agent-runtime`, **`emptyDir` workspace**, `restartPolicy: Never`, общий `serviceAccountName`.

### Identity / bridge / credentials

- [`pod_identity/bridge.py`](../../../apps/api/src/prodavan/application/pod_identity/bridge.py) — scoped JWT (`aud=prodavan-pod-bridge`), scopes `agent:events`, `internal:*`, `infra:*`, `module:<id>:*`; `bump_pod_bridge_generation`; ~~`_GEN_FALLBACK` in-memory~~ → Redis-only SoT в prod (`pod_identity_bridge_strict=True`), in-memory fallback только для dev/test (`strict=False`).
- [`openclaw_bridge.py`](../../../apps/api/src/prodavan/application/agent/openclaw_bridge.py) — HTTP на **pod IP**.
- [`credential_broker.py`](../../../apps/api/src/prodavan/application/agent/credential_broker.py) — lease AI keys в память sidecar (не env).

### Project lifecycle

[`project_service/command.py`](../../../apps/api/src/prodavan/application/project_service/command.py): launch / resume / reload / sync. Resume в k8s — **`asyncio.create_task`** (не Celery). Idle pause — отдельный sweep.

Ops-канон: [`docs/07-infrastructure/runbook.md`](../../07-infrastructure/runbook.md) — без compose-as-cluster / k3d / recover shell.

## Проблемы

### POD-P0a — gen-revocation race

**Приоритет:** P0  
~~`_GEN_FALLBACK: dict[str, int]` process-local. Без Redis / multi-replica API: bump на A не виден на B → отозванные JWT остаются валидны. Секрет подписки имеет fallback до `dev-pod-bridge-secret` (см. [04](04-api-infra-access.md)).~~  
**Исправлено:** gen теперь Redis-only SoT в strict mode (`pod_identity_bridge_strict=True`, по умолчанию); `_GEN_FALLBACK` используется только в dev/test (`strict=False`). `bump` fail-closed (503) при Redis-down — отозванные токены не остаются валидны из-за несинхронизированного in-memory dict. Signing secret fail-closed без `dev-pod-bridge-secret` fallback в prod. Остаток: при multi-replica + Redis-down новый minted токен (gen 0) валиден до первого bump — требует `redis_required=True` в prod (шаг рефакторинга 6 из слоя 04).

### POD-P0b — emptyDir workspace

**Приоритет:** P0  
Данные не переживают reschedule/delete. Каждый start = hydrate из object store. Checkpoint best-effort → риск потери mid-turn state агента.

### POD-P0c — ephemeral pod IP

**Приоритет:** P0  
Нет headless Service. Между recreate и re-resolve — `POD_NOT_RUNNING` / `BRIDGE_UNREACHABLE`. Retry в bridge покрывает `BRIDGE_SESSION_NOT_FOUND`, не всегда pod-down.

### POD-P1a — bare Pod Never

**Приоритет:** P1  
Нет auto-restart при OOM/CrashLoop между тиками reconcile. Окно недоступности = период beat.

### POD-P1b — non-durable resume/bootstrap

**Приоритет:** P1  
`asyncio.create_task` без persistent queue: рестарт API → `launch_phase=resuming` застревает. Trigger/wipe идут через Celery — асимметрия.

### POD-P1c — pause/terminate без wait

**Приоритет:** P1  
DB ставит PAUSED/TERMINATED сразу после `delete_pod`; pod может ещё жить в grace. Краткое окно расхождения desired/observed.

### POD-P1d — isolation на app-layer

**Приоритет:** P1  
Общий SA, общий namespace sandboxes, нет NetworkPolicy east-west, нет per-project ResourceQuota. JWT scopes не заменяют сетевую изоляцию (CLUSTER-GAPS I8).

### POD-P2a — reconcile session / N+1 metrics

**Приоритет:** P2  
~~Один `AsyncSession` на reconcile-проход с commit внутри sync без явного rollback на partial failure.~~  
**Частично исправлено:** `PodReconcileService.run()` теперь под PG advisory lock (`core/infra/advisory_lock.py`, `pg_try_advisory_lock`/`pg_advisory_unlock`) — multi-replica race (admin `/reconcile` vs Celery beat) устранён: второй проход получает `lock_held` и skip'ает. PG-backed (не Redis) — работает при Redis-down (PG = SoT). Partial failure изоляция уже была через try/except per pod (sync_desired/`_apply_running` commit'ит row до k8s create — осознанный design для zombie reaper). Общий helper `advisory_lock` переиспользуется trigger_worker (DRY).  
**Остаток:** N+1 metrics (`PodMetricsSampler.sample_managed_pods` каждый проход + `get_pod_metrics` на каждый Running observe) — backlog (batch metrics).

## Target-design

### Паттерн оркестрации

**Вариант A (прагматичный next):** сохранить app-layer reconcile, но дотянуть:

- Headless Service на каждый `runtime_ref` (stable DNS).
- PVC или remote FS mount вместо emptyDir (или snapshot+restore pipeline с SLA).
- Durable queue для resume/bootstrap/rematerialize.
- `wait_absent` перед финальным DB status.
- Redis-only gen (fail closed без Redis), без in-memory fallback в multi-replica.

**Вариант B (идеал cloud):** CRD `AgentSandbox` + controller (или Deployment/Job wrapper): status subresource, auto-restart, events; API пишет только desired CR.

Рекомендация аудита: **A сейчас**, дорожная карта к **B** когда sandbox-изолятор (I8) и multi-tenant densitу потребуют operator.

### Изоляция

- Per-project ServiceAccount + Role (минимум).
- NetworkPolicy: deny all east-west в `prodavan-sandboxes`; allow только API egress по allowlist.
- ResourceQuota / LimitRange per company или per project.
- Bridge JWT: RS256/JWKS, секрет из Vault (см. 04).

### Workspace

- Persist: PVC **или** обязательный checkpoint в object store перед любым delete + verify hydrate hash.
- Streaming archive (не 512 MB BytesIO) — см. 04.
- `hydrate_generation`: один SoT (label = PG после успешного create), явная транзакция «materialize complete → bump → recreate».

### Ответственность

| Компонент | Владеет | Не владеет |
|-----------|---------|------------|
| PodCommand | Desired transitions | Stream agent events |
| Reconcile | Drift + zombie GC | Business pause policy |
| Observation | Derived ObservedState | Writing desired |
| Runtime adapter | k8s CRUD | DB PodStatus (кроме ошибок порта) |
| Bridge bootstrap | Session register after Running | Materialize contents |

## Шаги рефакторинга

1. Убрать `_GEN_FALLBACK` в multi-replica; fail closed если Redis down; ротация secret без hardcoded fallback ([`bridge.py`](../../../apps/api/src/prodavan/application/pod_identity/bridge.py)).
2. Добавить headless Service в [`pod_spec` / adapter](../../../apps/api/src/prodavan/infrastructure/k8s/sandbox/); перевести bridge/workspace/credential clients на DNS.
3. Перенести resume/bootstrap на Celery (как wipe/triggers) в [`project_service/command.py`](../../../apps/api/src/prodavan/application/project_service/command.py).
4. `pause`/`terminate`: wait_absent (с timeout) → затем DB status; иначе `deleting` промежуточный статус.
5. NetworkPolicy + ResourceQuota манифесты в `infra/k3s` overlays; per-project SA если densitу позволяет.
6. Workspace: выбрать PVC **или** усилить checkpoint+verify; streaming tar в [`workspace_tar_download.py`](../../../apps/api/src/prodavan/application/pod_service/workspace_tar_download.py).
7. Reconcile: per-pod session/transaction; batch metrics; distributed lock на reconcile-проход.
8. Документировать решение A→B в PRODUCT / CLUSTER-GAPS; обновить L07 as-built.

## Ссылки

- Runbook: [`docs/07-infrastructure/runbook.md`](../../07-infrastructure/runbook.md)
- CLUSTER-GAPS: [`docs/09-checklists/CLUSTER-GAPS.md`](../../09-checklists/CLUSTER-GAPS.md)
- Pod service target notes: [`docs/target/14-project-containers/`](../../target/14-project-containers/)
