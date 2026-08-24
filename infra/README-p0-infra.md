# P0 platform infra (local)

## Sidecars only

```bash
# from prodavan/
docker compose -f infra/docker-compose.dev.yml up -d
```

Redis `:6379`, MinIO `:9000`/console `:9001`, Redpanda Kafka `:19092`.

## Full stack (API + UI + P0 brokers + Celery)

```bash
docker compose -f infra/docker-compose.stack.yml up --build -d
```

Stack wires API/Celery to Redis (AOF), MinIO (`prodavan` bucket via `minio-init`), Redpanda
(PVC/volume, **no** `dev-container` / fictional `--mode empty`), and runs `celery-worker` with beat.
Host Kafka: `localhost:19092`. Worker entrypoint: `python -m celery …` (image ENTRYPOINT passes `$@`).

## k3s / k3d (canonical)

```bash
kubectl apply -k infra/k3s/overlays/dev
# or: bash infra/scripts/bootstrap_local_cluster.sh
# after host reboot: bash infra/scripts/recover_local_stack.sh
```

Brokers live under `infra/k3s/base/platform/` (StatefulSets + PVC). API ConfigMap enables Redis/S3/Kafka/Celery.

## Project sandbox reality check

- **Not** creating per-project Kubernetes containers yet (`CLUSTER-GAPS` I8).
- Create project → `container_ref=object-ws:{key}` → materialize to MinIO + local mirror on **API PVC**.
- Celery worker mounts the **same** PVC on single-node k3d so rematerialize jobs see the mirror.
- MCP packages: zip hydrate; `MCP_SANDBOX_SPAWN=false` in cluster (fixture agent chat does not need a live MCP process).
- Agent chat in seed/e2e uses **FixtureCursorAdapter** (`cursor` + `cursor_sdk`) — not real Cursor SDK.
- Next: k8s Job/Pod isolator + live MinIO volume mount (documented hole).

## Touchable local UI

One-shot after cluster is up (included in `bootstrap_gitops.sh` / `recover_local_stack.sh` when `SEED_UI=1`):

```bash
bash infra/scripts/recover_local_stack.sh
# or terraform: bootstrap_gitops=true → from_scratch_local (SEED_UI=1, BUILD_LOCAL_IMAGES=1)
# Open http://prodavan.local:8088/
# Paste JWTs printed by seed_dev_identity.sh
```

Manual seed only:

```bash
SKIP_SEED=1 bash infra/scripts/recover_local_stack.sh   # stack without seed
bash infra/scripts/seed_dev_identity.sh                 # seed alone
bash infra/scripts/import_local_app_images_k3d.sh       # rebuild web+api + import
```

WSL `docker build` may fail on `apt-get` (debian mirror timeout). Build on **Docker Desktop** (Windows), then import:

```powershell
docker build -f apps/api/Dockerfile -t ghcr.io/ne-tort/prodavan-api:local .
```

WSL picks it up via `bridge_docker_desktop_image.sh` inside `import_local_app_images_k3d.sh`.

## Notes / holes

- Kafka consumer kick|dispatch; PG outbox still claim SoT.
- Redpanda single-node `--overprovisioned` for k3d; HA/TLS/Helm — hole.
- AUTH_MODE=test in cluster ConfigMap (Keycloak-in-cluster — hole).
