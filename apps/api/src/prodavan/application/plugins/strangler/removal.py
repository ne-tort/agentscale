"""Historical/dormant inventory для code-first review, а НЕ runbook.

Этот модуль не выполняет удаление, не содержит DDL/DML и не подключается к
startup/deploy. Пути ниже — только кандидаты для будущего независимого S4
review после доказанных S2/S3. Их наличие не является разрешением на удаление.

2026110802 — необратимо устаревший historical/dormant артефакт. Его запрещено
переносить, копировать, регистрировать в Alembic или активировать. Он не является
кандидатом на финальную schema revision и не должен становиться частью цепочки.

Порядок остаётся code-first expand/contract A→B: сначала отдельный S4 удаляет
старые runtime readers/writers/routes/imports и согласует ORM metadata, затем
отдельный S5 ADR/review/acceptance проектирует НОВУЮ revision от тогдашней
current head. Финальная revision требует schema-only downgrade и не использует
env guard. Schema-first и drop-first запрещены.

Migration-only helpers, applied/historical revisions, dynamic inventory и M05
не входят в deletion candidates и сохраняются для fresh/historical upgrades.
"""

from __future__ import annotations

from dataclasses import dataclass


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


@dataclass(frozen=True, slots=True)
class RemovalChecklistItem:
    """Один inventory item для review; поле action не является исполняемой командой."""

    group: str
    target: str
    action: str
    reference: str


#: Только каталог кандидатов для будущего S4 review, не последовательность операций.
#: Любой item требует нового dependency/import-graph review на момент S4.
CODE_FIRST_REMOVAL_INVENTORY: tuple[RemovalChecklistItem, ...] = (
    # ---------------------------------------------------------------- 1. ORM
    RemovalChecklistItem(
        group="orm-models",
        target="8 ORM-классов module_* (ModuleRow, ModuleMetaDocumentRow, "
               "ModuleCompanyGrantRow, ModuleCabinetBindingRow, "
               "ModuleProjectBindingRow, ModuleInstanceRow, "
               "ModuleInstanceMetaDocumentRow, ModuleInstanceDataRow)",
        action="S4 review candidate: рассмотреть models/modules.py только после "
               "доказанного отсутствия runtime readers/writers; historical imports "
               "и fresh/historical upgrade compatibility обязаны сохраняться.",
        reference="apps/api/src/prodavan/infrastructure/persistence/models/modules.py",
    ),
    RemovalChecklistItem(
        group="orm-models",
        target="Импорты 8 модульных Row-классов и записи Module*Row в __all__",
        action="S4 review candidate: согласовать реестр metadata с фактически "
               "оставшимися consumers; никакого намеренного ORM/schema drift.",
        reference="apps/api/src/prodavan/infrastructure/persistence/models/__init__.py",
    ),
    RemovalChecklistItem(
        group="orm-models",
        target="Импорты Module*Row в сервисах, читающих module_* напрямую",
        action="S4 review candidate: пересмотреть application/agent/session_service.py, "
               "application/content/{asset_link_service,access_policy}.py, "
               "application/platform/{bootstrap_service,seed_mcp_bootstrap_service}.py, "
               "application/tenant_infra/pod_modules.py, "
               "application/project_service/module_settings.py, "
               "application/projects/{materialize_planner,workspace_sync_policy}.py "
               "и application/modules/web_products_ttl_sweep.py.",
        reference="apps/api/src/prodavan/**/*service*.py, **/materialize_planner.py",
    ),
    # ------------------------------------------------------------ 2. Роутеры API
    RemovalChecklistItem(
        group="api-routers",
        target="admin_modules.router (CRUD модулей/мета-документов/экшенов)",
        action="S4 review candidate: рассмотреть include_router и admin_modules.py "
               "только после per-context S3 evidence и dependency review.",
        reference="apps/api/src/prodavan/api/v1/admin_modules.py",
    ),
    RemovalChecklistItem(
        group="api-routers",
        target="company_modules.py, pod_modules.py",
        action="S4 review candidate: рассмотреть company/pod module API и их "
               "include_router после доказанного отсутствия рабочих consumers.",
        reference="apps/api/src/prodavan/api/v1/{company_modules,pod_modules}.py",
    ),
    RemovalChecklistItem(
        group="api-routers",
        target="module-эндпоинты внутри cabinets.py / projects.py / external.py",
        action="S4 review candidate: проверить обработчики и imports "
               "module_binding_service, module_action_executor, ModuleRowIO.",
        reference="apps/api/src/prodavan/api/v1/{cabinets,projects,external}.py",
    ),
    RemovalChecklistItem(
        group="api-routers",
        target="admin_search_index.py (equipment_catalog_opensearch)",
        action="S4 review candidate: legacy indexing path рассматривается только "
               "после доказанного plugin replacement и сохранения S1 behavior.",
        reference="apps/api/src/prodavan/api/v1/admin_search_index.py",
    ),
    # -------------------------------------------------------------- 3. Frontend
    RemovalChecklistItem(
        group="frontend-pages",
        target="EquipmentProcurementPage + EquipmentPage",
        action="S4 review candidate: проверить EquipmentPage и features/equipment/** "
               "по актуальному import graph и browser acceptance, не по старым строкам.",
        reference="apps/web/src/routes/pages.tsx::EquipmentPage, "
                  "apps/web/src/features/equipment/EquipmentProcurementPage.tsx",
    ),
    RemovalChecklistItem(
        group="frontend-pages",
        target="ModuleRuntime.tsx + ModuleRuntimePage / createModuleRuntimeService",
        action="S4 review candidate: проверить features/modules/** и routes/pages.tsx "
               "после S3 evidence; сохранённые dynamic renderer paths не затрагиваются.",
        reference="apps/web/src/features/modules/ModuleRuntime.tsx, "
                  "apps/web/src/routes/pages.tsx",
    ),
    # --------------------------------------------------------------- 4. Сервисы
    RemovalChecklistItem(
        group="services",
        target="application/modules/** legacy runtime services",
        action="S4 review candidate: заново построить inventory module_service, "
               "module_meta_service, module_instance_service, binding/materialize/"
               "action/equipment/web_products/chat_scope/document services и assets; "
               "migration-only helpers в этот candidate scope не входят.",
        reference="apps/api/src/prodavan/application/modules/**",
    ),
    RemovalChecklistItem(
        group="services",
        target="Потребители application.modules.* вне каталога",
        action="S4 review candidate: проверить импорты/вызовы в application, "
               "core/jobs и api/v1 по актуальному source graph; никакого удаления "
               "по историческому списку без нового review.",
        reference="apps/api/src/prodavan/application/**, core/jobs/**, api/v1/**",
    ),
    # ------------------------------------------------------------- 5. Jobs
    RemovalChecklistItem(
        group="runtime-jobs",
        target="Celery beat/sweep и MCP equipment legacy runtime",
        action="S4 review candidate: рассмотреть equipment_*_sweep, "
               "web_products_ttl_sweep и prodavan_equipment_mcp только после "
               "проверки replacement jobs и обоих enabled entrypoints.",
        reference="apps/api/src/prodavan/core/jobs/tasks.py, "
                  "application/mcp/prodavan_equipment_mcp/server.py",
    ),
    # ---------------------------------------------------- 6. Tests / acceptance
    RemovalChecklistItem(
        group="tests-and-acceptance",
        target="Тесты legacy module runtime и acceptance-страницы",
        action="S4 review candidate: переписать зависимые tests вместе с runtime "
               "changes; dormant inventory guards и dynamic guards сохраняются.",
        reference="apps/web/src/features/equipment/**/*.test.tsx, "
                  "apps/api/tests/integration/test_shell_nav_meta.py",
    ),
    RemovalChecklistItem(
        group="tests-and-acceptance",
        target="Согласованность ORM metadata и финальной схемы",
        action="S5 acceptance criterion: после отдельно approved schema contract "
               "не должно быть намеренного metadata drift; проверки выполняются "
               "только после снятия verification freeze.",
        reference="apps/api/alembic/env.py::target_metadata",
    ),
)


