# k3s manifests (Prodavan) — canonical GitOps path

Канон: **этот каталог** (`infra/k3s/`). Sketches in `deploy/k8s/` mirror brokers; prefer kustomize here.

```text
infra/k3s/
├── base/
│   ├── platform/         # Redis (AOF) + MinIO + Redpanda StatefulSet + Celery
│   ├── prodavan-api/     # API + PVC + P0 env
│   ├── prodavan-web/
│   └── ingress.yaml
└── overlays/
    └── dev/              # + Postgres; imagePullSecrets
```

## Apply (local k3d)

```bash
bash infra/scripts/bootstrap_local_cluster.sh
# After rebuild / Docker Hub flakes — import broker+API images into the node:
bash infra/scripts/import_platform_images_k3d.sh
k3d image import ghcr.io/ne-tort/prodavan-api:latest -c prodavan-dev
# Schema rewrite (legacy alembic → stub_bootstrap chain) on an old PVC:
bash infra/scripts/reset_dev_postgres.sh
# or after reboot:
bash infra/scripts/recover_local_stack.sh
# or:
kubectl apply -k infra/k3s/overlays/dev
kubectl -n prodavan get pods,pvc
curl -sS -H 'Host: prodavan.local' http://127.0.0.1:8088/health/ready
```

**Argo CD** Application `prodavan-dev` auto-syncs `main` → `infra/k3s/overlays/dev` with selfHeal. Local `kubectl apply` without push will be reverted — commit+push first.

## Persistence / reboot

| Component | Persistence | Notes |
|-----------|-------------|-------|
| Postgres | PVC | overlay/dev |
| Redis | PVC + AOF | StatefulSet |
| MinIO | PVC | StatefulSet + init Job for bucket |
| Redpanda | volumeClaimTemplates | No `--mode` (avoid `dev-container` fsync bypass); headless Service + sticky pod advertise; `--overprovisioned` for k3d only |
| API storage | PVC | mirror_local for agent cwd |
| Celery | stateless | same image/config as API |

## Project containers (sandbox)

Сегодня: **не** создаются k8s Pod/Job на project. Materialize пишет workspace в object-store + local mirror; MCP sandbox — opt-in **local process spawn** (`MCP_SANDBOX_SPAWN`, default off in cluster). Live MinIO mount / bubblewrap / per-project Pod — hole (P0 + CLUSTER-GAPS).

## Gaps

- Redpanda HA (≥3 brokers, anti-affinity, no overprovisioned) — Helm/operator later
- Keycloak in-cluster IdP — AUTH_MODE=test for local
- Project isolator Pods — deferred

Full chain: [`docs/07-infrastructure/local-cluster-e2e.md`](../../docs/07-infrastructure/local-cluster-e2e.md).
Smoke без кластера: `docker compose -f infra/docker-compose.stack.yml up --build`.
