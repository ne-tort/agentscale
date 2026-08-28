# P2 — k3s Runtime (real Pod adapter)

| Поле | Значение |
|------|----------|
| Priority | **P2** (после P1 stub complete; блокирует **P-POD-01** real k8s) |
| Canon | [14-project-containers/k3s-runtime/](../14-project-containers/k3s-runtime/) |
| Depends on | P1 `pod_service` merged, GitOps sandbox base, MinIO materialize |
| Status | `planned` |

## Цель

Заменить `StubPodRuntimeAdapter` / `StubHydrateAdapter` на **реальные k8s adapters** без нового BC: create/pause/delete Pod, hydrate, file copy, metrics, reconcile zombies.

## Non-goals

- Full Kubernetes Operator / CRD
- Multi-cluster scheduling
- Production HPA / cluster autoscaling

## Фазы и PR-разрез

### Phase 1 — Infrastructure client + RBAC ✅ doc / 🔲 code

| Item | DoD |
|------|-----|
| `infrastructure/k8s/sandbox/client.py` | Async client, in-cluster + kubeconfig |
| `core/infra/k8s_manager.py` | LifespanResource wired in API |
| GitOps RBAC Role | create/delete pods, jobs, exec ([gitops-rbac.md](../14-project-containers/k3s-runtime/gitops-rbac.md)) |
| Labels `pod_*` | [k8s-contract.md](../14-project-containers/k8s-contract.md) applied in manifests |

**PR title:** `feat(pods): k8s client and sandbox RBAC for pod-service`

### Phase 2 — PodRuntimePort k8s adapter

| Item | DoD |
|------|-----|
| `K8sPodRuntimeAdapter` | ensure_running, pause, terminate, get_status, list_managed_pods |
| PodSpec builder | image, resources, labels, SA, readiness |
| `POD_RUNTIME_MODE=k8s` | feature flag; default stub in dev |
| Unit tests | mock k8s API |
| Integration test | optional k3s job in CI (skipped locally) |

**PR title:** `feat(pods): K8sPodRuntimeAdapter lifecycle`

### Phase 3 — Hydrate + files

| Item | DoD |
|------|-----|
| `K8sHydrateJobAdapter` or initContainer | MinIO → `/workspace` |
| `hydrate_generation` bump on rematerialize | recreate Pod |
| `PodFilesPort` + k8s exec/tar | hot copy inbox |
| Events | `pod.hydrated` after real sync |

**PR title:** `feat(pods): k8s hydrate and PodFilesPort`

### Phase 4 — Metrics + reconcile hardening

| Item | DoD |
|------|-----|
| `PodMetricsPort` | metrics-server CPU/RAM |
| Extend `GET /projects/{id}/runtime` DTO | phase, restarts, usage |
| Reconcile | zombie delete, drift metrics |
| Admin containers | batch metrics optional |

**PR title:** `feat(pods): pod metrics and reconcile metrics`

### Phase 5 — Verify Dev e2e

| Item | DoD |
|------|-----|
| Dev cluster | lazy start creates real Pod |
| Pause/resume | Pod deleted / recreated |
| Verify Dev smoke | project trigger → pod running |

**PR title:** `test(pods): k8s e2e on Verify Dev`

## DoD (P2 complete)

- [ ] `POD_RUNTIME_MODE=k8s` on dev cluster creates Pod in `prodavan-sandboxes`
- [ ] Pause deletes Pod; resume creates new Pod + hydrate
- [ ] No k8s imports outside `infrastructure/k8s` and `adapters/k8s`
- [ ] RBAC least-privilege; no cluster-admin
- [ ] `pod.*` events unchanged contract
- [ ] Gap **P-POD-01** → `done` (real Pod)
- [ ] As-built card in [12-layer-docs](../12-layer-docs/) Quality ≥ 8

## Контракты

| Port | Doc |
|------|-----|
| PodRuntimePort | [ports.md](../14-project-containers/k3s-runtime/ports.md) |
| HydratePort | [file-sync.md](../14-project-containers/k3s-runtime/file-sync.md) |
| PodFilesPort | [file-sync.md](../14-project-containers/k3s-runtime/file-sync.md) |
| PodMetricsPort | [metrics-observability.md](../14-project-containers/k3s-runtime/metrics-observability.md) |

## Риски

| Risk | Mitigation |
|------|------------|
| API crash mid-provision | reconcile + stale `provisioning` timeout |
| Hydrate slow | async Job + poll; UI shows provisioning |
| k3s dev drift | GitOps only; no manual kubectl on shared |
| Secret leak in Pod spec | keys via agent session, not env dump |
