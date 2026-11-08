"""Ленивое разрешение ORM-моделей задачи A10 и контракт колонок.

Модели ``plugin_platform.py`` и ревизия ``2026110801_plugin_platform.py``
создаются ПАРАЛЛЕЛЬНОЙ задачей A10 и на момент написания этого модуля ещё
могли отсутствовать в worktree. Поэтому:

* импорт моделей делается ТОЛЬКО внутри методов (не на уровне модуля) —
  иначе весь пакет репозиториев упал бы на ``import`` до появления A10;
* каждый репозиторий явно проверяет наличие нужных колонок через
  :func:`require_columns` и бросает :class:`PluginRepositorySchemaError`
  (fail-closed: «нет колонки» ≠ «молча вернуть пустое значение»);
* имя колонки можно переопределить через :class:`ColumnMap` в конструкторе —
  это единственный рычаг стыка, не требующий правки чужих моделей.

Родитель добавляет импорт моделей в ``models/__init__.py`` сам (граница
владения: мы не редактируем чужие каталоги).

Все комментарии — на русском, идентификаторы — английские.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# Имя модуля, в котором задача A10 объявляет 5 ORM-моделей.
PLUGIN_PLATFORM_MODULE = "prodavan.infrastructure.persistence.models.plugin_platform"

# Канонические имена классов (контракт ТЗ A10).
GRANT_ROW = "CapabilityGrantRow"
INSTANCE_ROW = "PluginInstanceRow"
ARTIFACT_ROW = "PluginArtifactRow"
AUDIT_ROW = "PluginAuditEventRow"
DELIVERY_ROW = "PluginEventDeliveryRow"


class PluginRepositoryError(RuntimeError):
    """Базовая ошибка слоя репозиториев плагинов (fail-closed)."""


class PluginRepositorySchemaError(PluginRepositoryError):
    """Модель A10 отсутствует или не содержит ожидаемой колонки.

    Отсутствие интеграции = ЯВНАЯ ошибка, а не «разрешить»/«пустой список».
    """


class PluginRepositoryUnavailable(PluginRepositoryError):
    """Хранилище (БД/Redis) недоступно; позитивная мутация отклоняется."""


def load_row(class_name: str) -> type:
    """Лениво импортировать ORM-класс из ``models.plugin_platform``.

    Импорт выполняется при КАЖДОМ вызове (дешевле, чем хранить устаревшую
    ссылку, и позволяет A10 появиться уже после старта процесса). Ошибка
    импорта всегда превращается в ``PluginRepositorySchemaError``: репозиторий
    не должен «тихо» работать без таблицы.
    """
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
    """Проверить, что у ORM-класса есть все ожидаемые атрибуты-колонки.

    Используем ``__table__.columns`` (а не ``hasattr``), чтобы не спутать
    колонку с одноимённым relationship/python-property.
    """
    table = getattr(row, "__table__", None)
    if table is None:
        raise PluginRepositorySchemaError(f"{row.__name__} не является ORM-моделью")
    existing = set(table.columns.keys())
    missing = [name for name in names if name not in existing]
    if missing:
        raise PluginRepositorySchemaError(
            f"{row.__name__}: отсутствуют колонки {sorted(missing)}; "
            f"есть {sorted(existing)}. Передайте ColumnMap с реальными именами "
            f"или выровняйте модель A10 (не правя её из этого каталога)."
        )


def has_columns(row: type, names: Sequence[str]) -> bool:
    """Мягкая проверка для ОПЦИОНАЛЬНЫХ колонок (не бросает)."""
    table = getattr(row, "__table__", None)
    if table is None:
        return False
    existing = set(table.columns.keys())
    return all(name in existing for name in names)


def aware_utc(value: Any) -> Any:
    """Привести naive ``datetime`` к aware UTC (SQLite-зеркало теряет tzinfo).

    ``None`` остаётся ``None``. Не-datetime значения пробрасываются как есть —
    их разберёт валидатор доменного контракта (fail-closed).
    """
    from datetime import UTC, datetime

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return value


def text(value: Any, *, field: str) -> str:
    """Строгая непустая строка; иначе явная ошибка, а не ``str(None)``."""
    if not isinstance(value, str) or not value.strip():
        raise PluginRepositoryError(f"{field} обязано быть непустой строкой")
    return value


def string_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    """JSONB-массив строк → кортеж; пустой/битый массив = ошибка."""
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
    """JSONB-объект → обычный dict; отсутствие/мусор = fail-closed ошибка."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PluginRepositoryError(f"{field}: ожидался JSONB-объект, получено {type(value).__name__}")
    return dict(value)


