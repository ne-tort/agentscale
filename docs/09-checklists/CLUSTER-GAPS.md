# CLUSTER-GAPS — готовность Prodavan к k3s (без MCP)

Живой чеклист. Цель оркестрации: **k3s** (`infra/k3s/`). MCP gateway / agent-worker — deferred (I6).

Легенда статусов: `open` | `in_progress` | `done` | `deferred`

---

## Blocker

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| B1 | Dockerfile API | done | `apps/api/Dockerfile` |
| B2 | Dockerfile Flutter web | done | `apps/flutter/Dockerfile` |
| B3 | Каталог `infra/k3s/` manifests | done | base + overlays/dev + platform brokers |
| B4 | Stub path `infra/k8s` vs docs `infra/k3s` | done | k8s README → redirect |
| B5 | CI build/push images | done | `.github/workflows/ci-images.yml` (`:latest` + SHA; overlay не бампается) |
| B6 | PVC / persistent storage для API | done | PVC + STORAGE_ROOT env |

## Important

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| I1 | `/health/live` + `/health/ready` | done | probes для k3s |
| I2 | CORS для Ingress origin | done | CORS_ORIGINS env |
| I3 | Flutter refresh token | done | SessionStore + AuthApi.refresh |
| I4 | OpenAPI stub ≪ runtime | open | не блокер k3s; sync later |
| I5 | Redis/MinIO/Kafka/Celery in k3s | **done** (subset) | Exec Redpanda binary with `--unsafe-bypass-fsync=false` (rpk start --check=false was still bypassing fsync). Admin :9644 + PDB. Init Job cluster knobs + `min.insync.replicas=1`. Celery `inspect ping`. |
| I6 | Базовые экраны projects/runs/variants | done | Flutter screens для отладки |
| I7 | docker-compose.stack.yml (api+web+pg+P0) | done | smoke без кластера |
| I8 | Project sandbox Pod/Job isolator | **in_progress** | object-ws create path; SA on API + token (`verify_sandbox_sa.sh`); Job PVC probe. Admin `/sandbox-k8s` after `:latest` rebuild. **Holes:** no spawn on create; no per-project Pod; no MinIO→/workspace; RWO single-node |
| I9 | Redpanda HA (≥3 / anti-affinity) | deferred | local uses overprovisioned single node |
| I10 | Keycloak in-cluster | open | AUTH_MODE=test + `seed_dev_identity.sh` (company+AI key+cabinet+e2e chat) |
| I11 | Alembic history rewrite vs old PVC | **done** (ops) | `reset_dev_postgres.sh` for legacy `20260808*`/`2026082101` → stub chain |
| I12 | GHCR API/web image lag | **done** (ops path) | Overlay first-party **`:latest`** + k3d import (`IfNotPresent`). SHA-pin without GHCR digest → ImagePullBackOff (I18). |
| I13 | Terraform apply = full stack | **done** (subset) | `bootstrap_gitops=true` → verify_touchable_ui; Windows: `build_local_app_images.ps1` first |
| I14 | Recover after reboot | **done** | `recover_local_stack.sh` (Argo refresh) + `test_k3d_recover.sh TEST_WORKLOADS=1` + `test_broker_pod_recover.sh` (minio/redis/kafka/pg object+row retain) + `acceptance_local.sh` (`BROKER_RECOVER_TEST=1`) |
| I15 | Argo Job churn / selfHeal fight | **done** (subset) | AppProject `prodavan` (not `default`); RespectIgnoreDifferences; no ApplyOutOfSyncOnly; local `prune: false`. `verify_gitops.sh` |
| I16 | Secrets in git (dev) | open | SealedSecrets/SOPS deferred; rotate before shared cluster. **Hole:** `prodavan-minio` / API S3 keys must stay in sync if rotated |
| I17 | API image rebuild without network | **done** (ops) | Dockerfile без apt/curl; `bridge_docker_desktop_image.sh` + verify alembic/celery |
| I18 | SHA-pin `overlays/dev` without GHCR | **done** (policy) | First-party **`:latest`** (CI also pushes SHA as extra tag). Job `bump-k3s-dev` удалён. Infra frozen: postgres `16.15`, redis `7.4.11-alpine`, minio/mc RELEASE, redpanda `v24.2.4`. `verify_image_pins.sh` |
| I19 | Argo repo-server → GitHub TLS | **done** (ops subset) | `reposerver.git.request.timeout=90s` + git retries; wait/recover accept Healthy+ComparisonError if core pods Ready; local Application `prune: false` |
| I20 | Celery readiness vs broker | **done** | `inspect ping` readiness; liveness is PID 1 only (avoid restart storm when Redis blips); `verify_celery.sh` added |
| I21 | Celery worker+beat in one pod | **done** | k3s + `docker-compose.stack.yml` split worker/beat |
| I22 | `kubectl apply -k` immutable hook Jobs | **done** (ops) | `apply_overlay_safe.sh` deletes fixed-name init Jobs before apply (kafka/minio) |
| I23 | RollingUpdate + shared RWO PVC (API/Celery) | **done** | Recreate on api/celery-worker; beat **does not** mount PVC |
| I24 | Redpanda fsync bypass via rpk start | **done** | `rpk start --check=false` injected `--unsafe-bypass-fsync=true`; now exec binary + ConfigMap `developer_mode: false` |
| I25 | Single-node internal RF + Kafka PVC retain | **done** | `internal_topic_replication_factor=1`. `verify_kafka_pvc_retain.sh` produce→delete pod→consume |
| I26 | Redis as Celery broker eviction / PVC | **done** | `maxmemory-policy noeviction` + AOF; `verify_redis_pvc_retain.sh` |
| I27 | MinIO object retain + bucket hardening | **done** | `verify_minio_pvc_retain.sh` (S3 put/get via API). Init: `mc ready`, anonymous none, version suspend. STS: fsGroup 1000, `MINIO_UPDATE=off`. **Hole:** Argo selfHeal reverts uncommitted STS until push; verify waits ingress not Deploy Available (I18 SHA race) |
| I28 | Postgres PVC retain after pod delete | **done** | `verify_postgres_pvc_retain.sh` INSERT→delete→SELECT; Recreate + `pg_ctl` preStop already |
| I29 | Argo AppProject kind whitelist + orphans | **done** | `namespaceResourceWhitelist` pin; AppProject `orphanedResources.warn=true` (not Application — Argo 3.x) |
| I30 | Deploy CI создаёт второй k3d | **done** | `REQUIRE_EXISTING_CLUSTER=1`; terraform apply не в Deploy; runner должен видеть тот же Docker, что и workstation k3d |
| I32 | API integration pytest red on PG 16.15 | open | ~19 failing (`ResourceClosedError`, event-loop, 404 rematerialize). PR Gate = `tests/unit`; full integration = `ci-nightly` |

