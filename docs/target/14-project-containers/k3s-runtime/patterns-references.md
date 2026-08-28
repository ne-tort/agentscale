# k3s Runtime — паттерны и референсы

## Паттерны проектирования

| Pattern | Применение в Prodavan | Альтернатива |
|---------|----------------------|--------------|
| **Hexagonal (Ports & Adapters)** | `PodRuntimePort` + `K8sPodRuntimeAdapter` | Прямой k8s в handlers |
| **Facade** | `PodCommand` / `PodQuery` для `project_service` | Разрозненные k8s вызовы |
| **Reconcile loop (level-triggered)** | `PodReconcileService` | Edge-triggered only |
| **Outbox** | `pod.*` events после commit PG | Dual-write Kafka+PG |
| **Idempotent commands** | `ensure_running` by `runtime_ref` | Create always new |
| **Sidecar / Init container** | Hydrate before sandbox start | Manual rsync |
| **Anti-corruption layer** | `infrastructure/k8s/` maps API errors → domain | Leak ApiException |
| **Strangler** | `POD_RUNTIME_MODE=stub\|k8s` | Big-bang cutover |

### Operator vs In-process adapter

| | **In-process adapter (P2 choice)** | **Kubernetes Operator (Kopf/Kubebuilder)** |
|--|-----------------------------------|---------------------------------------------|
| SoT | PostgreSQL `project_pods` | CRD `.spec` |
| Fit | Single API owns business rules | Many controllers, CRD-first |
| Complexity | Low | High (RBAC, deployment, upgrades) |
| When upgrade | >1k pods, self-healing without API | |

Prodavan: business lifecycle tied to **Project** entity — PG SoT правильнее CRD-only.

### Command vs Event

- **Command:** `sync_desired` — synchronous, transactional with project row
- **Event:** `pod.started` — notify downstream

Не смешивать (CQRS light): Kafka consumers read-only.

---

## Библиотеки и инструменты

### Python k8s clients

| Library | Notes | Link |
|---------|-------|------|
| **official kubernetes** | Sync + dynamic client; `kubernetes-asyncio` for async | https://github.com/kubernetes-client/python |
| **kr8s** | Async, ergonomic; good for workers | https://github.com/kr8s-org/kr8s |
| **kopf** | Operator framework (reference, not P2 default) | https://github.com/nolar/kopf |

**Recommendation P2:** `kubernetes-asyncio` or `kr8s` behind `infrastructure/k8s/sandbox/client.py` — swap without touching adapters.

### File copy

| Mechanism | Notes |
|-----------|-------|
| `kubernetes.stream.stream` + tar | Same as `kubectl cp` |
| **kubectl cp** (ops only) | Runbook debug, not API path |
| **S3 presigned** | Large files; agent pulls directly |

Ref: kubernetes-client/python `stream` examples — https://github.com/kubernetes-client/python/tree/master/examples

### Metrics

| Component | Role |
|-----------|------|
| **metrics-server** | Pod CPU/RAM (bundled in k3s) |
| **kube-state-metrics** | Optional Grafana dashboards |
| **Prometheus Operator** | Future cluster-wide |

### Hydrate / object storage

| Tool | Use |
|------|-----|
| **mc** (MinIO client) | initContainer image |
| **rclone** | S3-compatible sync |
| **aws-cli** | Alternative |

---

## GitHub reference projects

Примеры для изучения (не форк as-is):

| Repo | Что взять |
|------|-----------|
| [kubernetes-client/python](https://github.com/kubernetes-client/python) | Official API patterns, watch, exec stream |
| [kr8s-org/kr8s](https://github.com/kr8s-org/kr8s) | Async Pod create/delete |
| [nolar/kopf](https://github.com/nolar/kopf) | Reconcile loop structure if operator later |
| [jupyterhub/kubespawner](https://github.com/jupyterhub/kubespawner) | 1-user-1-pod lifecycle, spawn/stop |
| [coder/coder](https://github.com/coder/coder) | Workspace pods, lifecycle (Go, architecture ref) |
| [gitpod-io/gitpod](https://github.com/gitpod-io/gitpod) | Dev sandbox orchestration (complex) |
| [dagster-io/dagster-k8s](https://github.com/dagster-io/dagster-k8s) | Job-based init patterns |
| [ray-project/kuberay](https://github.com/ray-project/kuberay) | Operator CRD (overkill, good CRD design) |

**Closest analog:** JupyterHub Spawner model — map User→Project, single Pod sandbox, stop = delete Pod.

---

## Industry patterns (reading)

| Topic | Resource |
|-------|----------|
| K8s API conventions | https://kubernetes.io/docs/concepts/overview/working-with-objects/names/ |
| Controller pattern | https://kubernetes.io/docs/concepts/architecture/controller/ |
| Pod lifecycle | https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/ |
| Downward API | Expose labels to container without env sprawl |
| Network policies | Already in [isolation.md](../isolation.md) |

---

## Testing strategy (P2)

| Level | Marker | Approach |
|-------|--------|----------|
| Unit | — | Mock `PodRuntimePort`; test `PodCommand` state machine |
| Integration | `integration` | TestClient + Docker Postgres + `POD_RUNTIME_MODE=stub` (nightly) |
| K8s runtime | `k8s` | In-cluster Job: TestClient + `POD_RUNTIME_MODE=k8s` → real Pods in `prodavan-sandboxes` |
| Live API | `live` | HTTP pytest against dev Traefik `:8088` |
| Contract | — | Label selector matches [k8s-contract.md](../k8s-contract.md) |

Canonical runbook: [`docs/07-infrastructure/e2e.md`](../../../07-infrastructure/e2e.md).

Stub integration stays default on dev API; k8s/live run via opt-in CI (`label: e2e`) or `prodavan-ops e2e run`.
