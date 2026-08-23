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

Stack wires API/Celery to Redis, MinIO (`prodavan` bucket via `minio-init`), Redpanda, and runs
`celery-worker` with beat. Host Kafka port: `localhost:19092`.

## Suggested local API env (sidecars / host uvicorn)

```text
REDIS_URL=redis://localhost:6379/0
OBJECT_STORE_BACKEND=s3
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=prodavan
S3_SECRET_KEY=prodavan123
S3_BUCKET=prodavan
KAFKA_ENABLED=true
KAFKA_BOOTSTRAP_SERVERS=localhost:19092
KAFKA_CONSUMER_ENABLED=true
# KAFKA_CONSUMER_MODE=kick   # or dispatch (per-id Celery task)
CELERY_ENABLED=true
TRIGGER_WORKER_ENABLED=true
```

## Notes / holes

- Celery worker CLI bootstraps app at import (`worker_manager.celery_app`); beat drain/idle follow `TRIGGER_WORKER_ENABLED` / `IDLE_PAUSE_WORKER_ENABLED`.
- Kafka consumer: `kick` or `dispatch`; PG outbox remains claim SoT until full cutover.
- Package sandbox hydrate-from-zip; live mount-from-MinIO still a hole.
- k8s sketches include minio-init Job; PVC/TLS/Helm still hole.
- Company runtime cache: policy/sub/quota; HMAC secrets never stored in Redis.
