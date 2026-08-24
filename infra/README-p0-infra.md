# P0 platform infra (local)

## Sidecars only

```bash
# from prodavan/
docker compose -f infra/docker-compose.dev.yml up -d
```

Redis `:6379`, MinIO `:9000`/console `:9001`, Redpanda Kafka `:19092`.

## Full stack (API + UI + P0 brokers + Celery)

```bash
docker compose -f infra/docker-compose.stack.yml up --build -d
```

## k3s / k3d (canonical — GitOps)

```bash
k3d cluster create --config infra/k3d/prodavan-dev.yaml
kubectl apply -k infra/argocd/install
kubectl apply -k infra/argocd/sealed-secrets
kubectl apply -f infra/argocd/root-app.yaml
cd infra/ops && poetry install
poetry run prodavan-ops ensure-ghcr-secret   # or SealedSecret — see overlays/dev/SECRETS.md
poetry run prodavan-ops wait && poetry run prodavan-ops smoke && poetry run prodavan-ops seed
```

After reboot: Docker restart + Argo selfHeal. **No** `recover_*.sh`.

Brokers: `infra/k3s/base/platform/`. First-party images `:latest` + IfNotPresent; pins enforced by `prodavan-ops validate`.

Ops runbook: [`docs/07-infrastructure/runbook.md`](../docs/07-infrastructure/runbook.md).
