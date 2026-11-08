"""``DbFeatureFlagStore``: флаг выключен → legacy-путь (критерий №16).

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.strangler.flags import (
    EnabledModulesPolicy,
    FeatureFlagStorePort,
)
from prodavan.infrastructure.persistence.repositories.plugins.flags import (
    DbFeatureFlagStore,
)

from .conftest import NOW, FakeSession, FakeSessionFactory


def _store(*, rows: list | None = None, flags_row: type | None = None) -> DbFeatureFlagStore:
    return DbFeatureFlagStore(
        session_factory=FakeSessionFactory(FakeSession(rows=rows)),
        flags_row=flags_row,
        clock=lambda: NOW,
    )


def _feature_flag_model() -> type:
    """Локальная ORM-заглушка 6-й таблицы A10 (без импорта реальной модели)."""
    from sqlalchemy import Column, DateTime, String
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.orm import declarative_base

    base = declarative_base()

    class _FlagRow(base):  # type: ignore[misc, valid-type]
        __tablename__ = "plugin_feature_flags_stub"

        company_id = Column(String(40), primary_key=True)
        enabled_modules = Column(JSONB, nullable=False)
        updated_at = Column(DateTime(timezone=True), nullable=True)

    return _FlagRow


def test_db_feature_flag_store_implements_port_structurally() -> None:
    store = _store()
    assert isinstance(store, FeatureFlagStorePort)
    # Порт разрешает РОВНО три операции — проверяем отсутствие запрещённых имён.
    for forbidden in ("dual_read", "dual_write", "parity", "snapshot", "migrate_data"):
        assert not hasattr(store, forbidden)


def test_flag_disabled_routes_to_legacy() -> None:
    """Кр. 16: флаг выключен (пустой кэш) → ``EnabledModulesPolicy`` = legacy."""
    store = _store()
    policy = EnabledModulesPolicy(store)
    assert policy.is_enabled("co_123", "mod_equipment") is False
    assert policy.enabled_modules("co_123") == ()
    assert store.list("co_123") == ()


def test_enabled_flag_routes_to_plugin() -> None:
    store = _store()
    store.warm("co_123", ["mod_equipment"])
    policy = EnabledModulesPolicy(store)
    assert policy.is_enabled("co_123", "mod_equipment") is True
    assert policy.is_enabled("co_123", "mod_other") is False
    assert store.list("co_123") == ("mod_equipment",)


def test_empty_identifiers_are_fail_closed() -> None:
    store = _store()
    store.warm("co_123", ["mod_equipment"])
    assert store.enabled("", "mod_equipment") is False
    assert store.enabled("co_123", "") is False
    assert store.list("") == ()


def test_sync_set_updates_cache_immediately() -> None:
    """Sync ``set`` (порт) сразу меняет решение роутера."""
    store = _store()
    policy = EnabledModulesPolicy(store)
    assert policy.is_enabled("co_123", "mod_equipment") is False
    store.set("co_123", ["mod_equipment", "mod_equipment"])
    assert policy.is_enabled("co_123", "mod_equipment") is True
    assert store.list("co_123") == ("mod_equipment",)  # дедупликация


def test_sync_set_is_not_durable_until_flush() -> None:
    """Без ``await flush()`` запись остаётся отложенной (fail-closed)."""
    store = _store()
    store.set("co_123", ["mod_equipment"])
    assert store._pending["co_123"] == ("mod_equipment",)


@pytest.mark.asyncio
async def test_async_list_reads_dedicated_flag_table() -> None:
    """Авторитетный список читает 6-ю таблицу A10 (company_id PK + JSONB)."""
    store = _store(rows=[_FeatureRowStub()], flags_row=_feature_flag_model())
    modules = await store.list_async("co_123")
    assert modules == ("mod_equipment", "mod_docs")
    assert store.list("co_123") == ("mod_equipment", "mod_docs")


@pytest.mark.asyncio
async def test_prefetch_warms_cache_for_all_companies() -> None:
    store = _store(rows=[_FeatureRowStub()], flags_row=_feature_flag_model())
    await store.prefetch()
    assert store.enabled("co_123", "mod_equipment") is True
    assert store.enabled("co_999", "mod_equipment") is False


@pytest.mark.asyncio
async def test_async_set_updates_cache_only_after_confirmed_write() -> None:
    store = _store(rows=[_FeatureRowStub()], flags_row=_feature_flag_model())
    result = await store.set_async("co_123", ("mod_equipment",))
    assert result == ("mod_equipment",)
    assert store.enabled("co_123", "mod_equipment") is True
    assert store.enabled("co_123", "mod_docs") is False


def test_missing_flag_table_is_explicit_error() -> None:
    """Нет 6-й таблицы / колонки → ЯВНАЯ ошибка, а не «тихо пустой список»."""
    from prodavan.infrastructure.persistence.repositories.plugins._schema import (
        PluginRepositorySchemaError,
    )

    store = _store(flags_row=_WrongRow())
    with pytest.raises(PluginRepositorySchemaError):
        store._backend()


class _FeatureRowStub:
    """Простая строка-заглушка (атрибуты = колонки)."""

    company_id = "co_123"
    enabled_modules = ["mod_equipment", "mod_docs"]
    updated_at = NOW


class _WrongRow:
    """Модель без company_id — для проверки fail-closed схемы."""

    __table__ = type("T", (), {"columns": {"something": object()}})()
