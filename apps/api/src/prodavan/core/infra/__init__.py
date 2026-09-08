"""Infrastructure lifespan resources + managers (P0)."""

from prodavan.core.infra.database_resource import DatabaseEngineResource
from prodavan.core.infra.kafka_manager import (
    KafkaManager,
    get_kafka_manager,
    get_kafka_manager_optional,
    set_kafka_manager,
)
from prodavan.core.infra.mongo_manager import (
    MongoManager,
    get_document_store_service,
    get_mongo_manager,
    set_mongo_manager,
)
from prodavan.core.infra.redis_manager import RedisManager, get_redis_manager, set_redis_manager
from prodavan.core.infra.trigger_worker_resource import TriggerWorkerResource
from prodavan.core.infra.worker_manager import WorkerManager, get_celery_app, get_worker_manager
from prodavan.infrastructure.files.manager import (
    FileStoreManager,
    ensure_file_store,
    get_file_store,
    get_file_store_optional,
    set_file_store,
)

__all__ = [
    "DatabaseEngineResource",
    "FileStoreManager",
    "KafkaManager",
    "MongoManager",
    "RedisManager",
    "TriggerWorkerResource",
    "WorkerManager",
    "ensure_file_store",
    "get_celery_app",
    "get_document_store_service",
    "get_file_store",
    "get_file_store_optional",
    "get_kafka_manager",
    "get_kafka_manager_optional",
    "get_mongo_manager",
    "get_redis_manager",
    "get_worker_manager",
    "set_file_store",
    "set_kafka_manager",
    "set_mongo_manager",
    "set_redis_manager",
]