@dataclass(frozen=True, slots=True)
class ColumnMap:
    """Переопределения имён колонок модели A10 (стык без правки чужих файлов).

    Ключ — логическое имя из ТЗ, значение — реальная колонка ORM-модели.
    Пустое переопределение = каноническое имя. Заполняется родителем при
    интеграции, если A10 назовёт колонки иначе.
    """

    overrides: Mapping[str, str] = field(default_factory=dict)

    def col(self, logical_name: str) -> str:
        return self.overrides.get(logical_name, logical_name)

    def require(self, row: type, *logical_names: str) -> None:
        require_columns(row, [self.col(name) for name in logical_names])

    def has(self, row: type, *logical_names: str) -> bool:
        return has_columns(row, [self.col(name) for name in logical_names])


# ---------------------------------------------------------------------------
# Канонические наборы колонок (документация контракта для A10 / родителя)
# ---------------------------------------------------------------------------

GRANT_COLUMNS: tuple[str, ...] = (
    "grant_id",
    "company_id",
    "plugin_id",
    "plugin_instance_id",
    "manifest_version",
    "artifact_digest",
    "capabilities",
    "egress_policy",
    "quota",
    "status",
    "created_at",
    "revoked_at",
)

#: Опциональные колонки гранта: при отсутствии трактуем как «нет ограничения»,
#: НО строку без ``status``/``revoked_at`` всё равно не считаем активной.
GRANT_OPTIONAL_COLUMNS: tuple[str, ...] = ("expires_at", "enabled_modules", "feature_flags")

ARTIFACT_COLUMNS: tuple[str, ...] = (
    "id",
    "plugin_id",
    "version",
    "digest",
    "manifest",
    "manifest_fingerprint",
    "status",
    "created_at",
)

AUDIT_COLUMNS: tuple[str, ...] = (
    "id",
    "event_id",
    "event_type",
    "occurred_at",
    "company_id",
    "plugin_id",
    "details",
)

DELIVERY_COLUMNS: tuple[str, ...] = (
    "id",
    "subscription_id",
    "source_event_id",
    "status",
    "created_at",
)

#: Зарезервированная служебная строка для фича-флага, когда отдельной таблицы
#: нет. ВСЕ запросы грантов обязаны исключать её (см. grants.py::SENTINEL_*).
FLAG_SENTINEL_GRANT_ID = "__plugin_feature_flags__"
FLAG_SENTINEL_PLUGIN_ID = "__plugin_feature_flags__"

ACTIVE_GRANT_STATUS = "active"
"""Единственный статус, который репозиторий считает активным (fail-closed)."""


__all__ = [
    "ACTIVE_GRANT_STATUS",
    "ARTIFACT_COLUMNS",
    "ARTIFACT_ROW",
    "AUDIT_COLUMNS",
    "AUDIT_ROW",
    "ColumnMap",
    "DELIVERY_COLUMNS",
    "DELIVERY_ROW",
    "FLAG_SENTINEL_GRANT_ID",
    "FLAG_SENTINEL_PLUGIN_ID",
    "GRANT_COLUMNS",
    "GRANT_OPTIONAL_COLUMNS",
    "GRANT_ROW",
    "INSTANCE_ROW",
    "PLUGIN_PLATFORM_MODULE",
    "PluginRepositoryError",
    "PluginRepositorySchemaError",
    "PluginRepositoryUnavailable",
    "aware_utc",
    "has_columns",
    "json_mapping",
    "load_row",
    "require_columns",
    "string_tuple",
    "text",
]
