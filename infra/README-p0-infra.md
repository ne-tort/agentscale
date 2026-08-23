# P0 platform infra (local)

Sidecar services for Redis / MinIO / Redpanda (Kafka API):

```bash
# from prodavan/
docker compose -f infra/docker-compose.dev.yml up -d
```

## Suggested API env (`apps/api/.env`)

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
CELERY_ENABLED=true
TRIGGER_WORKER_ENABLED=true
```

Create MinIO bucket `prodavan` once (console http://localhost:9001).

## Celery worker

Separate process (with `PYTHONPATH=apps/api/src`):

```bash
celery -A prodavan.core.infra.worker_manager.celery_app worker -l info -B
```

When `CELERY_ENABLED=true`, API skips in-process trigger loop.

## Notes / holes

- Kafka consumer only **kicks** Celery drain; PG outbox remains claim SoT until full cutover.
- Stack compose (`docker-compose.stack.yml`) still Postgres-only for API UI; use `docker-compose.dev.yml` for P0 brokers.
