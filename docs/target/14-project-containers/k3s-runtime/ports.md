# k3s Runtime — порты (Hexagonal)

Все порты — `typing.Protocol`, async. Реализации: `adapters/stub_*` и `adapters/k8s/*`.

## PodRuntimePort (lifecycle) — **existing, extend P2**

Текущий контракт ([`pod_runtime.py`](../../../../apps/api/src/prodavan/application/pod_service/ports/pod_runtime.py)):

| Method | Semantics | k8s mapping (P2) |
|--------|-----------|------------------|
| `ensure_running(runtime_ref)` | Idempotent start | Create Pod if NotFound; wait Ready |
| `pause(runtime_ref)` | Compute off, keep MinIO | Delete Pod, gracePeriod=30 |
| `terminate(runtime_ref)` | Hard teardown | Delete Pod, gracePeriod=0 |
| `get_status(runtime_ref)` | Actual phase | GET Pod; return `{phase, restarts, uid, node}` |
| `list_managed_pods()` | Reconcile inventory | LIST with label selector |

**Расширение P2 (optional on Protocol):**

```python
async def force_kill(self, *, runtime_ref: str) -> None: ...
async def wait_ready(self, *, runtime_ref: str, timeout_s: float) -> None: ...
```

`force_kill` уже вызывается через `hasattr` в `PodCommand` — зафиксировать в Protocol.

### PodSpec inputs (internal DTO, not Port)

Builder читает из `ProjectPodRow` + `ProjectRow`:

| Field | Source |
|-------|--------|
| `name` | `pod-{workspace_key}` or stored `runtime_ref` |
| `labels` | [k8s-contract.md](../k8s-contract.md) |
| `service_account` | `prodavan-sandbox` |
| `resources` | company quota / default profile |
| `image` | `settings.sandbox_image` |
| `env` | workspace_key, project_id (non-secret) |

Secrets (AI keys) — **не** через Pod spec на P2; inject per agent session ([isolation.md](../isolation.md)).

---

## HydratePort (workspace sync) — **existing**

| Method | Stub (now) | k8s (P2) |
|--------|------------|----------|
| `hydrate(workspace_key, runtime_ref)` | no-op log | Init Job or initContainer: MinIO → `/workspace` |

Bump `hydrate_generation` on rematerialize → next `ensure_running` must re-hydrate.

---

## PodFilesPort (P2 — new)

Hot file ops **без** полного re-hydrate (attachments, inbox push):

| Method | Use case | k8s mechanism |
|--------|----------|---------------|
| `copy_to_pod(local_path, remote_path, runtime_ref)` | Push attachment to `/workspace/inbox` | `kubectl cp` equivalent: exec + tar stream |
| `copy_from_pod(remote_path, local_path, runtime_ref)` | Pull artifact | same |
| `exec(runtime_ref, command, timeout_s)` | Health probe, one-off tool | `POST .../pods/{name}/exec` |

**Не** использовать для full workspace sync — только HydratePort.

---

## PodMetricsPort (P2 — new, read-only)

| Method | Returns |
|--------|---------|
| `get_pod_metrics(runtime_ref)` | CPU/RAM usage (metrics-server) |
| `get_pod_status_detail(runtime_ref)` | phase, conditions, restartCount, startedAt |
| `list_sandbox_metrics()` | Admin batch for `/admin/containers` |

Join с product metrics (`agent_usage`) — в query layer, не в k8s port.

---

## Error taxonomy (all ports)

| Class | Retry | Pod row status |
|-------|-------|----------------|
| `TransientK8sError` (timeout, 429) | yes, backoff | keep provisioning |
| `PermanentK8sError` (403, invalid spec) | no | `failed` + `pod.failed` |
| `NotFoundRuntimeRef` | recreate on ensure_running | — |

Map in `infrastructure/k8s/errors.py`; adapters translate API exceptions.
