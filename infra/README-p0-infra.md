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

- Kafka consumer: `kick` (debounce drain) or `dispatch` (`claim_by_id` via Celery); PG outbox remains claim SoT until full cutover.
- Package sandbox trees hydrate from object-store zip when local dir missing; live mount-from-MinIO still a hole.
- k8s sketches: `deploy/k8s/{redis,minio,kafka,celery}` (not Helm).
- CORS registered via `prodavan.core.middleware.register_cors`.
- Company agent policy + subscription peek use Redis cache (`company_runtime_cache`).
