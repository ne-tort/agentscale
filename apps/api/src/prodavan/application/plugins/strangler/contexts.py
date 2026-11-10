"""Historical data-only bounded-context inventory с текущими safety boundaries.

Имена полей, values и порядок records сохранены для поведенческой совместимости.
Модуль ничего не импортирует из legacy services, не вызывает Alembic, не меняет
флаги, таблицы, routes или call flow. ``RemovalStep`` и его ``dropped_*`` поля —
inert historical metadata, а не план destructive migration.

``legacy_entrypoints`` — compatibility source references, а не deletion list.
В частности, product upsert/seeds, ``mod_management_meta``, ``seed_rows``,
``management_module_merge`` и bootstrap constants сохраняются для fresh/historical
upgrades. Их присутствие в historical tuple не делает их cutover entrypoints.

``2026110802`` — dormant/not active и не активируется. Никакого runtime cutover
или startup/deploy schema removal эти records не выполняют. Любая будущая
destructive revision требует завершённого code-first S4, отдельного S5
ADR/review/acceptance и создаётся заново от тогдашней current head со
schema-only downgrade и без env guard.

Исторические имена ``MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER`` и
``DATA_TRANSFER_POLICY`` сохранены как compatibility contracts. Актуальный канон —
code-first expand/contract A→B и отдельные S4/S5 decisions, а не старый §8 drop plan.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RemovalStep:
    """Inert historical removal metadata, сохранённое для compatibility.

    ``alembic_revision`` — opaque legacy slug, а не active или approved revision id.
    ``dropped_tables`` и ``dropped_table_slugs`` — historical names, а не DDL
    instructions. Значения не вызывают Alembic и не разрешают destructive cutover.
    Новая revision возможна только после code-first S4 и отдельного S5 review от
    тогдашней current head; ``2026110802`` остаётся dormant/not active.
    """

    alembic_revision: str
    dropped_module: str
    dropped_tables: tuple[str, ...]
    dropped_table_slugs: tuple[str, ...]
    #: Compatibility boolean, а не текущая data-transfer или cutover policy.
    content_transferred: bool = False


@dataclass(frozen=True, slots=True)
class ContextMigration:
    """Data-only bounded-context record; не исполняемый runtime cutover."""

    context_id: str
    #: Compatibility source references, не deletion list; helpers сохраняются.
    legacy_entrypoints: tuple[str, ...]
    #: Capability плагина, закрывающая контекст в его собственной tenant-схеме.
    plugin_capability: str
    #: Inert historical metadata; не вызывает Alembic и не активирует 0802.
    removal_step: RemovalStep


#: Historical compatibility name/tuple; не разрешение на DROP или runtime cutover.
MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER: tuple[str, ...] = (
    "modules",
    "module_meta_documents",
    "module_company_grants",
    "module_cabinet_bindings",
    "module_project_bindings",
    "module_instances",
    "module_instance_meta_documents",
    "module_instance_data_rows",
)

#: Narrative-only compatibility text; operational tables/flags/call flow не меняются.
DATA_TRANSFER_POLICY: str = (
    "0802 dormant/not active; runtime cutover и destructive revisions запрещены. "
    "Migration-only helpers preserved for fresh/historical upgrades. Будущая "
    "schema revision — только после code-first S4 и отдельного S5 review от "
    "тогдашней current head, со schema-only downgrade и без env guard"
)

#: Compatibility source references, а не deletion/cutover entrypoints.
SHARED_LEGACY_ENTRYPOINTS: tuple[str, ...] = (
    "apps/web/src/features/equipment/EquipmentProcurementPage.tsx::EquipmentProcurementPage",
    "apps/web/src/routes/pages.tsx::EquipmentPage",
    "apps/web/src/features/modules/ModuleRuntime.tsx::ModuleRuntime",
)


# Historical order сохранён для behavior compatibility, не как execution plan.
MIGRATION_CONTEXTS: tuple[ContextMigration, ...] = (
    # ---------------------------------------------------------------- 1. offers
    ContextMigration(
        context_id="offers",
        legacy_entrypoints=(
            "equipment_offers_service.py::EquipmentPipelineService",
            "equipment_offers_service.py::ModuleRowIO",
            "equipment_offers_service.py::search_catalog_docs",
            "equipment_offers_service.py::best_price_from_docs",
            "equipment_catalog_search.py::search_catalogs",
            "equipment_catalog_opensearch.py::canonical_doc_mappings",
            "equipment_catalog_reindex_sweep.py::sweep_due_equipment_catalogs",
            "equipment_ready_builds_service.py::ReadyBuildsPipelineService",
            "equipment_ready_builds_sweep.py::sweep_ready_builds",
            "web_products_opensearch.py / web_products_ttl_sweep.py",
            "equipment_search.py::EquipmentSearchService",
            "mcp/prodavan_equipment_mcp/server.py (catalog-sources, catalog-search)",
        ),
        plugin_capability="mod_equipment.offers (db.direct: found_groups/found_offers/catalogs)",
        removal_step=RemovalStep(
            alembic_revision="drop_module_tables_after_offers",
            dropped_module="mod_equipment",
            dropped_tables=MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER,
            dropped_table_slugs=(
                "found_groups",
                "found_offers",
                "request_lines",
                "catalogs",
                "part_cards",
                "ready_builds",
                "ready_build_slots",
                "equipment_builds",
                "web_products",
                "trusted_sellers",
            ),
        ),
    ),
    # ---------------------------------------------------------------- 2. prices
    ContextMigration(
        context_id="prices",
        legacy_entrypoints=(
            "equipment_master_price.py::MasterPriceService",
            "equipment_master_price.py::build_master_price_workbook",
            "equipment_master_price_manual.py",
            "equipment_fx.py::convert_offer_price / apply_fx_to_offer_body",
            "equipment_catalog_search.py::parse_price",
            "equipment_search.py::build_equipment_search_query",
        ),
        plugin_capability="mod_equipment.prices (db.direct: master_price/fx_rates/margins)",
        removal_step=RemovalStep(
            alembic_revision="drop_module_tables_after_prices",
            dropped_module="mod_equipment",
            dropped_tables=MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER,
            dropped_table_slugs=(
                "master_price",           # виртуальная таблица (OpenSearch)
                "equipment_search",       # виртуальная таблица (OpenSearch)
                "master_price_manual",
                "master_price_mail",
            ),
        ),
    ),
    # ---------------------------------------------------------------- 3. budget
    ContextMigration(
        context_id="budget",
        legacy_entrypoints=(
            "equipment_budget.py::fill_budget_workbook",
            "equipment_budget.py::fill_kp_workbook",
            "equipment_budget.py::build_budget_fill / build_kp_fill",
            "equipment_offers_service.py::EquipmentPipelineService (бюджетные строки)",
        ),
        plugin_capability="mod_equipment.budget (db.direct: budget_lines/markup/vat)",
        removal_step=RemovalStep(
            alembic_revision="drop_module_tables_after_budget",
            dropped_module="mod_equipment",
            dropped_tables=MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER,
            dropped_table_slugs=("budget_lines", "procurement"),
        ),
    ),
    # ------------------------------------------------------------- 4. documents
    ContextMigration(
        context_id="documents",
        legacy_entrypoints=(
            "equipment_docs_render.py::build_kp_pdf / build_spec_pdf",
            "equipment_docs_render.py::build_kp_xlsx / build_spec_xlsx",
            "equipment_docs_render.py::build_doc_model / DocumentFields",
            "documents_file_ref.py",
            "equipment_mail.py",
        ),
        plugin_capability="mod_equipment.documents (objects/docs: КП, спецификация, document_fields)",
        removal_step=RemovalStep(
            alembic_revision="drop_module_tables_after_documents",
            dropped_module="mod_equipment",
            dropped_tables=MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER,
            dropped_table_slugs=("document_fields", "document_company_fields"),
        ),
    ),
    # --------------------------------------------------------------- 5. tenders
    ContextMigration(
        context_id="tenders",
        legacy_entrypoints=(
            "equipment_tender_readiness.py::assess_readiness",
            "application/tenders/workflow.py",
            "application/b2b/importer.py",
            "api/v1/b2b.py",
            "core/jobs/tasks.py (B2B-Center импорт тендеров)",
            "domain/tenders.py",
        ),
        plugin_capability="mod_equipment.tenders (events/db.direct: tenders/candidate_groups)",
        removal_step=RemovalStep(
            alembic_revision="drop_module_tables_after_tenders",
            dropped_module="mod_equipment",
            dropped_tables=MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER,
            dropped_table_slugs=("tenders",),
        ),
    ),
    # -------------------------------------- 6. mod_management historical record
    ContextMigration(
        context_id="mod_management",
        # Helpers ниже preserved for fresh/historical upgrades, НЕ deletion targets.
        legacy_entrypoints=(
            "application/platform/product_module_seeds.py::mod_management_meta",
            "application/platform/management_module_merge.py",
            "application/platform/bootstrap_config.py::DEFAULT_BASIC_MODULE_IDS",
            "domain/modules/types.py::GLOBAL_DEFAULT_PROJECT_MODULES",
            "apps/api/tests/.../integration.acceptance.test.tsx (mod_management runtime)",
            "apps/web/src/features/modules/ModuleRuntime.tsx::ModuleRuntime",
        ),
        plugin_capability="mod_management (db.direct: управление/настройки компании)",
        removal_step=RemovalStep(
            alembic_revision="drop_module_tables_final",
            dropped_module="declarative-app (mod_management + mod_equipment)",
            dropped_tables=MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER,
            dropped_table_slugs=(),
        ),
    ),
)


#: Historical context order для behavior compatibility, не для runtime cutover.
CONTEXT_ORDER: tuple[str, ...] = tuple(item.context_id for item in MIGRATION_CONTEXTS)


def context_by_id(context_id: str) -> ContextMigration | None:
    """Найти контекст по id (``None`` — если id нет в карте)."""
    for item in MIGRATION_CONTEXTS:
        if item.context_id == context_id:
            return item
    return None


__all__ = [
    "CONTEXT_ORDER",
    "DATA_TRANSFER_POLICY",
    "MIGRATION_CONTEXTS",
    "MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER",
    "SHARED_LEGACY_ENTRYPOINTS",
    "ContextMigration",
    "RemovalStep",
    "context_by_id",
]
