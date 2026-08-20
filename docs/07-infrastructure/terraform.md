# Terraform

Infrastructure as Code для базовой облачной инфраструктуры Prodavan. Kubernetes manifests и Helm charts — в `infra/k3s/`; Terraform — **cloud primitives** (VPC, nodes, managed DB, object storage).

---

## Repository layout

```text
infra/terraform/
├── README.md
├── versions.tf
├── backends.tf
├── modules/
│   ├── network/           # VPC, subnets, security groups
│   ├── k3s-cluster/       # VM pool for k3s nodes
│   ├── postgres/          # managed PostgreSQL
│   ├── object-storage/    # S3 bucket + IAM
│   ├── redis/             # managed Redis (optional)
│   └── dns/               # records for api.prodavan.ru
└── environments/
    ├── dev/
    │   ├── main.tf
    │   ├── variables.tf
    │   └── terraform.tfvars
    ├── staging/
    └── prod/
```

---

## State management

```hcl
# backends.tf
terraform {
  backend "s3" {
    bucket         = "prodavan-terraform-state"
    key            = "env/${terraform.workspace}/terraform.tfstate"
    region         = "ru-central1"
    encrypt        = true
    dynamodb_table = "prodavan-terraform-locks"
  }
}
```

Workspaces: `dev`, `staging`, `prod` — isolated state.

---

## Module: network

```hcl
module "network" {
  source = "../../modules/network"

  vpc_cidr             = "10.0.0.0/16"
  public_subnet_cidrs  = ["10.0.1.0/24", "10.0.2.0/24"]
  private_subnet_cidrs = ["10.0.10.0/24", "10.0.11.0/24"]

  tags = local.common_tags
}
```

Outputs:
- `vpc_id`
- `public_subnet_ids`
- `private_subnet_ids`
- `default_security_group_id`

---

## Module: k3s-cluster

Provisions VM instances for k3s (not managed K8s — cost control).

```hcl
module "k3s" {
  source = "../../modules/k3s-cluster"

  cluster_name     = "prodavan-${var.env}"
  server_count     = var.env == "prod" ? 3 : 1
  agent_count      = var.worker_node_count
  instance_type    = var.k3s_instance_type
  subnet_ids       = module.network.private_subnet_ids
  ssh_key_name     = var.ssh_key_name

  # cloud-init installs k3s, joins agents
  k3s_version      = "v1.29.5+k3s1"
}
```

Outputs:
- `kubeconfig` (sensitive → CI secret)
- `server_ips`
- `api_endpoint`

---

## Module: postgres

Managed PostgreSQL 16:

```hcl
module "postgres" {
  source = "../../modules/postgres"

  name               = "prodavan-${var.env}"
  instance_class     = var.pg_instance_class
  allocated_storage  = var.pg_storage_gb
  subnet_ids         = module.network.private_subnet_ids
  vpc_id             = module.network.vpc_id

  databases = ["prodavan"]
  extensions = ["pgcrypto", "uuid-ossp"]

  backup_retention_days = var.env == "prod" ? 30 : 7
}
```

Output: `connection_string` → K8s Secret via external-secrets operator.

---

## Module: object-storage

```hcl
module "storage" {
  source = "../../modules/object-storage"

  bucket_name = "prodavan-${var.env}-files"
  versioning  = var.env == "prod"
  lifecycle_rules = [{
    prefix = "tenants/*/cabinets/*/integrations/s4b-cache/"
    expiration_days = 14
  }]
}
```

IAM policy: api + worker service accounts — scoped `s3:PutObject`, `s3:GetObject` on bucket prefix.

---

## Environment: prod main.tf

```hcl
locals {
  env  = "prod"
  tags = { Project = "prodavan", Environment = local.env }
}

module "network" { ... }
module "k3s" {
  worker_node_count = 5
  k3s_instance_type = "standard-4"
}
module "postgres" {
  pg_instance_class = "db.r6g.large"
  pg_storage_gb     = 200
}
module "storage" { ... }
module "dns" {
  records = {
    "api.prodavan.ru" = module.k3s.api_endpoint
  }
}
```

---

## Variables (prod example)

| Variable | prod | staging | dev |
|----------|------|---------|-----|
| `worker_node_count` | 5 | 2 | 1 |
| `pg_instance_class` | db.r6g.large | db.t4g.medium | db.t4g.micro |
| `pg_storage_gb` | 200 | 50 | 20 |

---

## Apply workflow

```bash
cd infra/terraform/environments/staging
terraform init
terraform plan -out=plan.tfplan
terraform apply plan.tfplan
```

CI: plan on PR, apply on merge to `main` with manual approval for prod.

---

## What Terraform does NOT manage

| Resource | Tool |
|----------|------|
| K8s Deployments | ArgoCD + `infra/k3s/` |
| Application secrets rotation | Manual / external-secrets |
| GitHub Runner VM | Separate tf module or manual |
| ArgoCD apps | `infra/argocd/` |

---

## Drift detection

Weekly `terraform plan` in CI — alert on non-empty diff.

---

## Local dev bypass

Dev uses **docker compose** — no Terraform required. Optional `environments/dev` provisions cheap single VM.

---

## Связанные документы

- [topology.md](topology.md)
- [k3s-services.md](k3s-services.md)
- [argocd.md](argocd.md)
- [env-matrix.md](env-matrix.md)
