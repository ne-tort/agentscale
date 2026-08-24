# Local dev cluster: native k3s on WSL via Terraform SSH (127.0.0.1:2222).

```powershell
# From Windows (repo checkout on C:)
cd infra/terraform/environments/local
$env:TF_VAR_ghcr_token = gh auth token
terraform init
terraform apply -auto-approve
```

Requirements on WSL host (`www`):

- Passwordless sudo (`/etc/sudoers.d/prodavan-terraform`) for k3s install
- `~/.ssh/authorized_keys` contains `infra/.ssh/prodavan_tf.pub`
- Repo path matches `remote_repo_path` (default `/mnt/c/Users/qwerty/git/Commerce/prodavan`)

Outputs: `kubeconfig_path` → `~/.kube/prodavan-dev.yaml`, HTTP `:8088`.

Destroy: `terraform destroy` runs `k3s-uninstall.sh`.

Day-2 deploy: git merge → Argo sync (not terraform).
