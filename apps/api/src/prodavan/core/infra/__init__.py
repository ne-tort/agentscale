"""Infrastructure lifespan resources + managers (P0)."""

from prodavan.core.infra.database_resource import DatabaseEngineResource
from prodavan.core.infra.object_storage_manager import (
    ObjectStorageManager,
    ensure_object_storage,
    get_object_storage,
    get_object_storage_optional,
    set_object_storage,
)
from prodavan.core.infra.redis_manager import RedisManager, get_redis_manager, set_redis_manager
from prodavan.core.infra.trigger_worker_resource import TriggerWorkerResource
from prodavan.core.infra.worker_manager import WorkerManager, get_celery_app, get_worker_manager

__all__ = [
    "DatabaseEngineResource",
    "ObjectStorageManager",
    "RedisManager",
    "TriggerWorkerResource",
    "WorkerManager",
    "ensure_object_storage",
    "get_celery_app",
    "get_object_storage",
    "get_object_storage_optional",
    "get_redis_manager",
    "get_worker_manager",
    "set_object_storage",
    "set_redis_manager",
]
