# 04 — Backend: миграция FastAPI

## 4.1 Что остаётся (контракты стабильны)

| Слой | Что | Почему |
|---|---|---|
| Домен | `PodDesiredState` (RUNNING/ABSENT), `PodStatus`, `project_pods` таблица | бизнес-контракт выше рантайма |
| Команда | `PodCommand.sync_desired` сигнатура + event-flow (`pod.*` события) | ProjectCommand и idle_pause не меняются |
| Ports | `PodRuntimePort`, `HydratePort`, `PodFilesPort`, `PodMetricsPort` | hexagonal — меняем адаптеры |
| Identity | `pod_identity/bridge.py` mint/verify/gen | 02 |
| Pod-surface | `main_pod.py` (кроме workspace-archive), `api/internal/pods.py` (credentials) | 02 |
| Agent | `session_service` (PG SoT), SSE-протокол `bridge_envelope_*` | фронт не меняем |
| Project | `ProjectCommand` launch/pause/resume/reload/sync | только внутренности PodCommand |

`PodDesiredState.PAUSED` semantics: в 01 определено `ABSENT → patch operatingMode=Suspended` — домен получает **третью опцию рантайм-действия** (не новый desired-state). `sync_desired` решает по terminate-reason (см. 4.3).

## 4.2 Новый адаптер: `AgentSandboxRuntimeAdapter`

`application/pod_service/adapters/sandbox/runtime.py` (новый), реализует `PodRuntimePort`:

```python
class AgentSandboxRuntimeAdapter(PodRuntimePort):
    def __init__(self, client: AsyncSandboxClient, router_url: str, ns: str,
                 warmpool: str, shutdown_ttl_sec: int, k8s: K8sCustomObjects): ...

    async def ensure_running(self, *, runtime_ref: str, context: PodRuntimeContext) -> None:
        # claim по label prodavan.io/pod-id (list_sandboxes label_selector)
        # если живой Sandbox c operatingMode=Running → return (идемпотентно)
        # если Suspended → patch operatingMode=Running
        # если нет → client.create_sandbox(warmpool, ns, pod_labels={prodavan.io/*},
        #          shutdown_after_seconds=ttl)  # watch Ready внутри SDK
        # затем: _bootstrap_identity(sandbox, context)  # 02: push Bridge JWT
        #        _seed_workspace(sandbox, context)      # 4.5: перенос tar при миграции/legacy
    async def pause(self, *, runtime_ref: str) -> None:
        # patch sandbox spec.operatingMode=Suspended; ждать Suspended=True (timeout)
    async def terminate(self, *, runtime_ref: str) -> None:
        # удалить SandboxClaim → каскад (Sandbox+PVC+Service); shutdownPolicy Delete
    async def force_kill(self, *, runtime_ref: str) -> None:
        # terminate + (dev) grace-хак не нужен — пода нет
    async def get_status(self, *, runtime_ref: str) -> dict[str, Any]:
        # из claim/sandbox status: conditions → старый словарь-формат
        # {phase: map_conditions(...), pod_ip: status.podIPs[0], node: nodeName, ...}
    async def list_managed_pods(self) -> list[dict[str, Any]]:
        # list sandboxes по label prodavan.io/managed-by (гипер-набор для admin)
```

- Claim-имя/лейблы: claim генерит SDK (`sandbox-claim-…`), но Sandbox-объект получает **pod_labels** `prodavan.io/pod-id/project-id/company-id/workspace-key` → все k8s-объекты (Pod/Service/PVC) маркированы как раньше (админ-UI, NetworkPolicy, reconcile).
- `runtime_ref` в PG остаётся `pod-{workspace_key}` — **но теперь это label-ключ**, а имя объекта = sandbox-name (хранить в `project_pods.runtime_ref`? — нет: `runtime_ref` = label `prodavan.io/pod-id`-совместимый ключ; отдельное поле не добавляем, label-lookup по list). Вариант: хранить `sandbox_name` в существующем `runtime_ref` — проще: `runtime_ref := sandbox_name` после первого ensure. Решение в Phase 1 (см. 07): **runtime_ref → sandbox_name (CR name)**, поиск по нему.

### Условия → старые observed_state (контракт фронтa!)

