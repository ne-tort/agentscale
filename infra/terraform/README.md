# Terraform

Cloud skeletons: `environments/dev`, `environments/staging`, `modules/*`.

**Local dev cluster (native k3s on WSL):**

```bash
cd infra/terraform/environments/local
TF_VAR_ghcr_token=$(gh auth token) terraform apply -auto-approve
```

See [`environments/local/README.md`](environments/local/README.md).

Day-2 deploy: git → Argo CD (`docs/07-infrastructure/runbook.md`). Terraform is bootstrap/destroy only.
