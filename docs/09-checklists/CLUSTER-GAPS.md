# CLUSTER-GAPS — готовность Prodavan к k3s (без MCP)

Живой чеклист. Цель оркестрации: **k3s** (`infra/k3s/`). MCP gateway / agent-worker — deferred (I6).

Легенда статусов: `open` | `in_progress` | `done` | `deferred`

---

## Blocker

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| B1 | Dockerfile API | done | `apps/api/Dockerfile` |
| B2 | Dockerfile Flutter web | done | `apps/flutter/Dockerfile` |
| B3 | Каталог `infra/k3s/` manifests | done | base + overlays/dev |
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
| I5 | MinIO/Redis в compose без потребителей | open | docs only; не блокер |
| I6 | Базовые экраны projects/runs/variants | done | Flutter screens для отладки |
| I7 | docker-compose.stack.yml (api+web+pg) | done | smoke без кластера |

## Nice / later

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| N1 | Terraform modules (k3s-cluster, network, …) | done | skeleton + validate |
| N2 | Argo CD | done | `infra/argocd/` + Application `prodavan-dev`; bootstrap `infra/scripts/argocd-bootstrap.sh` |
| N3 | ExternalSecrets / SOPS | deferred | |
| N4 | mcp-gateway / agent-worker in k3s | deferred | I6 — вне scope |
| N5 | S4B vault AES/KMS | deferred | |
| N6 | Full Flutter screen inventory | deferred | I8 |

## Acceptance (итерация 1)

```text
docker compose -f infra/docker-compose.stack.yml up --build
# login UI + GET /api/v1/health

# на хосте с k3s:
kubectl apply -k infra/k3s/overlays/dev
kubectl -n prodavan rollout status deploy/prodavan-api
```

Обновлено: 2026-08-20
