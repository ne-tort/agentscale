# prodavan-ops

Companion CLI for GitOps. **No shell scripts. No apply. No cluster create.**

```bash
cd infra/ops
poetry install
poetry run prodavan-ops validate   # no .sh / no compose / no k3d; kustomize + image pins
poetry run prodavan-ops wait       # Argo Application Synced+Healthy
poetry run prodavan-ops smoke      # HTTP live/ready/auth/UI
```

Secrets (`ghcr-pull`) — SealedSecret, see `infra/k3s/overlays/dev/SECRETS.md`.