#: Эти helpers и historical revisions НЕ являются deletion candidates.
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


#: Сохраняемые dynamic paths, а НЕ кандидаты на удаление.
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


#: M05 и связанные registry tables сохраняются независимо от legacy cleanup.
PRESERVED_PLUGIN_INVENTORY: tuple[str, ...] = (
    "apps/api/src/prodavan/infrastructure/persistence/models/"
    "plugin_instance_registry.py (M05)",
    "apps/api/alembic/versions/2026110303_m05_plugin_instance_registry.py",
    "m05_plugin_instances",
    "m05_plugin_instance_registry_links",
    "m05_plugin_instance_promotion_evidence",
)


#: Renderer-only tests и source-graph guard сохраняются после legacy cleanup.
PRESERVED_DYNAMIC_GUARDS: tuple[str, ...] = (
    "apps/web/src/dynamic/declarative/renderer.test.ts",
    "apps/web/src/dynamic/legacy-host-import-graph.test.ts",
)


#: Машинно читаемые границы для static tests и будущего отдельного review.
REMOVAL_BOUNDARIES: tuple[str, ...] = (
    DORMANT_CUTOVER_BARRIER,
    *FUTURE_CUTOVER_REQUIREMENTS,
    "CODE_FIRST_REMOVAL_INVENTORY не исполняется и не вызывается из startup/deploy.",
    "Applied migrations не редактируются; historical imports остаются воспроизводимыми.",
    "PRESERVED_MIGRATION_ONLY_INVENTORY сохраняется: product_module_upsert/"
    "upsert_product_modules, product seeds/mod_management_meta/seed_rows, "
    "management_module_merge и bootstrap constants не удаляются.",
    "Плагиновые plugin_* и M05 m05_* таблицы не являются legacy deletion targets.",
    "tenant-БД tenant_<company> и схемы module_<plugin_id> не затрагиваются.",
    "companies, projects, cabinet_instances и dynamic_app_* не затрагиваются.",
    "PRESERVED_DYNAMIC_INVENTORY сохраняет renderer, schema/protocol/policy, "
    "registry-client и dynamic-apps; автоматическое удаление declarative/** запрещено.",
    "PRESERVED_DYNAMIC_GUARDS сохраняются и не заменяют browser/live acceptance.",
    "Lossy data semantics требуют явного S5 review; historical inventory не является "
    "разрешением на destructive DDL или на отсутствие schema-only downgrade.",
)


def checklist_by_group(group: str) -> tuple[RemovalChecklistItem, ...]:
    """Вернуть read-only inventory candidates группы для статического review."""

    return tuple(item for item in CODE_FIRST_REMOVAL_INVENTORY if item.group == group)


__all__ = [
    "CODE_FIRST_REMOVAL_INVENTORY",
    "DORMANT_CUTOVER_BARRIER",
    "DORMANT_REVISION_ID",
    "FUTURE_CUTOVER_REQUIREMENTS",
    "PRESERVED_DYNAMIC_GUARDS",
    "PRESERVED_DYNAMIC_INVENTORY",
    "PRESERVED_MIGRATION_ONLY_INVENTORY",
    "PRESERVED_PLUGIN_INVENTORY",
    "REMOVAL_BOUNDARIES",
    "RemovalChecklistItem",
    "checklist_by_group",
]
