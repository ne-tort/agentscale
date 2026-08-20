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

## Apply (local k3d)

```bash
bash infra/scripts/bootstrap_local_cluster.sh
# or:
kubectl apply -k infra/k3s/overlays/dev
kubectl -n prodavan rollout status deploy/prodavan-api
curl -sS -H 'Host: prodavan.local' http://127.0.0.1:8088/health
```

Full chain (Terraform → Argo → CI smoke): [`docs/07-infrastructure/local-cluster-e2e.md`](../../docs/07-infrastructure/local-cluster-e2e.md).

Smoke без кластера: `docker compose -f infra/docker-compose.stack.yml up --build` (порт web `:8080`).
