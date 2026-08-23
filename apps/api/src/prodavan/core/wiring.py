"""Assemble default LifespanManager for the API process (P0 wiring)."""

from __future__ import annotations

from prodavan.config.settings import settings
from prodavan.core.infra.database_resource import DatabaseEngineResource
from prodavan.core.infra.object_storage_manager import ObjectStorageManager
from prodavan.core.infra.redis_manager import RedisManager
from prodavan.core.infra.trigger_worker_resource import TriggerWorkerResource
from prodavan.core.lifespan.manager import LifespanManager

_lifespan_manager: LifespanManager | None = None


def get_lifespan_manager() -> LifespanManager | None:
    return _lifespan_manager


def build_lifespan_manager() -> LifespanManager:
    """Register infra resources: DB → Redis → object store → transitional worker."""
    global _lifespan_manager
    backend = (settings.object_store_backend or "local").strip().lower()
    if backend not in ("local", "s3"):
        backend = "local"
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
    manager.register(TriggerWorkerResource())
    _lifespan_manager = manager
    return manager
