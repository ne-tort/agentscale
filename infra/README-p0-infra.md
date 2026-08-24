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
(PVC/volume, **exec binary** `--unsafe-bypass-fsync=false` — not `rpk start --check=false`),
and runs `celery-worker` plus `celery-beat`.
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
- I8: SA `prodavan-sandbox` on **API** (token for future Jobs). Create path stays `object-ws`.
- `verify_sandbox_job.sh` + `verify_sandbox_sa.sh` (included in `verify_touchable_ui.sh`).
- Admin `GET /admin/projects/sandbox-k8s` + flag-gated `POST .../sandbox-k8s/pvc-probe` (in git; cluster image until next `:latest` rebuild).
- **Holes:** no per-project Pod; no spawn on create; live MinIO→/workspace; RWO single-node.
- Create project → `container_ref=object-ws:{key}` (`domain/projects/types.py`) → sync materialize via `ProjectMaterializeService` → `WorkspaceLayoutWriter` writes to MinIO (SoT) + local mirror on **API PVC** when `OBJECT_STORE_MIRROR_LOCAL=true`.
- Celery worker mounts the **same** PVC on single-node k3d so rematerialize jobs see the mirror.
- MCP packages: zip hydrate; `MCP_SANDBOX_SPAWN=false` in cluster (fixture agent chat does not need a live MCP process).
- Agent chat in seed/e2e uses **FixtureCursorAdapter** (`cursor` + `cursor_sdk`) — not real Cursor SDK.
- Verify: `bash infra/scripts/verify_project_sandbox.sh` + `verify_sandbox_job.sh` + `verify_sandbox_sa.sh` (included in `verify_touchable_ui.sh`).
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
docker build -f apps/api/Dockerfile -t ghcr.io/ne-tort/prodavan-api:latest .
```

WSL picks it up via `bridge_docker_desktop_image.sh` inside `import_local_app_images_k3d.sh`.

Or one-shot on Windows:

```powershell
pwsh infra/scripts/build_local_app_images.ps1
```

## Acceptance

```bash
bash infra/scripts/acceptance_local.sh          # recover + smoke + seed chat + project sandbox
BROKER_RECOVER_TEST=1 bash infra/scripts/acceptance_local.sh   # + redis/minio/kafka/pg/api pod recover
TEST_WORKLOADS=1 bash infra/scripts/test_k3d_recover.sh   # k3d stop/start + recover
bash infra/scripts/test_broker_pod_recover.sh   # redis/minio/kafka/postgres/api/celery-worker/celery-beat
```

## Terraform (local, one shot)

```bash
bash infra/scripts/terraform_apply_local.sh   # Windows build + terraform apply + GitOps + UI
```

## Notes / holes

- Kafka consumer kick|dispatch; PG outbox still claim SoT.
- Redpanda single-node `--overprovisioned` for k3d; HA/TLS/Helm — hole (I9). `verify_kafka.sh` includes produce/consume + `min.insync.replicas=1` on app topics.
- Broker PVC retain: `verify_minio_pvc_retain.sh` / `verify_redis_pvc_retain.sh` / `verify_kafka_pvc_retain.sh` / `verify_postgres_pvc_retain.sh` (wired into `test_broker_pod_recover.sh`).
- Celery runs as two Deployments: `prodavan-celery-worker` and `prodavan-celery-beat` (local-compatible split).
- `verify_celery.sh` validates worker `inspect ping` and that beat pod really runs `celery ... beat`.
- Argo local Application: **prune=false**; AppProject kind whitelist + `orphanedResources.warn`; overlay first-party **`:latest`**. Recover does not require Synced if GitHub TLS ComparisonError and workloads are Ready (I19).
- Local emergency apply uses `apply_overlay_safe.sh` to delete fixed-name init Jobs before `kubectl apply -k` (avoids immutable Job template errors).
- **Dev images:** first-party `:latest` (k3d import + IfNotPresent). Infra: postgres `16.15`, redis `7.4.11-alpine`, MinIO/mc RELEASE, Redpanda `v24.2.4`. SHA-pin overlay breaks GitOps if GHCR has no digest (I18). `verify_image_pins.sh`.
- AUTH_MODE=test in cluster ConfigMap (Keycloak-in-cluster — hole).
- Secrets still in git for local (I16); MinIO root + API S3 keys must stay aligned if rotated.
