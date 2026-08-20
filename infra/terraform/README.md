# Terraform — cloud primitives + local k3d

Layout mirrors [`docs/07-infrastructure/terraform.md`](../../docs/07-infrastructure/terraform.md).

```text
infra/terraform/
├── versions.tf
├── backends.tf
├── modules/
│   ├── network/           # cloud skeleton
│   ├── k3s-cluster/       # cloud skeleton
│   ├── k3s-local/         # k3d on Docker Desktop / WSL
│   ├── postgres/
│   └── object-storage/
└── environments/
    ├── local/             # apply for local CI/GitOps chain
    ├── dev/               # cloud skeleton
    └── staging/
```

Cloud modules are **skeletons**. Local env creates a real k3d cluster (idempotent).

```bash
# Local k3d (WSL / Docker Desktop)
cd infra/terraform/environments/local
terraform init
terraform apply -auto-approve
# kubeconfig → infra/.kube/prodavan-k3d.yaml

# Cloud skeleton validate only
cd infra/terraform/environments/dev
terraform init
terraform validate
```

Kubernetes workloads: `infra/k3s/`. E2E: [`docs/07-infrastructure/local-cluster-e2e.md`](../../docs/07-infrastructure/local-cluster-e2e.md).