```python
# adapters/sandbox/conditions.py
def observed_state_from_conditions(conds, phase_pod) -> ObservedState:
    # Ready=True/DependenciesReady → RUNNING
    # Ready=False/SandboxSuspended → PAUSED (not FAILED!)         ← см. 05
    # Suspended=True/PodTerminating → PAUSING
    # PodScheduled=False + Unschedulable → PROVISIONING (sticky FAILED после таймаута — оставляем промоут/demote)
    # claim Wait/NotFound при desired RUNNING → PREPARING
    # Pod Pending/ContainerCreating → PROVISIONING (pulling-различие умирает: IfNotPresent + warm = нет pull-фазы)
    # Ready=False/ReconcilerError|PodFailed → FAILED
```

**Словарь `ObservedState` сохраняем целиком** (фронт зависит, см. 05) — маппер транслирует conditions в те же строки. `PULLING/HYDRATING` в норме не встречаются; сохранены для legacy-стабов.

## 4.3 PodCommand — точечные правки

- `_apply_running()`: `runtime.ensure_running()` (внутри — claim+watch+bootstrap); `hydrate.hydrate()` → **новая семантика**: `SandboxHydrateAdapter.hydrate()` = seed-проверка (если PVC пуст и есть tar в MinIO — распаковать; нет — no-op). Событие `pod.hydrated` эмитится после успешного seed (awaiting_verification=False — PVC-модель верифицируется самим фактом Ready).
- `_apply_absent()` (pause): вместо `pause()→PAUSED` → `runtime.pause()` (suspend) → PAUSED. **`purge_project_tenant_infra` НЕ вызываем** (сегодня pause хранит tenant infra — semantics сохранены).
- `_apply_terminate()`: `terminate()` → claim-delete каскад + `purge_project_tenant_infra()` как раньше.
- `_prepare_reload()` (reload): checkpoint (dehydrate-snapshot, 4.5) → **terminate + create** (новый sandbox = свежий образ из шаблона, PVC… — **PVC переживает terminate? НЕТ**: claim-delete каскадит PVC. Reload ⇒ новый PVC ⇒ seed из MinIO-snapshot. Это осознанно: reload = «обновить образ + чистый workspace из MinIO»). bump gen остаётся (ревокация bridge).
- `_mint_pod_bridge_token()` — остаётся, но не уходит в Pod-спеку: в `PodRuntimeContext.pod_auth_token` (контекст тот же) → адаптер пушит post-Ready (02).
- Идемпотентность `_status_matches_desired`: PAUSED → `operatingMode=Suspended && Suspended=True`; RUNNING → `Ready=True`.

## 4.4 Удаляемый код (полный список)

| Файл | Действие |
|---|---|
| `infrastructure/k8s/sandbox/pod_spec.py` | **удалить** |
| `infrastructure/k8s/sandbox/client.py` (K8sSandboxClient) | **удалить** (SDK + CustomObjectsApi для patch) |
| `infrastructure/k8s/auth.py` | **удалить** (SDK own config; in-cluster — k8s-helper SDK) |
| `infrastructure/k8s/errors.py` | оставить (контракт ошибок адаптеров), транслировать SDK-искл. |
| `infrastructure/k8s/sandbox_jobs.py` | оставить (MCP/PVC-probe Jobs — вне scope), пометить legacy |
| `core/infra/k8s_manager.py` + wiring | заменить на `SandboxClientResource` (lifespan: SDK-client + CustomObjects) |
| `application/pod_service/adapters/k8s/pod_runtime.py` | **удалить** (заменён sandbox/) |
| `adapters/k8s/{dehydrate,workspace_exec*,metrics,exec_errors}.py` | **удалить**; dehydrate → `adapters/sandbox/snapshot.py` (SDK files); metrics → `adapters/sandbox/metrics.py` (metrics-server через новый тонкий клиент ИЛИ SDK не умеет → оставить metrics.k8s.io read через kubernetes_asyncio, уже в зависимостях SDK) |
| `reconcile.py` | **вырезать** zombie-reaper + drift (контроллер делает); оставить только `PodMetricsSampler`-запуск + runtime-health sync (observation через get_status — дёшево, conditions из кэша) |
| `runtime_observation.py` | `_observe_k8s` → `_observe_sandbox` (conditions); promote/demote оставить (flap-grace полезен) |
| `factory.py` | режим `sandbox` (default после стабилизации), `k8s` удаляем после Phase N |
| `agent/openclaw_bridge.py` | `_resolve_pod_ip` → router URL + X-Sandbox-ID (headers инжектит connector SDK; для ручных httpx — самим) |
| `config/settings.py` | блоки по 3.6 |
| `api/internal/pods.py::workspace-archive` + `runtime/hydrate.py` + `workspace_tar_download.py` | удалить в финальной фазе (после того как seed-snapshot через SDK стабилен) |

