# Local k3d via Terraform (WSL target)

## Recommended: Terraform on Windows → SSH into WSL

```powershell
# 1) SSH daemon on WSL (once per reboot if rootless)
wsl -e bash /mnt/c/Users/qwerty/git/Commerce/prodavan/infra/scripts/setup_wsl_sshd.sh

# 2) Copy key into repo path (gitignored)
wsl -e bash -lc "cp -f ~/.ssh/prodavan_tf /mnt/c/Users/qwerty/git/Commerce/prodavan/infra/.ssh/"

# 3) From-scratch cluster
wsl -e bash -lc "k3d cluster delete prodavan-dev || true"

# 4) Terraform apply (SSH remote-exec → ensure_k3d_cluster.sh)
cd c:\Users\qwerty\git\Commerce\prodavan
.\tools\terraform.exe -chdir=infra/terraform/environments/local init
.\tools\terraform.exe -chdir=infra/terraform/environments/local apply -auto-approve `
  -replace=module.k3s_local.terraform_data.k3d_cluster_ssh[0]

# 5) GitOps (Argo + images + smoke) on WSL
wsl -e bash -lc "export GHCR_TOKEN=... ARGOCD_REPO_TOKEN=... FROM_TERRAFORM=1; bash /mnt/c/Users/qwerty/git/Commerce/prodavan/infra/scripts/from_scratch_local.sh"
```

Defaults: `connection_type=ssh`, host `127.0.0.1:2222`, user `www`,
repo `/mnt/c/Users/qwerty/git/Commerce/prodavan`.

## Alternative: all inside WSL (`connection_type=local`)

```bash
cd infra/terraform/environments/local
terraform apply -auto-approve -var=connection_type=local
FROM_TERRAFORM=1 bash ../../../scripts/from_scratch_local.sh
```
