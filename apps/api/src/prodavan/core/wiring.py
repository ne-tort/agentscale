"""Assemble default LifespanManager for the API process (P0 wiring)."""

from __future__ import annotations

from prodavan.config.settings import settings
from prodavan.core.infra.database_resource import DatabaseEngineResource
from prodavan.core.infra.k8s_manager import k8s_manager_from_settings
from prodavan.core.infra.kafka_manager import KafkaManager
from prodavan.core.infra.metrics_presence_consumer_resource import MetricsPresenceConsumerResource
from prodavan.core.infra.platform_bootstrap_resource import PlatformBootstrapResource
from prodavan.core.infra.redis_manager import RedisManager
from prodavan.core.infra.trigger_worker_resource import TriggerWorkerResource
from prodavan.core.lifespan.manager import LifespanManager
from prodavan.infrastructure.files.manager import FileStoreManager

_lifespan_manager: LifespanManager | None = None


def get_lifespan_manager() -> LifespanManager | None:
    return _lifespan_manager


def build_lifespan_manager() -> LifespanManager:
    """Register infra: DB → Redis → file store → Kafka → Celery → transitional worker."""
    global _lifespan_manager
    backend = (settings.object_store_backend or "local").strip().lower()
    if backend not in ("local", "s3"):
        backend = "local"
    from prodavan.core.infra.worker_manager import worker_manager_from_settings

    manager = LifespanManager()
    manager.register(DatabaseEngineResource())
    manager.register(k8s_manager_from_settings())
    manager.register(
        RedisManager(
            url=settings.redis_url,
            required=settings.redis_required,
        )
    )
    manager.register(
        FileStoreManager(
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
            topic_auth_commands=settings.kafka_topic_auth_commands,
            topic_auth_events=settings.kafka_topic_auth_events,
            topic_relation_events=settings.kafka_topic_relation_events,
            required=settings.kafka_required,
            consumer_enabled=settings.kafka_consumer_enabled,
            consumer_group=settings.kafka_consumer_group,
            auth_commands_group=settings.kafka_auth_commands_group,
            auth_events_group=settings.kafka_auth_events_group,
            relation_events_group=settings.kafka_relation_events_group,
            drain_debounce_sec=settings.kafka_drain_debounce_sec,
            consumer_mode=settings.kafka_consumer_mode,
        )
    )
    manager.register(worker_manager_from_settings())
    if settings.kafka_consumer_enabled:
        manager.register(MetricsPresenceConsumerResource())
    manager.register(PlatformBootstrapResource())
    manager.register(TriggerWorkerResource())
    _lifespan_manager = manager
    return manager