## Nice / later

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| N1 | Terraform modules (k3s-cluster, network, …) | done | skeleton + validate |
| N2 | Argo CD | done | AppProject `prodavan` + kind whitelist + orphanedResources; Application `prodavan-dev`; bootstrap `ensure_argocd.sh` |
| N3 | ExternalSecrets / SOPS | deferred | |
| N4 | mcp-gateway / agent-worker in k3s | deferred | I6 — вне scope |
| N5 | S4B vault AES/KMS | deferred | |
| N6 | Full Flutter screen inventory | deferred | |

## Acceptance (итерация platform brokers)

```text
bash infra/scripts/apply_overlay_safe.sh
kubectl -n prodavan get sts,deploy,pvc,sa
kubectl -n prodavan get sa prodavan-sandbox
bash infra/scripts/verify_touchable_ui.sh
```

После reboot хоста: `bash infra/scripts/recover_local_stack.sh` (печатает JWT для UI).

Broker/API pod failure: `bash infra/scripts/test_broker_pod_recover.sh` (minio object / redis / kafka / postgres retain + api/celery restarts).

Terraform (local): `bash infra/scripts/terraform_apply_local.sh` или `bootstrap_gitops=true` → verify_touchable_ui; Windows: `build_local_app_images.ps1` first.

Обновлено: 2026-08-24 (I31 PR/CI Gate/auto-merge; I30 deploy attach-only; I18 no bump-k3s-dev)
