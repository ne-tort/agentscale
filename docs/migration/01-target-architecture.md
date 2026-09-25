# 01 — Целевая архитектура

## Компоненты (после миграции)

| Компонент | Чем становится | Namespace |
|---|---|---|
| Под агента проекта | `Sandbox` CR (adopt из `SandboxWarmPool`), Pod из `SandboxTemplate`, PVC `workspace` | `prodavan-sandboxes` |
| Оркестратор подов | **agent-sandbox-controller** (вендор, v1.0.2) + extensions (Claim/Template/WarmPool) | `agent-sandbox-system` |
| Канал к поду | **sandbox-router** (`sandbox-router-svc:8080`, header-based `X-Sandbox-*`) | `agent-sandbox-system` |
| API (:8000/:8001) | FastAPI; :8000 — полный, :8001 — pod-only surface (остаётся) | `prodavan` |
| SDK | `k8s-agent-sandbox[async]` (AsyncSandboxClient, DirectConnectionConfig на router) | — |
| Workspace | PVC RWO `workspace` в SandboxTemplate; MinIO — snapshot/backup | — |
| Identity | Bridge JWT (HS256+gen Redis) — контракт сохранён, доставка post-Ready | — |

## Маппинг концепций prodavan → agent-sandbox

| prodavan сейчас | agent-sandbox | Примечание |
|---|---|---|
| `build_pod_body` + `create_pod` (httpx) | `SandboxTemplate` `prodavan-agent` + `SandboxClaim` | шаблон в Git, claim'ы из API |
| emptyDir `/workspace` + initContainer hydrate | `volumeClaimTemplates: workspace` (PVC) | персистентность natively |
| `sync_desired(RUNNING)` | claim create + watch `Ready` (adoption ~2 с из пула) | `ensure_running` → `create_sandbox` |
| `sync_desired(ABSENT)` (pause) | patch `spec.operatingMode: Suspended` | **не** delete; PVC выживает |
| terminate / force_kill | claim delete (каскад: Sandbox+PVC+Service) | `shutdownPolicy` |
| reload (bump generation + recreate) | claim delete + new claim **или** patch (если только env) |详见 04 |
| Bridge JWT в env Pod-спеки | post-Ready push токена в agent-runtime | 详见 02 |
| `_resolve_pod_ip` per message | router `X-Sandbox-ID` → pod (IP-cache) | стабильный адрес |
| runtime_observation poll | claim status conditions (`Ready`, `Suspended`) + watch | событийно |
| zombie-reaper reconcile | controller reconcile (own CR) | удалить из API |
| cluster-heal CronJob | не нужен (controller requeue) | удалить |
| hydrate_generation label + recreate | PVC не требует re-hydrate; «sync project» = files-API push diff |详见 04 |
| dehydrate tar → MinIO | snapshot PVC → MinIO (по расписанию/перед delete) | backup, не lifecycle |
| метрики metrics-server poll | `PodMetricsPort` оставить (metrics-server жив) + метрики controller/router | 04 |
| idle_pause sweep | sweep патчит `operatingMode` (тот же ProjectIdlePauseService) | 06 |
| HPA/масштабирование (нет) | HPA на `SandboxWarmPool` по `agent_sandbox_claim_creation_total` | 06 |

## Потоки данных (после)

### 1. Launch (UI → running)

```text
Flutter → POST /projects/{id}/launch (API:8000)
  → ProjectCommand.launch (unchanged business logic)
  → PodCommand.sync_desired(RUNNING)                    [контракт не меняется]
    → AgentSandboxRuntimeAdapter.ensure_running()
       → AsyncSandboxClient.create_sandbox(warmpool="prodavan-agent-pool",
           namespace="prodavan-sandboxes",
           labels={prodavan.io/project: id, ...},
           shutdown_after_seconds=TTL_страховка)
       → watch claim Ready (~2 s warm / холоднее при cold)
    → post-Ready: push Bridge JWT + workspace seed (files API / HTTP bridge)
  → observed_state: preparing → provisioning → running
  → pod.started event, PG `project_pods` строка (как раньше)
```

### 2. Чат

