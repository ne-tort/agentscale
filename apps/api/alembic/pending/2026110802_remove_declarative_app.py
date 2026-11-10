"""Неисполняемый historical/dormant inventory 2026110802, а НЕ Alembic revision.

Этот файл намеренно остаётся в alembic/pending и не является runbook. Он не
содержит Alembic revision/down_revision identifiers, upgrade/downgrade
entrypoints, imports Alembic op, DDL, DML или обращения к environment. Поэтому
даже ошибочное копирование не даёт готовой миграции.

ЗАПРЕЩЕНО переносить, копировать, регистрировать или активировать 2026110802 в
versions/version_locations. Номер сохранён только для исторической идентификации
устаревшего предложения. Никакой env flag не снимает этот запрет.

Старая схема drop-first и предположение о прежней фиксированной голове отменены.
Активная цепочка уже содержит более поздние revisions. Будущий schema contract
проектируется только после code-first S4 как НОВАЯ revision от тогдашней current
head, с отдельным S5 ADR/review/acceptance, schema-only downgrade и без env guard.

Сохраняются product_module_upsert/upsert_product_modules, product seeds,
mod_management_meta, seed_rows, management_module_merge, bootstrap constants и
historical revisions. Dynamic renderer/schema/protocol/policy/registry-client,
dynamic-apps, plugin_* и M05 m05_* не входят в deletion inventory.

HISTORICAL_MODULE_TABLE_INVENTORY — только бывший список platform module_* имён,
а не порядок операций и не разрешение на destructive cleanup. Lossy data
semantics подлежат отдельному S5 review.
"""

from __future__ import annotations


DORMANT_REVISION_ID = "2026110802"
DORMANT_CUTOVER_BARRIER = (
    "2026110802 — только historical/dormant inventory; move/copy/register/activate "
    "в Alembic versions или version_locations запрещены. Никакой env flag не "
    "разрешает активацию; финальная схема требует новой revision после S4/S5 gates."
)

FUTURE_CUTOVER_REQUIREMENTS: tuple[str, ...] = (
    "S4 code-first cleanup завершается до любого schema removal и имеет отдельные evidence.",
    "S5 требует отдельные ADR, review и acceptance; S2/S3/S5 не объединяются.",
    "Новая revision проектируется от тогдашней current head; 2026110802 не переиспользуется.",
    "Новая revision имеет schema-only downgrade без восстановления данных.",
    "Новая revision не использует PLUGIN_CUTOVER_CONFIRMED или другой env guard.",
)

HISTORICAL_MODULE_TABLE_INVENTORY: tuple[str, ...] = (
    "module_cabinet_bindings",
    "module_company_grants",
    "module_instance_data_rows",
    "module_instance_meta_documents",
    "module_instances",
    "module_meta_documents",
    "module_project_bindings",
    "modules",
)

PRESERVED_MIGRATION_ONLY_INVENTORY: tuple[str, ...] = (
    "apps/api/src/prodavan/application/platform/product_module_upsert.py",
    "apps/api/src/prodavan/application/platform/product_module_seeds.py "
    "(PRODUCT_MODULES, mod_management_meta, seed_rows)",
    "apps/api/src/prodavan/application/platform/management_module_merge.py",
    "apps/api/src/prodavan/application/platform/bootstrap_config.py::"
    "DEFAULT_BASIC_MODULE_IDS",
    "apps/api/src/prodavan/domain/modules/types.py::GLOBAL_DEFAULT_PROJECT_MODULES",
    "apps/api/alembic/versions/2026110321_management_module_merge.py",
)

PRESERVED_DYNAMIC_INVENTORY: tuple[str, ...] = (
    "apps/web/src/dynamic/declarative/renderer.ts",
    "apps/web/src/dynamic/declarative/README.md",
    "apps/web/src/dynamic/schema.ts",
    "apps/web/src/dynamic/protocol.ts",
    "apps/web/src/dynamic/policy.ts",
    "apps/web/src/dynamic/registry-client/**",
    "apps/api/src/prodavan/application/dynamic_app*/**",
    "apps/api/src/prodavan/domain/dynamic_apps/**",
    "apps/api/src/prodavan/infrastructure/persistence/models/dynamic_app*.py",
)

PRESERVED_PLUGIN_INVENTORY: tuple[str, ...] = (
    "apps/api/src/prodavan/infrastructure/persistence/models/"
    "plugin_instance_registry.py (M05)",
    "apps/api/alembic/versions/2026110303_m05_plugin_instance_registry.py",
    "m05_plugin_instances",
    "m05_plugin_instance_registry_links",
    "m05_plugin_instance_promotion_evidence",
)

__all__ = [
    "DORMANT_CUTOVER_BARRIER",
    "DORMANT_REVISION_ID",
    "FUTURE_CUTOVER_REQUIREMENTS",
    "HISTORICAL_MODULE_TABLE_INVENTORY",
    "PRESERVED_DYNAMIC_INVENTORY",
    "PRESERVED_MIGRATION_ONLY_INVENTORY",
    "PRESERVED_PLUGIN_INVENTORY",
]