## 4.5 Workspace: PVC + snapshot-модель

- **Перенос данных (миграция существующих проектов)**: при первом ensure_sandbox: если `workspace_object_key` в MinIO непуст и PVC свежий → `K8sSandboxSeedAdapter.seed()`: скачать tar (`workspace_tar_download.py` — код живёт, переиспользуется) → `sandbox.files` upload (POST /upload через router) в `/workspace`. Это «мигрантский» путь; после миграции всех проектов — только для новых/legacy-стабов.
- **Dehydrate-snapshot**: после turn (существующий `_checkpoint_workspace_after_turn`) — не на каждый ход (дорого через files-API на больших WS), а **по расписанию/событию**: (а) перед terminate/reload (checkpoint, как сегодня), (б) периодический snap (daily/6h, tenant-infra воркер). Снапшот = tar через `sandbox.files.read_to` стримом → `workspace_tar_upload.py` → MinIO (last-good, дельта-удаление как сегодня). MinIO — disaster-recovery и источник для «пересоздать проект с нуля».
- **Миграционный сценарий**: pause старого пода (checkpoint в MinIO уже есть от старого рантайма) → переключить POD_RUNTIME_MODE=sandbox → resume → seed из MinIO → жить на PVC.

## 4.6 Session bootstrap: `openclaw_bridge.py`

- `register_session` / `_patch_adapter_state` / `_stream_send` / approvals: **URL меняется** с `http://{pod_ip}:3921/...` на router: base `http://sandbox-router-svc...:8080`, headers `X-Sandbox-ID: {sandbox_name}`, `X-Sandbox-Namespace: prodavan-sandboxes`, `X-Sandbox-Port: "3921"`, `Authorization: Bearer {pod_agent_runtime_token}` (платформенный, как сегодня). Router стрипает Authorization **перед форвардом** — runtime-эндпоинты сегодня не проверяют Bearer (кроме /v1/identity каналов) — **проверить при реализации**: если runtime проверяет Bearer — передавать через кастомный header `X-Prodavan-Runtime-Token` (router пропускает X-*), иначе пустой.
- `pod_session_bootstrap.py`: после resume (watch Ready) — реактивация сессий (unchanged логика, другой transport).
- Retry: router делает 3 dial-ретрая (200/400/800ms) — наш `BRIDGE_UNREACHABLE`-меппинг остаётся.

## 4.7 Модели БД

- `project_pods`: без миграции схемы в первой волне. `runtime_ref` = sandbox_name (заполняется адаптером). `hydrate_generation` = счётчик seeds (продолжает использоваться reload/sync-логикой). `desired_state` — unchanged.
- Alembic: новая миграция **не нужна** (поля совпадают); в Phase 8 — опционально `sandbox_name` индекс/`launch_type` (warm/cold) для метрик.
- e2e: `tests/e2e/k8s/` — адаптировать fixtures (базовый тест lifecycle: create claim → Ready → suspend → resume → delete), см. 07.

## 4.8 Ошибки

- `TransientK8sError` ← router 502/timeout, SDK watch-timeout (backoff — у роутера есть, у SDK requeue).
- `PermanentK8sError` ← claim rejected (Disallowed env/VCT — не должны слать), InvalidConfiguration (имя > 63).
- `NotFoundRuntimeRef` ← sandbox NotFound → recreate (ensure идемпотентен).
- `POD_NOT_RUNNING` на dehydrate-snapshot → skip снапшот (suspend-под: files недоступны — снапшот только Running).
