"""Настоящие SQLAlchemy/Redis-репозитории плагинов (F11, задача A10-стык).

Публичный контракт пакета — :func:`build_plugin_repositories` и
:class:`PluginRepositories`. Модули НЕ импортируют ORM-модели A10 на уровне
пакета: все 6 моделей (``models/plugin_platform.py``, ревизия
``2026110801_plugin_platform.py``) разрешаются ЛЕНИВО внутри методов через
:func:`_schema.load_row`, а физические имена колонок сведены в
:data:`_schema.A10_COLUMN_MAP`. Родитель самостоятельно добавляет импорт
моделей в ``infrastructure/persistence/models/__init__.py``.

Соответствие портам:

===================================================  ==========================================
Файл                                                Порт / задача
===================================================  ==========================================
``grants.py``      ``SqlCapabilityGrantRepository``  ``admin.GrantStorePort`` (12) + sync-контракт ТЗ
``revocation.py``  ``DbGrantRevocation``            ``identity.RevocationPort`` (02), ``capabilities.GrantRevocationPort`` (03), ``admin.RevocationPort`` (12)
``quotas.py``      ``RedisPluginQuota``             ``capabilities.QuotaPort`` (03)
``flags.py``       ``DbFeatureFlagStore``           ``strangler.FeatureFlagStorePort`` (F10, 6-я таблица A10)
``artifacts.py``   ``SqlArtifactRegistryStore``    ``artifacts.RegistryStorePort`` (07)
``events.py``      ``Db*Store`` (4 класса)          ``events/ports.py`` (10)
``audit.py``       ``AppendOnlyAuditSink``          ``admin/events/capabilities.AuditPort``
``build.py``       ``build_plugin_repositories``    композиция, fail-closed при отсутствии session/redis
===================================================  ==========================================

Все решения fail-closed, комментарии на русском. Тесты написаны, НО НЕ
ЗАПУСКАЮТСЯ (verification freeze плана миграции §0.2).
"""

from __future__ import annotations

from ._schema import (
    A10_COLUMN_MAP,
    ColumnMap,
    PluginRepositoryError,
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
)
from .artifacts import ConcurrentPublishError, SqlArtifactRegistryStore
from .audit import AppendOnlyAuditSink, AuditAppendError, canonical_digest, redact_details
from .build import PluginRepositories, build_plugin_repositories
from .events import (
    DbDlqStore,
    DbIdempotencyStore,
    DbPublishedEventStore,
    DbSubscriptionStore,
)
from .flags import DbFeatureFlagStore, SyncFeatureFlagStore, feature_flag_port
from .grants import SqlCapabilityGrantRepository
from .instances import SqlPluginInstanceRepository
from .publication_approvals import SqlPluginPublicationApprovalRepository
from .quotas import RedisPluginQuota
from .revocation import DbGrantRevocation
from .update_lock import PluginUpdateLockError, SqlPluginUpdateLock

__all__ = [
    "A10_COLUMN_MAP",
    "AppendOnlyAuditSink",
    "AuditAppendError",
    "ColumnMap",
    "ConcurrentPublishError",
    "DbDlqStore",
    "DbFeatureFlagStore",
    "DbGrantRevocation",
    "DbIdempotencyStore",
    "DbPublishedEventStore",
    "DbSubscriptionStore",
    "PluginRepositories",
    "PluginRepositoryError",
    "PluginRepositorySchemaError",
    "PluginRepositoryUnavailable",
    "RedisPluginQuota",
    "SqlArtifactRegistryStore",
    "SqlCapabilityGrantRepository",
    "SqlPluginInstanceRepository",
    "SqlPluginPublicationApprovalRepository",
    "SqlPluginUpdateLock",
    "PluginUpdateLockError",
    "SyncFeatureFlagStore",
    "build_plugin_repositories",
    "canonical_digest",
    "feature_flag_port",
    "redact_details",
]
