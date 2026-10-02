# Local cluster: SSH host + Terraform → k3s + Argo. UI: http://127.0.0.1:8088/

## Canon (only this)

```bash
# On the SSH host (here: WSL user www), with terraform on PATH:
cd infra/terraform/environments/local
export TF_VAR_ghcr_token="$(gh auth token)"   # or .tf-ghcr.env (gitignored)
# SSH key must be mode 0600 (not on /mnt/c NTFS):
#   cp .../infra/.ssh/prodavan_tf ~/.ssh/prodavan_tf && chmod 600 ~/.ssh/prodavan_tf
terraform init
terraform apply -auto-approve
```

Then open **http://127.0.0.1:8088/** (Traefik `hostNetwork` + `hostIP: 0.0.0.0`, Ingress without `host:`).

Sign in uses `AUTH_MODE=test` one-click personas (**Demo Employee** / **Platform Admin**). Paste JWT is Advanced only.

Requirements on the SSH host:

- Passwordless sudo for k3s (`/etc/sudoers.d/prodavan-terraform`)
- `authorized_keys` has `infra/.ssh/prodavan_tf.pub`
- `remote_repo_path` points at this checkout (default `/mnt/c/Users/qwerty/git/Commerce/agentscale`)

Destroy: `terraform destroy` (k3s-uninstall over the same SSH).

Day-2: merge to `main` → Argo / CI Images — not terraform.

## Not the bootstrap path

- **Browser / UI** does not need kubeconfig, portproxy, or `agentscale.local`.
- **Docker Desktop GHA runners** (Verify Dev): optional `TF_VAR_export_docker_kubeconfig=true` and `infra/github-runner/` — CI only, see that README.
