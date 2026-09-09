# k3s Runtime — GitOps, RBAC, sandbox

As-built manifests и целевые дополнения для P2.

## As-built paths

| Path | Content |
|------|---------|
| `infra/k3s/base/prodavan-sandbox/rbac.yaml` | SA `prodavan-sandbox` — probe Jobs in `prodavan` |
| `infra/k3s/base/prodavan-sandbox/rbac-api.yaml` | SA `prodavan-api` — API + Celery worker |
| `infra/k3s/base/prodavan-sandbox/rbac-sandboxes.yaml` | SA `prodavan-project-pod` + cross-ns RoleBinding for API |
| `infra/k3s/base/prodavan-sandbox/hydrate-secret.yaml` | MinIO creds for initContainer (namespace `prodavan-sandboxes`) |
| `infra/k3s/overlays/sandboxes/` | Namespace, NetworkPolicy, sandboxes RBAC, hydrate secret |
| `infra/k3s/overlays/dev/` | Includes `../sandboxes` |
| `docs/07-infrastructure/runbook.md` | Cluster access |

## Namespace

| Key | Value |
|-----|-------|
| Name | `prodavan-sandboxes` |
| Labels | `prodavan.io/managed-by=pod-service` |

All project Pods — **only** this namespace (no default ns).

## Labels (canonical)

Синхронизировано с [k8s-contract.md](../k8s-contract.md):

| Label | Example | Purpose |
|-------|---------|---------|
| `prodavan.io/managed-by` | `pod-service` | Zombie list selector |
| `prodavan.io/pod-id` | UUID | Join PG `project_pods.id` |
| `prodavan.io/project-id` | UUID | |
| `prodavan.io/company-id` | UUID | Quota / admin filter |
| `prodavan.io/workspace-key` | `ws-abc` | Hydrate path |

Legacy `pctr_*` / `container-runtime` — **deprecated**, remove in migration PR.

## ServiceAccount

| SA | Namespace | Used by |
|----|-----------|---------|
| `prodavan-project-pod` | `prodavan-sandboxes` | **Inside** user Pods (minimal; secret get only) |
| `prodavan-api` | `prodavan` | **API + Celery** — k8s client for lifecycle (cross-ns RoleBinding) |
| `prodavan-sandbox` | `prodavan` | Optional PVC probe Job only |

User Pod SA: no create Pod permission. API SA: scoped Role in `prodavan-sandboxes` only.

## RBAC — as-built (P2)

| Resource | Namespace | Notes |
|----------|-----------|-------|
| Role `prodavan-pod-service` | `prodavan-sandboxes` | pods CRUD, exec, metrics |
| RoleBinding → `prodavan-api` | `prodavan-sandboxes` | cross-ns subject |
| Role `prodavan-project-pod` | `prodavan-sandboxes` | get `prodavan-minio-hydrate` secret |
| Role `prodavan-api-jobs` | `prodavan` | batch Jobs when `SANDBOX_K8S_JOBS=true` |

Legacy single-SA model (`prodavan-sandbox` on API + user Pods) — **removed** (P2 audit).

## NetworkPolicy

From [isolation.md](../isolation.md):

- Egress: DNS (UDP/TCP 53), internet (80/443), **Pod API in `prodavan` (TCP 8001 only)**
- Ingress: from ns `prodavan` → agent-runtime **TCP 3921**
- Selector: `prodavan.io/managed-by=pod-service`
- Deny: API `:8000`, MinIO `:9000`, Redis/Kafka/Postgres/Mongo/Keycloak; lateral sandbox→sandbox
- Path isolation inside `:8001` is still auth allowlist + Bridge scopes — [tenant-infra-gateway](../../12-layer-docs/tenant-infra-gateway.md)

## API cluster access

| Env | Auth |
|-----|------|
| In-cluster API pod | SA token mounted at `/var/run/secrets/kubernetes.io/serviceaccount` |
| Local dev | `KUBECONFIG` → WSL k3s ([wsl-dev.md](../../../07-infrastructure/wsl-dev.md)) |

`K8sManager` (target `core/infra`) loads config once at lifespan.

## Image contract

| Image | Tag source |
|-------|------------|
| `prodavan-sandbox` | CI Images → Argo |
| `prodavan-hydrate` | Same pipeline, smaller entrypoint |

Pin by digest in production overlay; dev may use `:latest` from local registry.

## Do not

- `kubectl apply` from agent on shared env ([AGENTS.md](../../../../AGENTS.md))
- Cluster-admin for API SA
- hostPath for workspace

Changes via **PR → Argo sync**.
