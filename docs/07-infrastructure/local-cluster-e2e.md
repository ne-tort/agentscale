# Local cluster e2e (GitOps)

Единственный путь: **k3s + Argo CD + kustomize**.

1. k3s running; `export KUBECONFIG=~/.kube/prodavan-dev.yaml`
2. `kubectl apply -k infra/argocd/install`
3. `kubectl apply -k infra/argocd/sealed-secrets`
4. `kubectl apply -f infra/argocd/root-app.yaml`
5. Seal/apply `ghcr-pull` — [`overlays/dev/SECRETS.md`](../../infra/k3s/overlays/dev/SECRETS.md)
6. `cd infra/ops && poetry install && poetry run prodavan-ops wait && poetry run prodavan-ops smoke`

Day-2: merge to `main` → Argo sync.

**Pytest e2e (канон):** [`e2e.md`](e2e.md) — L2/L3a/L3b, CI opt-in. Legacy-скрипты (`tools/_live_containers_e2e.py`, `tools/generated/_e2e_*`) **удалены**.
