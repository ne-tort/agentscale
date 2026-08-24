# P0 infra

Канон: [`docs/07-infrastructure/runbook.md`](../docs/07-infrastructure/runbook.md).

```bash
export KUBECONFIG=~/.kube/prodavan-dev.yaml
kubectl apply -k infra/argocd/install
kubectl apply -k infra/argocd/sealed-secrets
kubectl apply -f infra/argocd/root-app.yaml
# ghcr-pull: see infra/k3s/overlays/dev/SECRETS.md
cd infra/ops && poetry install
poetry run prodavan-ops wait && poetry run prodavan-ops smoke
```

- Workloads: `infra/k3s/overlays/dev` (Argo Application `prodavan-dev`)
- Ops: `validate` / `wait` / `smoke` only
- После reboot: k3s + Argo selfHeal. **Нет** `recover_*.sh`.
