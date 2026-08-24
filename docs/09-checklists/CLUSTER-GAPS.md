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
| B5 | CI build/push images | done | `.github/workflows/ci-images.yml` |
| B6 | PVC / persistent storage для API | done | PVC + STORAGE_ROOT env |

## Important

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| I1 | `/health/live` + `/health/ready` | done | probes для k3s |
| I2 | CORS для Ingress origin | done | CORS_ORIGINS env |
| I3 | Flutter refresh token | done | SessionStore + AuthApi.refresh |
| I4 | OpenAPI stub ≪ runtime | open | не блокер k3s; sync later |
| I5 | Redis/MinIO/Kafka/Celery in k3s | **done** (subset) | `base/platform/`; Redpanda PVC+headless, no `dev-container` fsync bypass |
| I6 | Базовые экраны projects/runs/variants | done | Flutter screens для отладки |
| I7 | docker-compose.stack.yml (api+web+pg+P0) | done | smoke без кластера |
| I8 | Project sandbox Pod/Job isolator | **open** | local-ws process only; MCP_SANDBOX_SPAWN off in cluster |
| I9 | Redpanda HA (≥3 / anti-affinity) | deferred | local uses overprovisioned single node |
| I10 | Keycloak in-cluster | open | AUTH_MODE=test + `seed_dev_identity.sh` |
| I11 | Alembic history rewrite vs old PVC | **done** (ops) | `reset_dev_postgres.sh` for legacy `20260808*`/`2026082101` → stub chain |
| I12 | GHCR API image lag (celery) | open | rebuild+`k3d image import`; CI push must include celery deps |
| I13 | Terraform apply = full stack | **done** (subset) | `bootstrap_gitops=true` → from_scratch_local; cloud modules still skeletons |
| I14 | Recover after reboot | **done** (subset) | `recover_local_stack.sh`: platform import + Argo + smoke |
| I15 | Argo Job churn / selfHeal fight | **done** (subset) | Sync hooks + ignoreDifferences; wait script no longer apply -k by default |
| I16 | Secrets in git (dev) | open | SealedSecrets/SOPS deferred; rotate before shared cluster |

## Nice / later

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| N1 | Terraform modules (k3s-cluster, network, …) | done | skeleton + validate |
| N2 | Argo CD | done | `infra/argocd/` + Application `prodavan-dev`; bootstrap `infra/scripts/argocd-bootstrap.sh` |
| N3 | ExternalSecrets / SOPS | deferred | |
| N4 | mcp-gateway / agent-worker in k3s | deferred | I6 — вне scope |
| N5 | S4B vault AES/KMS | deferred | |
| N6 | Full Flutter screen inventory | deferred | |

## Acceptance (итерация platform brokers)

```text
kubectl apply -k infra/k3s/overlays/dev
kubectl -n prodavan get sts,deploy,pvc
kubectl -n prodavan rollout status sts/prodavan-kafka
curl -sS -H 'Host: prodavan.local' http://127.0.0.1:8088/health/ready
```

После reboot хоста: `bash infra/scripts/recover_local_stack.sh`.

Обновлено: 2026-08-24
