"""Static tests historical contexts narrative и неизменного behavior contract.

Тесты НЕ запускались (verification freeze).
"""

from __future__ import annotations

import pytest

import prodavan.application.plugins.strangler.contexts as contexts_module
from prodavan.application.plugins import strangler as strangler_package
from prodavan.application.plugins.strangler import (
    CONTEXT_ORDER,
    DATA_TRANSFER_POLICY,
    MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER,
    ContextMigration,
    RemovalStep,
    context_by_id,
)
from prodavan.application.plugins.strangler.contexts import MIGRATION_CONTEXTS

EXPECTED_CONTEXTS = ("offers", "prices", "budget", "documents", "tenders", "mod_management")


def _narrative_text() -> str:
    return "\n".join(
        (
            strangler_package.__doc__ or "",
            contexts_module.__doc__ or "",
            RemovalStep.__doc__ or "",
            ContextMigration.__doc__ or "",
            DATA_TRANSFER_POLICY,
        )
    )


def test_all_five_contexts_plus_mod_management_are_covered() -> None:
    assert CONTEXT_ORDER == EXPECTED_CONTEXTS
    assert tuple(item.context_id for item in MIGRATION_CONTEXTS) == EXPECTED_CONTEXTS
    assert len(MIGRATION_CONTEXTS) == 6


def test_order_matches_section_8_stage_4_and_stage_6() -> None:
    """offers → prices → budget → documents → tenders, mod_management последним."""
    assert CONTEXT_ORDER[:5] == ("offers", "prices", "budget", "documents", "tenders")
    assert CONTEXT_ORDER[-1] == "mod_management"


@pytest.mark.parametrize("context_id", EXPECTED_CONTEXTS)
def test_every_context_has_required_fields(context_id: str) -> None:
    item = context_by_id(context_id)
    assert isinstance(item, ContextMigration)
    assert item.context_id == context_id
    # Compatibility source references сохранены; это не deletion list.
    assert isinstance(item.legacy_entrypoints, tuple)
    assert len(item.legacy_entrypoints) > 0
    assert all(isinstance(name, str) and name.strip() for name in item.legacy_entrypoints)
    # plugin_capability — непустой идентификатор capability плагина.
    assert isinstance(item.plugin_capability, str) and item.plugin_capability.strip()
    # Inert historical metadata сохранена без Alembic/runtime cutover.
    assert isinstance(item.removal_step, RemovalStep)
    assert item.removal_step.alembic_revision
    assert item.removal_step.dropped_module
    assert item.removal_step.dropped_tables


def test_context_by_id_is_strict() -> None:
    assert context_by_id("offers") is not None
    assert context_by_id("unknown-context") is None


def test_legacy_table_metadata_is_preserved_without_runtime_cutover() -> None:
    """Legacy values не меняются, но не становятся разрешением на DDL."""
    assert "module_meta_documents" in MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER
    assert "module_instance_data_rows" in MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER
    assert "modules" in MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER
    assert "module_instances" in MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER
    for item in MIGRATION_CONTEXTS:
        assert item.removal_step.content_transferred is False
        assert item.removal_step.dropped_tables == MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER
    assert "без переноса" not in DATA_TRANSFER_POLICY
    for token in (
        "0802 dormant/not active",
        "Migration-only helpers preserved for fresh/historical upgrades",
        "code-first S4",
        "S5 review",
        "тогдашней current head",
        "schema-only downgrade",
    ):
        assert token in DATA_TRANSFER_POLICY


def test_mod_equipment_legacy_module_metadata_is_unchanged() -> None:
    for item in MIGRATION_CONTEXTS[:5]:
        assert item.removal_step.dropped_module == "mod_equipment"


def test_final_legacy_removal_slug_is_unchanged() -> None:
    # Opaque slug сохранён для behavior compatibility, не approved revision id.
    last = MIGRATION_CONTEXTS[-1]
    assert last.context_id == "mod_management"
    assert "declarative-app" in last.removal_step.dropped_module
    assert last.removal_step.alembic_revision == "drop_module_tables_final"


def test_offers_context_names_real_legacy_entrypoints() -> None:
    """Имена взяты чтением реальных файлов, а не выдуманы."""
    item = context_by_id("offers")
    assert item is not None
    names = item.legacy_entrypoints
    assert any("EquipmentPipelineService" in name for name in names)
    assert any("equipment_catalog_search.py" in name for name in names)


def test_tenders_context_names_real_legacy_entrypoints() -> None:
    item = context_by_id("tenders")
    assert item is not None
    names = item.legacy_entrypoints
    assert any("equipment_tender_readiness.py" in name for name in names)
    assert any("b2b/importer.py" in name for name in names)


def test_contexts_module_does_not_import_legacy_code() -> None:
    """Карта — ТОЛЬКО данные: ни одного импорта legacy-сервисов/страниц."""
    import prodavan.application.plugins.strangler.contexts as module

    source_names = set(vars(module))
    assert "EquipmentPipelineService" not in source_names
    assert "ModuleRuntime" not in source_names
    # Нет операций переноса/сверки.
    for forbidden in ("dual_read", "dual_write", "parity", "snapshot", "migrate_data"):
        assert forbidden not in source_names


def test_contexts_are_frozen_data() -> None:
    """Карта неизменяема (данные, а не изменяемое состояние)."""
    with pytest.raises((AttributeError, TypeError)):
        MIGRATION_CONTEXTS[0].context_id = "changed"  # type: ignore[misc]


def test_narrative_has_current_safety_boundaries_not_stale_runbook() -> None:
    narrative = _narrative_text()
    for token in (
        "2026110802",
        "dormant/not active",
        "CODE_FIRST_REMOVAL_INVENTORY",
        "code-first S4",
        "S5 ADR/review/acceptance",
        "тогдашней current head",
        "schema-only downgrade",
        "fresh/historical",
        "startup/deploy",
    ):
        assert token in narrative
    for forbidden in (
        "ПОСЛЕ применения ревизии",
        "alembic upgrade head",
        "СРАЗУ",
        "таблицы дропаются ревизией",
        "данные dev эфемерны",
        "drop_module_tables",
    ):
        assert forbidden not in narrative


def test_mod_management_helpers_remain_preserved_source_references() -> None:
    names = MIGRATION_CONTEXTS[-1].legacy_entrypoints
    for helper in (
        "application/platform/product_module_seeds.py::mod_management_meta",
        "application/platform/management_module_merge.py",
        "application/platform/bootstrap_config.py::DEFAULT_BASIC_MODULE_IDS",
        "domain/modules/types.py::GLOBAL_DEFAULT_PROJECT_MODULES",
    ):
        # Values намеренно сохранены: compatibility reference, не deletion target.
        assert helper in names
    narrative = _narrative_text()
    for token in (
        "fresh/historical",
        "management_module_merge",
        "deletion entrypoints",
    ):
        assert token in narrative
