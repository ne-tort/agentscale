# Local cluster e2e (GitOps)

Единственный путь: **k3s + Argo CD + kustomize**.

1. k3s running; `export KUBECONFIG=~/.kube/prodavan-dev.yaml`
2. `kubectl apply -k infra/argocd/install`
3. `kubectl apply -k infra/argocd/sealed-secrets`
4. `kubectl apply -f infra/argocd/root-app.yaml`
5. Seal/apply `ghcr-pull` — [`overlays/dev/SECRETS.md`](../../infra/k3s/overlays/dev/SECRETS.md)
6. `cd infra/ops && poetry install && poetry run prodavan-ops wait && poetry run prodavan-ops smoke`

Day-2: merge to `main` → Argo sync. Нет `recover_*.sh`, нет `kubectl apply` overlay руками, нет image import.
