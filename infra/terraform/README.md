# Terraform — cloud primitives + local k3d

```text
infra/terraform/
├── modules/k3s-local/     # real: ensure_k3d_cluster.sh
├── environments/local/    # k3d + optional GitOps (bootstrap_gitops=true)
└── modules/{network,k3s-cluster,postgres,object-storage}  # cloud skeletons
```

## Local (recommended path)

```bash
# WSL (connection_type=local) — one apply → cluster + images + Argo + smoke
cd infra/terraform/environments/local
terraform init
terraform apply -auto-approve -var=connection_type=local
# UI tokens:
bash ../../../scripts/seed_dev_identity.sh
```

Windows Terraform → WSL SSH (default `connection_type=ssh`):

```powershell
wsl -e bash infra/scripts/setup_wsl_sshd.sh
.\tools\terraform.exe -chdir=infra/terraform/environments/local apply -auto-approve `
  -var="remote_repo_path=/mnt/c/Users/<you>/git/Commerce/prodavan"
# GHCR_TOKEN must be in the WSL environment for private image pulls during gitops provisioner
```

| Variable | Default | Meaning |
|----------|---------|---------|
| `bootstrap_gitops` | `true` | After k3d: `from_scratch_local.sh` → images + Argo + smoke |
| `connection_type` | `ssh` | `local` \| `ssh` |
| `remote_repo_path` | derived / WSL default | Set explicitly for SSH |

Disable GitOps in TF (cluster only): `-var=bootstrap_gitops=false` then `FROM_TERRAFORM=1 bash infra/scripts/from_scratch_local.sh`.

**Recover after reboot** (not terraform): `bash infra/scripts/recover_local_stack.sh`.

Workloads live in Git (`infra/k3s/` → Argo App `prodavan-dev`). Cloud modules remain skeletons — see `docs/07-infrastructure/terraform.md` (aspirational).