```text
Flutter → POST /projects/{id}/chat/stream (API:8000, Bearer OIDC)
  → AgentSessionService.iter_send_events
  → OpenClawBridgeBootstrap._stream_send
    → http://sandbox-router-svc:8080 + X-Sandbox-ID: <sandbox>
       X-Sandbox-Port: 3921, Authorization: Bearer <pod_agent_runtime_token>
    → router → pod:3921 (IP-cache fast path) → SSE обратно
  → PG транскрипт (unchanged)
```

### 3. Pod → платформа (:8001)

```text
agent-runtime:3921 → http://prodavan-api.prodavan.svc:8001/api/v1/...
  Authorization: Bearer <Bridge JWT>
  NetworkPolicy prodavan-sandboxes → ns prodavan TCP 8001 (только)
  → get_agent_auth → verify_pod_bridge_token (gen в Redis)
```

### 4. Pause / Resume

```text
pause:  ProjectCommand.pause → PodCommand.sync_desired(ABSENT, non-terminate)
        → patch Sandbox operatingMode=Suspended
        → wait Suspended condition (pod убит, PVC жив)
resume: sync_desired(RUNNING) → patch operatingMode=Running
        → wait Ready → pod_session_bootstrap (реактивация сессий)
```

## SandboxTemplate (канон, ns prodavan-sandboxes)

```yaml
apiVersion: extensions.agents.x-k8s.io/v1beta1
kind: SandboxTemplate
metadata:
  name: prodavan-agent
  namespace: prodavan-sandboxes
spec:
  podTemplate:
    metadata:
      labels:
        app.kubernetes.io/part-of: prodavan
        prodavan.io/managed-by: agent-sandbox   # NB: см. 03 — миграция селекторов
    spec:
      serviceAccountName: prodavan-project-pod
      automountServiceAccountToken: false       # default от template
      containers:
      - name: agent-runtime
        image: ghcr.io/ne-tort/prodavan-agent-runtime:<version>   # НЕ :latest
        imagePullPolicy: IfNotPresent
        ports: [{containerPort: 3921, name: http}]
        readinessProbe: {httpGet: {path: /health, port: 3921}}
        livenessProbe:  {httpGet: {path: /health, port: 3921}}
        resources: {requests: {cpu: 100m, memory: 256Mi},
                    limits:   {cpu: "1",  memory: 1Gi}}
        volumeMounts: [{name: workspace, mountPath: /workspace}]
      volumes: []   # workspace — из volumeClaimTemplates
  volumeClaimTemplates:
  - name: workspace
    spec: {accessModes: [ReadWriteOnce], resources: {requests: {storage: 5Gi}}}
  service: true          # стабильный DNS <sandbox>.prodavan-sandboxes.svc
  envVarsInjectionPolicy: Disallowed       # claims с env → reject (не cold start!)
  volumeClaimTemplatesPolicy: Disallowed   # claims с VCT → reject
  networkPolicyManagement: Unmanaged       # своя NetworkPolicy в Argo (03)
---
apiVersion: extensions.agents.x-k8s.io/v1beta1
kind: SandboxWarmPool
metadata:
  name: prodavan-agent-pool
  namespace: prodavan-sandboxes
spec:
  replicas: 2
  updateStrategy: {type: Recreate}
  sandboxTemplateRef: {name: prodavan-agent}
```

Ключевые решения шаблона:
- `envVarsInjectionPolicy: Disallowed` + `volumeClaimTemplatesPolicy: Disallowed` — claims **громко отвергаются** при попытке env/VCT вместо тихого cold-start. Per-project кастомизация только через `labels` (→ селектор NetworkPolicy/админ-UI). Это осознанный выбор hermes-паттерна: секундная latency важнее гибкости спеки.
- `service: true` — хотя трафик идёт через router, DNS полезен для wake-on-connect и деградации.
- **Управляемый NetworkPolicy оставляем свой** (`Unmanaged`): наша модель egress (:8001-only) специфичнее secure-default контроллера (ingress-from-router + internet-egress).

## Что удаляем (см. 04/03 детально)

API-код: `infrastructure/k8s/sandbox/{client,pod_spec}.py`, `auth.py`, `core/infra/k8s_manager.py`, zombie-reaper, `runtime_observation` k8s-ветки, `_resolve_pod_ip`.
Infra: `rbac-sandboxes.yaml` (Role prodavan-pod-service), probe-job, hydrate-secret, cluster-heal, `POD_SANDBOX_*` из ConfigMap.
