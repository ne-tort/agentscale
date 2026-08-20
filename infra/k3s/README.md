# k3s manifests (Prodavan)

Канон: **этот каталог** (`infra/k3s/`). Не `infra/k8s/`.

Первая волна (без MCP): **Postgres (dev) + API + Flutter web (nginx)** + Traefik Ingress.

```text
infra/k3s/
├── base/                 # namespace, api, web, ingress, PVC
└── overlays/
    └── dev/              # single-node: in-cluster Postgres, replicas=1
```

Deferred (см. CLUSTER-GAPS): `mcp-gateway`, `prodavan-ws`, `workers/agent-worker`.

## Apply (single-node k3s)

```bash
# 1) Install k3s on Linux VM / WSL2 (official curl | sh)
# 2) Build images and import into k3s, or push to GHCR and pull
docker build -t ghcr.io/prodavan/prodavan-api:dev -f apps/api/Dockerfile .
docker build -t ghcr.io/prodavan/prodavan-web:dev \
  --build-arg API_BASE=http://prodavan.local \
  -f apps/flutter/Dockerfile .
# example import on the k3s node:
# docker save ... | sudo k3s ctr images import -

kubectl apply -k infra/k3s/overlays/dev
kubectl -n prodavan rollout status deploy/prodavan-api
curl -sS http://prodavan.local/api/v1/health
# map prodavan.local → node IP in /etc/hosts (or Windows hosts)
```

Smoke без кластера: `docker compose -f infra/docker-compose.stack.yml up --build`.
