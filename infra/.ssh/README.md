# Terraform → WSL SSH

1. On WSL: `bash infra/scripts/setup_wsl_sshd.sh` (rootless sshd on `127.0.0.1:2222`).
2. Key is copied here as `prodavan_tf` (gitignored).
3. From Windows: `terraform -chdir=infra/terraform/environments/local apply` with `connection_type=ssh`.

Private key must stay out of git.
