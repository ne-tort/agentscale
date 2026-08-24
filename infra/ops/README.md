# prodavan-ops

Python/Poetry CLI for Prodavan GitOps. **No shell scripts.**

```bash
cd infra/ops
poetry install
poetry run prodavan-ops validate
poetry run prodavan-ops wait
poetry run prodavan-ops smoke
poetry run prodavan-ops seed
poetry run prodavan-ops ensure-ghcr-secret
```

Does **not**: create k3d clusters, `kubectl apply -k` overlays, or `k3d image import` in the happy path.
