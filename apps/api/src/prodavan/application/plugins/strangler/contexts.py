"""Карта переноса ``mod_equipment`` по bounded context'ам (F10, §8 этап 4).

**Это ТОЛЬКО данные.** Модуль ничего не импортирует из legacy-кода, ничего не
вызывает и не меняет существующие сервисы/страницы. Имена в ``legacy_entrypoints``
получены чтением (glob/grep) реальных файлов:

* ``apps/api/src/prodavan/application/modules/equipment_*.py`` и
  ``application/modules/module_*.py``;
* ``apps/api/src/prodavan/api/v1/b2b.py``, ``application/b2b/importer.py``,
  ``application/tenders/workflow.py``, ``application/mcp/prodavan_equipment_mcp/server.py``;
* ``apps/web/src/features/equipment/**`` и ``apps/web/src/routes/pages.tsx``.

Порядок переноса — ровно по §8 этап 4: **offers → prices → budget → documents →
tenders**, затем (§8 этап 6) ``mod_management`` последним. Каждый контекст
переводится за фича-флагом ``plugin_enabled_modules[]``; старый путь просто
выключается, **сравнивать нечего** (данные dev эфемерны, §8 / §13.1).

ЯВНАЯ ПОМЕТКА О ДАННЫХ
======================
Все таблицы ``module_*`` платформенной БД (``modules``,
``module_meta_documents``, ``module_instances``,
``module_instance_meta_documents``, ``module_instance_data_rows``,
``module_company_grants``, ``module_cabinet_bindings``,
``module_project_bindings``) дропаются ревизией Alembic **БЕЗ ПЕРЕНОСА
СОДЕРЖИМОГО**. Строки ``module_instance_data_rows`` (JSONB-строки declarative-app)
не читаются, не выгружаются, не бэкапируются, не перекладываются в таблицы плагина.
Тестовые данные создаются заново сидом плагина (§8). Никаких dual-read/dual-write,
snapshot, метрик парности или «недель наблюдения» — см. §13.1.

Плагин владеет своей схемой ``module_<plugin_id>`` в tenant-БД компании и
мигрирует её сам (§2, §3); это **другие** таблицы, не одноимённые legacy-таблицы
платформенной БД. Имена совпадают только префиксом, что отражено в
``RemovalStep.dropped_tables`` (платформенные) и ``plugin_capability`` (схема
плагина).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RemovalStep:
    """Что удаляется ревизией Alembic после переноса контекста.

    ``alembic_revision`` — метка/слаг ревизии (конкретный id назначает родитель
    при F11; здесь намеренно не выдумывается числовой id, чтобы не конфликтовать
    с чужой работой). ``dropped_table_slugs`` — ``table_slug`` значения внутри
    ``module_instance_data_rows`` для ``mod_equipment`` (реальные константы из
    ``equipment_offers_service.py``).
    """

    alembic_revision: str
    dropped_module: str
    dropped_tables: tuple[str, ...]
    dropped_table_slugs: tuple[str, ...]
    #: Явная фиксация: содержимое НЕ переносится (§8 этап 6, §13.1).
    content_transferred: bool = False


@dataclass(frozen=True, slots=True)
class ContextMigration:
    """Один bounded context переноса ``mod_equipment``."""

    context_id: str
    #: Реально существующие сервисы/страницы (имена из прочитанных файлов).
    legacy_entrypoints: tuple[str, ...]
    #: Capability плагина, закрывающая контекст в его собственной tenant-схеме.
    plugin_capability: str
    removal_step: RemovalStep


#: Все платформенные ``module_*`` таблицы declarative-app. Дропаются БЕЗ переноса.
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

#: Текстовая фиксация политики (дублирует docstring для проверок/отчётов).
DATA_TRANSFER_POLICY: str = (
    "module_* таблицы дропаются ревизией Alembic без переноса содержимого; "
    "данные dev эфемерны, создаются заново сидом плагина"
)

#: Общая страница/рантайм, общие для всех контекстов (§8 «в finale»).
SHARED_LEGACY_ENTRYPOINTS: tuple[str, ...] = (
    "apps/web/src/features/equipment/EquipmentProcurementPage.tsx::EquipmentProcurementPage",
    "apps/web/src/routes/pages.tsx::EquipmentPage",
    "apps/web/src/features/modules/ModuleRuntime.tsx::ModuleRuntime",
)


# Порядок ЗАФИКСИРОВАН §8 этап 4; не переставлять.
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
    # ------------------------------------------- 6. mod_management (§8 этап 6)
    ContextMigration(
        context_id="mod_management",
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


#: Порядок context_id для проверок/отчётов (§8 этап 4 → этап 6).
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
