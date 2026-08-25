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
- **SSH private key for Terraform-from-WSL:** copy off `/mnt/c` (NTFS → mode 0777 breaks OpenSSH):
  `cp .../infra/.ssh/prodavan_tf ~/.ssh/prodavan_tf && chmod 600 ~/.ssh/prodavan_tf`
  (local env prefers `~/.ssh/prodavan_tf` when present)
- Repo path matches `remote_repo_path` (default `/mnt/c/Users/qwerty/git/Commerce/prodavan`)
- Optional: `tools/win-wsl-keepalive.ps1` so Kali is not InitTerminate'd after the last `wsl.exe` exits

Outputs: `kubeconfig_path` → `~/.kube/prodavan-dev.yaml`, HTTP `:8088`.

Destroy: `terraform destroy` runs `k3s-uninstall.sh` over SSH (requires portproxy
`127.0.0.1:2222` → WSL eth0, see `infra/github-runner/Sync-KubeForDocker.ps1`).

Day-2 deploy: git merge → Argo sync (not terraform).
