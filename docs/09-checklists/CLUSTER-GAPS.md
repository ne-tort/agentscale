# CLUSTER-GAPS — готовность Prodavan к k3s (без MCP)

Живой чеклист. Цель оркестрации: **k3s** (`infra/k3s/`). MCP gateway / agent-worker — deferred (I6).

Легенда статусов: `open` | `in_progress` | `done` | `deferred`

---

## Blocker

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| B1 | Dockerfile API | done | `apps/api/Dockerfile` (no shell entrypoint; alembic initContainer) |
| B2 | Dockerfile Flutter web | done | `apps/flutter/Dockerfile` |
| B3 | Каталог `infra/k3s/` manifests | done | base + overlays/dev + platform brokers |
| B4 | Stub path `infra/k8s` vs docs `infra/k3s` | done | k8s README → redirect |
| B5 | CI build/push images | done | `.github/workflows/ci-images.yml` (`:latest` + SHA) |
| B6 | PVC / persistent storage для API | done | PVC + STORAGE_ROOT env |

## Important

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| I1 | `/health/live` + `/health/ready` | done | probes для k3s |
| I2 | CORS для Ingress origin | done | CORS_ORIGINS env |
| I3 | Flutter refresh token | done | SessionStore + AuthApi.refresh |
| I4 | OpenAPI stub ≪ runtime | open | не блокер k3s |
| I5 | Redis/MinIO/Kafka/Celery in k3s | **done** (subset) | platform StatefulSets |
| I6 | Базовые экраны projects/runs/variants | done | Flutter screens |
| I7 | docker-compose.stack.yml | **done** (removed) | compose-as-cluster удалён; только k3s+Argo |
| I8 | Project sandbox Pod/Job isolator | **in_progress** | SA + PVC probe; holes remain |
| I9 | Redpanda HA | deferred | local single node |
| I10 | Keycloak in-cluster | open | AUTH_MODE=test; seed — out of GitOps CLI |
| I11 | Alembic history rewrite vs old PVC | **done** (ops) | wipe PVC manually if legacy revisions |
| I12 | GHCR API/web image lag | **done** | `:latest` + IfNotPresent; kubelet + SealedSecret |
| I13 | Terraform apply = full stack | **done** (removed local) | local TF удалён; cloud skeletons only |
| I14 | Recover after reboot | **done** | k3s systemd + Argo selfHeal; **no** recover shell |
| I15 | Argo Job churn / selfHeal | **done** | AppProject + RespectIgnoreDifferences; prune enabled |
| I16 | Secrets in git (dev) | **in_progress** | Sealed Secrets controller + seal workflow; plaintext forbidden |
| I17 | API image rebuild | **done** | Dockerfile без apt/curl |
| I18 | SHA-pin overlay | **done** (policy) | First-party `:latest`; infra frozen tags; `prodavan-ops validate` |
| I19 | Argo repo-server → GitHub TLS | **done** | install patches (timeout/retries) |
| I20–I29 | Celery/PVC/Kafka/MinIO/Redis retain | **done** | manifests + prior verification |
| I30 | Deploy CI второй k3d | **done** | Deploy/k3d убраны; Verify Dev = wait/smoke |
| I31 | PR + CI Gate | **done** | runbook GitOps |
| I32 | API integration pytest on PG 16.15 | open | Gate = unit; nightly = integration |

## Nice / later

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| N1 | Terraform modules | done | cloud skeletons (local env removed) |
| N2 | Argo CD | done | install kustomize + root-app + apps |
| N3 | ExternalSecrets / SOPS | deferred | Sealed Secrets preferred path for ghcr-pull |
| N4–N6 | mcp / vault / Flutter inventory | deferred | |

## Acceptance

```text
cd infra/ops && poetry run prodavan-ops validate
kubectl -n prodavan get sts,deploy,pvc,sa
poetry run prodavan-ops wait && poetry run prodavan-ops smoke
```

После reboot: k3s + Argo selfHeal, без shell recover. Ранбук: `docs/07-infrastructure/runbook.md`.

Обновлено: 2026-08-24 (k3s-only GitOps; no compose/k3d/seed CLI)
