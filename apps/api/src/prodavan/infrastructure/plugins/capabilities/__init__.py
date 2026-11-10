"""Production IO backends for typed plugin capability adapters."""

from .celery import RedisCeleryJobQueue
from .kafka import (
    KafkaEventResourceManager,
    KafkaRawDlqAdapter,
    KafkaSecurityConfig,
    KafkaSecurityError,
    KafkaSubscriptionRuntime,
)
from .kubernetes import K8sEgressProxyTransport
from .mongo import ProductionDocumentStoreService
from .redis import ManagerRedisClient, RedisMcpRegistrationStore

__all__ = [
    "KafkaEventResourceManager",
    "KafkaRawDlqAdapter",
    "KafkaSecurityConfig",
    "KafkaSecurityError",
    "KafkaSubscriptionRuntime",
    "K8sEgressProxyTransport",
    "ManagerRedisClient",
    "ProductionDocumentStoreService",
    "RedisCeleryJobQueue",
    "RedisMcpRegistrationStore",
]
