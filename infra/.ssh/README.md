# Host SSH for local Terraform bootstrap

Local k3s on WSL is provisioned over **SSH** (`prodavan-sshd` on `127.0.0.1:2222`).

| Artifact | Role |
|----------|------|
| `prodavan_tf` / `.pub` | Keypair for Terraform remote-exec (gitignored private key) |
| `sshd_config.tpl` | Listen `0.0.0.0:2222`, used by module `k3s-dev-host` |

OpenSSH refuses keys on `/mnt/c` (mode 0777). Copy into the WSL home before apply:

```bash
cp /mnt/c/Users/qwerty/git/Commerce/prodavan/infra/.ssh/prodavan_tf ~/.ssh/prodavan_tf
chmod 600 ~/.ssh/prodavan_tf
```

See `infra/terraform/environments/local/README.md` and `docs/07-infrastructure/runbook.md` §2.

Private keys must stay out of git.
