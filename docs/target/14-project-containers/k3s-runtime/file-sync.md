# k3s Runtime — синхронизация файлов

Workspace content lives in **MinIO** ([15-content-storage](../../15-content-storage/)); Pod receives a **materialized copy** at `/workspace`.

## Стратегии hydrate (P2)

| Mode | Когда | Mechanism | Pros / Cons |
|------|-------|-----------|-------------|
| `initContainer` | Default P2 | Sidecar in Pod spec pulls S3 prefix before main container starts | Simple; re-create Pod on rematerialize |
| `batch_job` | Large workspaces | Separate Job mounts emptyDir + sync; Pod waits Job | Parallel; more RBAC |
| `exec` (hot) | Small patches | `PodFilesPort.copy_to_pod` | No recreate; not for full tree |

**Stub (now):** `StubHydrateAdapter` logs + emits `pod.hydrated` immediately.

## initContainer flow (recommended)

```text
Pod create
  initContainer: prodavan-hydrate
    env: WORKSPACE_KEY, MINIO_ENDPOINT, credentials from K8s Secret
    command: hydrate-cli sync s3://... → /workspace
  mainContainer: prodavan-sandbox
    volumeMount: /workspace (emptyDir shared with init)
```

Image `prodavan-hydrate` — thin wrapper around same logic as API `materialize` (shared lib target).

## Rematerialize

Trigger: project materialize / content version bump.

1. Bump `project_pods.hydrate_generation`
2. If Pod running:
   - **Option A (P2):** delete + recreate Pod (simplest, matches pause/resume)
   - **Option B:** run one-off Job + rsync into running Pod (complex)

Канон P2: **Option A** — align with «resume = new Pod».

## Hot copy (PodFilesPort)

Use cases:

- Upload attachment to `inbox/` during active session
- Pull generated artifact for download API

Implementation pattern (kubernetes client):

```text
exec into Pod:
  tar xzf - -C /workspace/inbox   ← stream from API multipart upload
```

Alternative: **S3 sidecar** — agent reads presigned URLs; platform only updates MinIO ACL ([15-content-storage](../../15-content-storage/)). Prefer presigned for large blobs; `PodFilesPort` for small admin ops.

## Security

- MinIO credentials: K8s Secret per namespace or shared read-only SA (not in Pod env plaintext in logs)
- NetworkPolicy: Pod egress only MinIO + AI provider endpoints ([isolation.md](../isolation.md))
- No hostPath mounts for workspace

## Content paths (contract)

| Path in Pod | Meaning |
|-------------|---------|
| `/workspace/` | Project root |
| `/workspace/inbox/` | User uploads |
| `/workspace/runs/` | Agent run artifacts |
| `/workspace/.meta/` | Platform-managed (optional) |

Sync excludes secrets; AI keys injected at runtime via agent port, not copied to disk when avoidable.

## Live workspace read (PodWorkspacePort)

When Pod is **running**, API/UI read `/workspace` via k8s exec into sandbox container (`python -m prodavan.runtime.workspace_fs`). Source of truth for the browser is **live Pod FS**, not MinIO mirror.

| Operation | Mechanism |
|-----------|-----------|
| list / stat / read | exec → JSON or bytes stdout |
| delete / move / copy | exec mutations (API only; no Flutter UI yet) |
| UI gate | `observed_state == running`; button hidden otherwise |

Write-back to MinIO (pause sync) remains future work — see rematerialize/hydrate paths above.
