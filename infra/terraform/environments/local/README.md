# Local k3d via Terraform (WSL target)

## One-shot (preferred)

```bash
# Inside WSL
cd infra/terraform/environments/local
terraform init
terraform apply -auto-approve -var=connection_type=local
# bootstrap_gitops=true → images + Argo + smoke
bash ../../../scripts/seed_dev_identity.sh
# Open http://prodavan.local:8088/ — paste JWT
```

## Windows Terraform → SSH into WSL

```powershell
wsl -e bash infra/scripts/setup_wsl_sshd.sh
wsl -e bash -lc "cp -f ~/.ssh/prodavan_tf /mnt/c/Users/qwerty/git/Commerce/prodavan/infra/.ssh/"
cd c:\Users\qwerty\git\Commerce\prodavan
.\tools\terraform.exe -chdir=infra/terraform/environments/local init
.\tools\terraform.exe -chdir=infra/terraform/environments/local apply -auto-approve `
  -var="remote_repo_path=/mnt/c/Users/qwerty/git/Commerce/prodavan"
```

Set `GHCR_TOKEN` in WSL before apply so the gitops remote-exec can pull GHCR images.

Re-run GitOps only:

```bash
terraform apply -replace='terraform_data.gitops_ssh[0]' -auto-approve
# or: FROM_TERRAFORM=1 bash infra/scripts/from_scratch_local.sh
```

After host reboot: `bash infra/scripts/recover_local_stack.sh` (not terraform).
