"""Assemble default LifespanManager for the API process (P0 wiring)."""

from __future__ import annotations

from prodavan.config.settings import settings
from prodavan.core.infra.database_resource import DatabaseEngineResource
from prodavan.core.infra.kafka_manager import KafkaManager
from prodavan.core.infra.object_storage_manager import ObjectStorageManager
from prodavan.core.infra.redis_manager import RedisManager
from prodavan.core.infra.trigger_worker_resource import TriggerWorkerResource
from prodavan.core.infra.worker_manager import WorkerManager
from prodavan.core.lifespan.manager import LifespanManager

_lifespan_manager: LifespanManager | None = None


def get_lifespan_manager() -> LifespanManager | None:
    return _lifespan_manager


def _celery_broker_url() -> str | None:
    return (settings.celery_broker_url or settings.redis_url or "").strip() or None


def build_lifespan_manager() -> LifespanManager:
    """Register infra: DB → Redis → object store → Kafka → Celery → transitional worker."""
    global _lifespan_manager
    backend = (settings.object_store_backend or "local").strip().lower()
    if backend not in ("local", "s3"):
        backend = "local"
    jobs_wanted = bool(settings.trigger_worker_enabled or settings.idle_pause_worker_enabled)
    manager = LifespanManager()
    manager.register(DatabaseEngineResource())
    manager.register(
        RedisManager(
            url=settings.redis_url,
            required=settings.redis_required,
        )
    )
    manager.register(
        ObjectStorageManager(
            backend=backend,  # type: ignore[arg-type]
            storage_root=settings.storage_root,
            s3_endpoint_url=settings.s3_endpoint_url,
            s3_access_key=settings.s3_access_key,
            s3_secret_key=settings.s3_secret_key,
            s3_bucket=settings.s3_bucket,
            s3_region=settings.s3_region,
            mirror_local=settings.object_store_mirror_local,
            required=settings.object_store_required,
        )
    )
    manager.register(
        KafkaManager(
            enabled=settings.kafka_enabled,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            client_id=settings.kafka_client_id,
            topic_platform_events=settings.kafka_topic_platform_events,
            topic_project_triggers=settings.kafka_topic_project_triggers,
            required=settings.kafka_required,
        )
    )
    manager.register(
        WorkerManager(
            enabled=settings.celery_enabled,
            broker_url=_celery_broker_url(),
            result_backend=(settings.celery_result_backend or "").strip() or _celery_broker_url(),
            trigger_interval_sec=settings.trigger_worker_interval_sec,
            schedule_trigger_drain=jobs_wanted,
            schedule_idle_pause=False,
            task_always_eager=settings.celery_task_always_eager,
        )
    )
    manager.register(TriggerWorkerResource())
    _lifespan_manager = manager
    return manager
