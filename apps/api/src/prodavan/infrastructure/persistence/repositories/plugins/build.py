"""Композиция SQL-репозиториев плагинов (F11).

``build_plugin_repositories(session, redis) -> PluginRepositories`` собирает ВСЕ
адаптеры одной неизменяемой связкой. Fail-closed по конструкции:

* ``session is None`` → ``ValueError`` (без БД гранты/аудит/события не работают);
* ``redis is None`` → ``ValueError`` (без квоты = deny, но «собрать без квоты»
  запрещено: иначе родитель случайно подключит InMemoryQuota);
* ни одно поле ``PluginRepositories`` не имеет значения по умолчанию.

``session`` может быть:

* ``AsyncSession`` — готовая сессия (транзакцией управляет вызывающий);
* ``async_sessionmaker[AsyncSession]`` — фабрика (репозитории сами открывают
  сессию на операцию; предпочтительный вариант для приложения).

``redis`` — клиент ``redis.asyncio.Redis`` / sync ``redis.Redis`` / ``RedisManager``.
Никакого «резервного in-memory»: если Redis недоступен во время запроса,
``RedisPluginQuota.enforce`` вернёт ``QUOTA_EXCEEDED`` (deny), но сама сборка
обязана получить НЕ-None зависимость.

Модели A10 НЕ импортируются на уровне модуля (их может ещё не быть в worktree):
все репозитории разрешают их лениво при первом обращении. Родитель добавляет
импорт в ``models/__init__.py`` сам — это его граница владения.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ._schema import ColumnMap
from .artifacts import SqlArtifactRegistryStore
from .audit import AppendOnlyAuditSink
from .events import (
    DbDlqStore,
    DbIdempotencyStore,
    DbPublishedEventStore,
    DbSubscriptionStore,
)
from .flags import DbFeatureFlagStore, SyncFeatureFlagStore
from .grants import SqlCapabilityGrantRepository
from .quotas import RedisPluginQuota
from .revocation import DbGrantRevocation


@dataclass(frozen=True, slots=True)
class PluginRepositories:
    """Все SQL/Redis-адаптеры плагинов одной неизменяемой связкой.

    Имена полей совпадают с именами портов, чтобы родитель мог разложить
    связку по ``PluginAdminPorts`` / ``PluginEventDispatcher`` / strangler
    без адаптеров-переходников (кроме уже описанных sync/async обёрток).
    """

    grant_store: SqlCapabilityGrantRepository
    grant_revocation: DbGrantRevocation
    quota: RedisPluginQuota
    feature_flags: DbFeatureFlagStore
    feature_flags_sync: SyncFeatureFlagStore
    artifact_store: SqlArtifactRegistryStore
    subscription_store: DbSubscriptionStore
    idempotency_store: DbIdempotencyStore
    published_event_store: DbPublishedEventStore
    dlq_store: DbDlqStore
    audit: AppendOnlyAuditSink


def build_plugin_repositories(
    session: AsyncSession | async_sessionmaker[AsyncSession] | Any,
    redis: Any,
    *,
    columns: ColumnMap | None = None,
    quota: Any = None,
    revocation_ttl_seconds: float | None = None,
    flags_column: str | None = None,
    flags_row: Any = None,
    current_column: str | None = None,
    lock_column: str | None = None,
    clock: Any = None,
    key_prefix: str = "",
    created_by: str = "platform-admin",
) -> PluginRepositories:
    """Собрать связку. Отсутствие session/redis = ЯВНАЯ ошибка сборки.

    ``quota`` — ``PluginQuota`` (лимиты); ``columns`` — переопределения имён
    колонок A10; ``clock`` — единые часы для всех репозиториев (тесты);
    ``key_prefix`` — префикс ключей Redis; ``flags_row`` — модель 6-й таблицы
    A10 (по умолчанию разрешается лениво как ``PluginFeatureFlagRow``);
    ``created_by`` — значение обязательной NOT NULL колонки гранта.
    """
    if session is None:
        raise ValueError(
            "build_plugin_repositories: session обязательна (fail-closed: "
            "без БД гранты, аудит и события не считаются рабочими)"
        )
    if redis is None:
        raise ValueError(
            "build_plugin_repositories: redis обязателен (fail-closed: "
            "без хранилища квот запросы отклоняются, но собрать без него нельзя)"
        )

    # Различаем готовую сессию и фабрику. ``async_sessionmaker`` — callable.
    if isinstance(session, AsyncSession):
        session_factory = None
        bound_session = session
    elif isinstance(session, async_sessionmaker):
        session_factory = session
        bound_session = None
    elif callable(session):
        # Дак-тайпинг для тестовых фабрик/обёрток, не являющихся
        # ``async_sessionmaker`` (например lambda, возвращающая async-CM).
        session_factory = session  # type: ignore[assignment]
        bound_session = None
    else:
        raise ValueError(
            "build_plugin_repositories: session должен быть AsyncSession, "
            "async_sessionmaker или async-CM-фабрикой"
        )

    cols = columns or ColumnMap()
    common: dict[str, Any] = {
        "session_factory": session_factory,
        "session": bound_session,
        "columns": cols,
    }

    # Один экземпляр на ВСЮ связку: strangler-роутер (sync ``enabled``) и
    # admin-сервис (async ``set_async``) обязаны видеть один и тот же кэш.
    feature_flags = DbFeatureFlagStore(
        **common,
        flags_column=flags_column,
        flags_row=flags_row,
        clock=clock,
    )

    grant_store = SqlCapabilityGrantRepository(
        **common, now=clock, feature_flags=feature_flags, created_by=created_by
    )
    grant_revocation = DbGrantRevocation(
        grant_store,
        **({"ttl_seconds": revocation_ttl_seconds} if revocation_ttl_seconds is not None else {}),
        clock=clock,
    )
    quota_store = RedisPluginQuota(
        redis,
        quota=quota,
        clock=clock,
        key_prefix=key_prefix,
    )
    artifact_store = SqlArtifactRegistryStore(
        **common,
        current_column=current_column,
        lock_column=lock_column,
        clock=clock,
    )
    subscription_store = DbSubscriptionStore(**common, clock=clock)
    idempotency_store = DbIdempotencyStore(**common, clock=clock)
    published_event_store = DbPublishedEventStore(**common, clock=clock)
    dlq_store = DbDlqStore(**common, clock=clock)
    audit_sink = AppendOnlyAuditSink(**common, clock=clock)

    return PluginRepositories(
        grant_store=grant_store,
        grant_revocation=grant_revocation,
        quota=quota_store,
        feature_flags=feature_flags,
        feature_flags_sync=SyncFeatureFlagStore(feature_flags),
        artifact_store=artifact_store,
        subscription_store=subscription_store,
        idempotency_store=idempotency_store,
        published_event_store=published_event_store,
        dlq_store=dlq_store,
        audit=audit_sink,
    )


__all__ = ["PluginRepositories", "build_plugin_repositories"]
