"""Ленивое разрешение ORM-моделей задачи A10 и РЕАЛЬНЫЙ контракт колонок.

Модель ``models/plugin_platform.py`` создана задачей A10 и прочитана при
реализации. Её физическая схема НЕ совпадает с «идеальным» контрактом ТЗ,
поэтому здесь зафиксировано ПРЕОБРАЗОВАНИЕ логических имён (ТЗ) в реальные
колонки A10. Репозитории работают ТОЛЬКО логическими именами через
:meth:`ColumnMap.col`, а маппинг собран в :data:`A10_COLUMN_MAP`.

Реальные таблицы (не правятся отсюда):

* ``plugin_capability_grants`` — PK ``id`` (логический ``grant_id``), нет
  ``created_at`` (есть ``issued_at``), есть ``expires_at`` и обязательный
  ``created_by``. НЕТ отдельной колонки ``grant_id``.
* ``plugin_artifacts`` — ``oci_digest`` вместо ``digest``, ``app_version_projection``
  (NOT NULL + CHECK ``manifest <> app_version_projection``), ``quarantine_reason``,
  ``cosign_verified``. НЕТ ``is_current``/``manifest_fingerprint``/``image_ref``.
* ``plugin_audit_events`` — append-only, НЕТ ``event_id``/``event_type``/``details``;
  зато есть ``request_id``/``subject_id``/``capability``/``operation``/``resource``/
  ``decision``/``code``/``sql_digest``/``payload_digest`` (часть NOT NULL).
* ``plugin_event_deliveries`` — Unique(``subscription_id``, ``source_event_id``),
  NOT NULL ``topic``/``partition``/``offset``. НЕТ ``kind``/``details``/``event_id``.

Родитель добавляет импорт моделей в ``models/__init__.py`` сам (граница
владения). Импорт моделей здесь ЛЕНИВЫЙ (внутри методов), чтобы пакет не падал
на ``import`` до появления A10 в других сессиях.

Все комментарии — на русском, идентификаторы — английские.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

PLUGIN_PLATFORM_MODULE = "prodavan.infrastructure.persistence.models.plugin_platform"

GRANT_ROW = "CapabilityGrantRow"
INSTANCE_ROW = "PluginInstanceRow"
ARTIFACT_ROW = "PluginArtifactRow"
AUDIT_ROW = "PluginAuditEventRow"
DELIVERY_ROW = "PluginEventDeliveryRow"
#: Шестая таблица A10 (по подтверждению родителя): единственный владелец
#: ``plugin_enabled_modules[]``. ``company_id`` PK + ``enabled_modules`` JSONB.
FLAG_ROW = "PluginFeatureFlagRow"


class PluginRepositoryError(RuntimeError):
    """Базовая ошибка слоя репозиториев плагинов (fail-closed)."""


class PluginRepositorySchemaError(PluginRepositoryError):
    """Модель A10 отсутствует или не содержит ожидаемой колонки."""


class PluginRepositoryUnavailable(PluginRepositoryError):
    """Хранилище (БД/Redis) недоступно; позитивная мутация отклоняется."""


def load_row(class_name: str) -> type:
    """Лениво импортировать ORM-класс из ``models.plugin_platform``."""
    import importlib

    try:
        module = importlib.import_module(PLUGIN_PLATFORM_MODULE)
    except (ImportError, ModuleNotFoundError) as exc:
        raise PluginRepositorySchemaError(
            f"модель A10 ещё не зарегистрирована: нет модуля {PLUGIN_PLATFORM_MODULE!r}; "
            f"родитель должен добавить импорт в models/__init__.py и создать файл модели"
        ) from exc
    row = getattr(module, class_name, None)
    if row is None:
        raise PluginRepositorySchemaError(
            f"в {PLUGIN_PLATFORM_MODULE} нет класса {class_name!r}"
        )
    return row


def require_columns(row: type, names: Sequence[str]) -> None:
    """Проверить наличие колонок (по ``__table__.columns``, не ``hasattr``)."""
    table = getattr(row, "__table__", None)
    if table is None:
        raise PluginRepositorySchemaError(f"{row.__name__} не является ORM-моделью")
    existing = set(table.columns.keys())
    missing = [name for name in names if name not in existing]
    if missing:
        raise PluginRepositorySchemaError(
            f"{row.__name__}: отсутствуют колонки {sorted(missing)}; "
            f"есть {sorted(existing)}. Передайте ColumnMap с реальными именами."
        )


def has_columns(row: type, names: Sequence[str]) -> bool:
    table = getattr(row, "__table__", None)
    if table is None:
        return False
    existing = set(table.columns.keys())
    return all(name in existing for name in names)


def column_names(row: type) -> frozenset[str]:
    table = getattr(row, "__table__", None)
    return frozenset(table.columns.keys()) if table is not None else frozenset()


def aware_utc(value: Any) -> Any:
    """Naive ``datetime`` → aware UTC (SQLite-зеркало теряет tzinfo)."""
    from datetime import UTC, datetime

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return value


def text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PluginRepositoryError(f"{field} обязано быть непустой строкой")
    return value


def string_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        items = value
    elif value is None:
        raise PluginRepositoryError(f"{field}: JSONB-массив отсутствует")
    else:
        raise PluginRepositoryError(f"{field}: ожидался JSONB-массив, получено {type(value).__name__}")
    result: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise PluginRepositoryError(f"{field}: элемент не является непустой строкой")
        result.append(item)
    return tuple(result)


def json_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PluginRepositoryError(f"{field}: ожидался JSONB-объект, получено {type(value).__name__}")
    return dict(value)


@dataclass(frozen=True, slots=True)
class ColumnMap:
    """Логическое имя (ТЗ) → реальная колонка A10.

    По умолчанию подставляется :data:`A10_COLUMN_MAP`. Дополнительные
    ``overrides`` из конструктора имеют наивысший приоритет — это рычаг стыка,
    если родитель переименует колонку, не правя этот каталог.
    """

    overrides: Mapping[str, str] = field(default_factory=dict)

    def col(self, logical_name: str) -> str:
        if logical_name in self.overrides:
            return self.overrides[logical_name]
        return A10_COLUMN_MAP.get(logical_name, logical_name)

    def require(self, row: type, *logical_names: str) -> None:
        require_columns(row, [self.col(name) for name in logical_names])

    def has(self, row: type, *logical_names: str) -> bool:
        return has_columns(row, [self.col(name) for name in logical_names])

    def merged(self, extra: Mapping[str, str] | None) -> "ColumnMap":
        if not extra:
            return self
        return ColumnMap(overrides={**self.overrides, **extra})


# ---------------------------------------------------------------------------
# РЕАЛЬНЫЙ маппинг A10 (прочитан из models/plugin_platform.py)
# ---------------------------------------------------------------------------

A10_COLUMN_MAP: Mapping[str, str] = {
    # --- CapabilityGrantRow: логический grant_id физически = PK id ---
    "grant_id": "id",
    "created_at": "issued_at",
    "expires_at": "expires_at",
    "revoked_at": "revoked_at",
    "created_by": "created_by",
    # --- PluginArtifactRow ---
    "digest": "oci_digest",
    "manifest": "manifest",
    "manifest_fingerprint": "manifest_fingerprint",  # отсутствует → вычисляем
    "image_ref": "image_ref",
    "app_version_projection": "app_version_projection",
    "quarantine_reason": "quarantine_reason",
    "cosign_verified": "cosign_verified",
    "sbom_digest": "sbom_digest",
    "lifecycle_version": "lifecycle_version",  # CAS при set_current
    "is_current": "is_current",  # partial unique UNIQUE(plugin_id) WHERE is_current
    "published_at": "published_at",
    # --- PluginAuditEventRow ---
    "request_id": "request_id",
    "run_id": "run_id",
    "cabinet_id": "cabinet_id",
    "project_id": "project_id",
    "subject_id": "subject_id",
    "plugin_instance_id": "plugin_instance_id",
    "capability": "capability",
    "operation": "operation",
    "resource": "resource",
    "decision": "decision",
    "code": "code",
    "reason": "reason",
    "sql_digest": "sql_digest",
    "payload_digest": "payload_digest",
    "occurred_at": "occurred_at",
    "artifact_digest": "artifact_digest",
    # --- PluginEventDeliveryRow ---
    "subscription_id": "subscription_id",
    "source_event_id": "source_event_id",
    "topic": "topic",
    "partition": "partition",
    "offset": "offset",
    "status": "status",
    "attempts": "attempts",
    "next_retry_at": "next_retry_at",
    "event_id": "event_id",
    "dlq_topic": "dlq_topic",
    "error_kind": "error_kind",
    # --- общие ---
    "id": "id",
    "company_id": "company_id",
    "plugin_id": "plugin_id",
    "version": "version",
    "capabilities": "capabilities",
    "egress_policy": "egress_policy",
    "quota": "quota",
}

# Обязательные логические колонки каждой модели (проверяются при первом вызове).
GRANT_COLUMNS: tuple[str, ...] = (
    "grant_id", "company_id", "plugin_id", "plugin_instance_id",
    "manifest_version", "artifact_digest", "capabilities", "egress_policy",
    "quota", "status", "created_at", "revoked_at",
)
GRANT_NOT_NULL_EXTRA: tuple[str, ...] = ("created_by",)

ARTIFACT_COLUMNS: tuple[str, ...] = (
    "id", "plugin_id", "version", "digest", "manifest",
    "app_version_projection", "status", "created_at",
)

AUDIT_COLUMNS: tuple[str, ...] = (
    "id", "request_id", "subject_id", "company_id", "occurred_at",
    "plugin_instance_id", "artifact_digest", "capability", "operation",
    "resource", "decision", "code",
)

DELIVERY_COLUMNS: tuple[str, ...] = (
    "id", "subscription_id", "source_event_id", "topic", "partition",
    "offset", "status", "attempts", "created_at",
)
#: Аддитивные колонки A10 (по согласованию с родителем).
DELIVERY_EXTRA_COLUMNS: tuple[str, ...] = (
    "event_id", "dlq_topic", "error_kind", "payload_digest",
)

#: ``PluginFeatureFlagRow`` (6-я таблица): company_id PK + enabled_modules JSONB.
FLAG_COLUMNS: tuple[str, ...] = ("company_id", "enabled_modules")

#: Зарезервированная служебная строка фича-флага (fallback, если 6-й таблицы
#: ещё нет в конкретной сессии). ВСЕ запросы грантов исключают её.
FLAG_SENTINEL_ID = "plugin_feature_flags_singleton"
FLAG_SENTINEL_PLUGIN_ID = "pluginfeatureflags"

ACTIVE_GRANT_STATUS = "active"

#: Артефакт: статусы A10 отличаются от ``AppLifecycleStatus`` задачи 07.
ARTIFACT_STATUS_PUBLISHED = "published"
ARTIFACT_STATUS_QUARANTINED = "quarantined"
ARTIFACT_STATUS_REVOKED = "revoked"


__all__ = [
    "ACTIVE_GRANT_STATUS",
    "A10_COLUMN_MAP",
    "ARTIFACT_COLUMNS",
    "ARTIFACT_ROW",
    "ARTIFACT_STATUS_PUBLISHED",
    "ARTIFACT_STATUS_QUARANTINED",
    "ARTIFACT_STATUS_REVOKED",
    "AUDIT_COLUMNS",
    "AUDIT_ROW",
    "ColumnMap",
    "DELIVERY_COLUMNS",
    "DELIVERY_EXTRA_COLUMNS",
    "DELIVERY_ROW",
    "FLAG_COLUMNS",
    "FLAG_ROW",
    "FLAG_SENTINEL_ID",
    "FLAG_SENTINEL_PLUGIN_ID",
    "GRANT_COLUMNS",
    "GRANT_NOT_NULL_EXTRA",
    "GRANT_ROW",
    "INSTANCE_ROW",
    "PLUGIN_PLATFORM_MODULE",
    "PluginRepositoryError",
    "PluginRepositorySchemaError",
    "PluginRepositoryUnavailable",
    "aware_utc",
    "column_names",
    "has_columns",
    "json_mapping",
    "load_row",
    "require_columns",
    "string_tuple",
    "text",
]
