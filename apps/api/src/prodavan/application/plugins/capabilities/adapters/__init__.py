"""Real typed capability adapters. Missing configuration fails at composition."""

from .cache import RedisCacheAdapter
from .docs import DocumentStoreDocsAdapter
from .events import KafkaEventsAdapter
from .jobs import CeleryJobsAdapter
from .mcp import DurableMcpAdapter
from .network import K8sEgressNetworkAdapter
from .objects import MinioObjectsAdapter
from .pods import ScopedPodsAdapter
from .secrets import SecretStoreSecretsAdapter

__all__ = [
    "CeleryJobsAdapter",
    "DocumentStoreDocsAdapter",
    "DurableMcpAdapter",
    "K8sEgressNetworkAdapter",
    "KafkaEventsAdapter",
    "MinioObjectsAdapter",
    "RedisCacheAdapter",
    "ScopedPodsAdapter",
    "SecretStoreSecretsAdapter",
]
