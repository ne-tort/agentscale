# Local cluster E2E (GitOps)

Canonical path: [`runbook.md`](runbook.md).

1. `k3d cluster create --config infra/k3d/prodavan-dev.yaml`
2. `kubectl apply -k infra/argocd/install && kubectl apply -f infra/argocd/root-app.yaml`
3. `cd infra/ops && poetry install && poetry run prodavan-ops ensure-ghcr-secret wait smoke seed`

Ports: HTTP **8088**, API **6443**. Host header `prodavan.local`.
No `recover_*.sh` — reboot relies on Docker restart + Argo selfHeal.
