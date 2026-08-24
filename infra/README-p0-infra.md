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

Stack wires API/Celery to Redis (AOF), MinIO (`prodavan` bucket via `minio-init`), Redpanda
(PVC/volume, **no** `dev-container` / fictional `--mode empty`), and runs `celery-worker` with beat.
Host Kafka: `localhost:19092`. Worker entrypoint: `python -m celery …` (image ENTRYPOINT passes `$@`).

## k3s / k3d (canonical)

```bash
kubectl apply -k infra/k3s/overlays/dev
# or: bash infra/scripts/bootstrap_local_cluster.sh
# after host reboot: bash infra/scripts/recover_local_stack.sh
```

Brokers live under `infra/k3s/base/platform/` (StatefulSets + PVC). API ConfigMap enables Redis/S3/Kafka/Celery.

## Project sandbox reality check

- **Not** creating per-project Kubernetes containers yet.
- Materialize → object-store keys + optional local mirror under `STORAGE_ROOT`.
- MCP packages: zip hydrate + optional **local** `subprocess` when `MCP_SANDBOX_SPAWN=true` (cluster default **false**).
- Next: k8s Job/Pod isolator + live MinIO volume mount (documented hole).

## Notes / holes

- Kafka consumer kick|dispatch; PG outbox still claim SoT.
- Redpanda single-node `--overprovisioned` for k3d; HA/TLS/Helm — hole.
- AUTH_MODE=test in cluster ConfigMap (Keycloak-in-cluster — hole).
