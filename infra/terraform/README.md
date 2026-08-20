# Terraform — cloud primitives for Prodavan (k3s VMs, managed PG, object storage)

Layout mirrors [`docs/07-infrastructure/terraform.md`](../../docs/07-infrastructure/terraform.md).

```text
infra/terraform/
├── versions.tf
├── backends.tf          # local backend until cloud state is configured
├── modules/
│   ├── network/
│   ├── k3s-cluster/
│   ├── postgres/
│   └── object-storage/
└── environments/
    ├── dev/
    └── staging/
```

Modules are **skeletons** (`null_resource` placeholders). Real Yandex/AWS resources require an explicit cloud apply request.

```bash
cd infra/terraform/environments/dev
terraform init
terraform validate
```

Kubernetes workloads live in `infra/k3s/`, not here.
