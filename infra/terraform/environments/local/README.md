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
- **Required on Win10:** `tools/win-wsl-keepalive.ps1` so Kali is not InitTerminate'd after the last `wsl.exe` exits
- Repo path matches `remote_repo_path` (default `/mnt/c/Users/qwerty/git/Commerce/prodavan`)

Run Terraform **from WSL** (`~/.local/bin/terraform`), not Windows PATH — SSH target is `127.0.0.1:2222` inside the same distro.

Destroy: `terraform destroy` runs `k3s-uninstall.sh` over SSH (requires portproxy
`127.0.0.1:2222` → WSL eth0, see `infra/github-runner/Sync-KubeForDocker.ps1`).

Day-2 deploy: git merge → CI Images → Verify Dev rollout → Argo/smoke (not terraform).

New installs add `--tls-san=host.docker.internal` for Docker runners. Existing clusters:
`Sync-KubeForDocker.ps1` sets `insecure-skip-tls-verify: true` for local-dev.
