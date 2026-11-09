"""Чек-лист удаления declarative-app ПОСЛЕ ``alembic upgrade head`` (§8 этап 6).

ЭТО НЕ ИСПОЛНЯЕМЫЙ КОД, А ЯВНЫЙ КОНТРАКТ/ЧЕК-ЛИСТ ДЛЯ РОДИТЕЛЯ.
=================================================================

Ревизия ``2026110802_remove_declarative_app.py`` дропает 8 платформенных
таблиц ``module_*`` **БЕЗ переноса содержимого** (данные dev эфемерны, §8/§13.1).
НО до реального cutover'а ORM-модели, роутеры и страницы намеренно ОСТАЮТСЯ:
другие подсистемы ещё читают эти таблицы. Сразу ПОСЛЕ применения ревизии к
кластеру родитель обязан удалить код из чек-листа ниже — иначе любой запрос к
удалённой таблице упадёт с ``UndefinedTable``, а ``alembic check`` будет
показывать drift (модели есть, таблиц нет).

ВАЖНО: сейчас ревизию ПРИМЕНЯТЬ НЕЛЬЗЯ (пилот не подтверждён, VM недоступна).
Этот модуль — только подготовленный чек-лист, ничего не удаляется и не
импортируется в рантайм.

ЗАЩИТА ОТ АВТОМАТИЧЕСКОГО ПРИМЕНЕНИЯ (fail-closed)
===================================================
CI Gate и initContainer ``scripts/migrate.sh`` вызывают ``alembic upgrade head``
на КАЖДОМ деплое. Поэтому ревизия лежит в ``alembic/pending/``, а НЕ в
``alembic/versions/``: Alembic не сканирует pending/, рабочая голова остаётся
``2026110801`` и обычный деплой НЕ применяет cutover. Дополнительно
``upgrade()`` требует ``PLUGIN_CUTOVER_CONFIRMED=true``; без неё (и при любом
другом значении) бросает ``RuntimeError`` ДО первого DROP и НЕ трогает таблицы
(не «тихий pass»). Порядок реального cutover'а для родителя:

1. Дождаться подтверждённого пилота в кластере и переключения
   ``plugin_enabled_modules[]`` (§8 этапы 2 и 5).
2. ПЕРЕНЕСТИ ``alembic/pending/2026110802_remove_declarative_app.py`` в
   ``alembic/versions/`` (до этого Alembic её не видит).
3. Выставить ``PLUGIN_CUTOVER_CONFIRMED=true`` ТОЛЬКО в окружении job'а/пода
   миграции, отдельным осознанным заходом (НЕ глобально в образе/CI).
4. Выполнить ``alembic upgrade head`` (с флагом дроп пройдёт).
5. СРАЗУ применить чек-лист ниже (удалить ORM/роутеры/страницы/сервисы/сиды).
6. Снять флаг и повторить ``alembic check``: drift по ``module_*`` исчезнет.
   Откат = выключить фича-флаг, НЕ ``alembic downgrade`` (он запрещён).

Все пути ниже получены чтением реальных файлов (glob/grep), не выдуманы.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RemovalChecklistItem:
    """Один пункт чек-листа: что удалить, где, и почему именно после upgrade."""

    group: str
    target: str
    action: str
    #: Ссылка на реальный файл/символ (для grep при выполнении cutover'а).
    reference: str


#: Что делает родитель ПОСЛЕ ``alembic upgrade head`` (revision 2026110802).
#: Порядок групп: сначала убрать точки входа (роутеры/страницы), затем сервисы,
#: затем ORM-модели + реестр, затем сиды/задачи. Данные НЕ переносим, НЕ бэкапим.
POST_UPGRADE_REMOVAL_CHECKLIST: tuple[RemovalChecklistItem, ...] = (
    # ---------------------------------------------------------------- 1. ORM
    RemovalChecklistItem(
        group="orm-models",
        target="8 ORM-классов module_* (ModuleRow, ModuleMetaDocumentRow, "
               "ModuleCompanyGrantRow, ModuleCabinetBindingRow, "
               "ModuleProjectBindingRow, ModuleInstanceRow, "
               "ModuleInstanceMetaDocumentRow, ModuleInstanceDataRow)",
        action="Удалить файл целиком. До cutover'а НЕ трогать: Base.metadata "
               "должен видеть таблицы, иначе чужие подсистемы упадут раньше времени.",
        reference="apps/api/src/prodavan/infrastructure/persistence/models/modules.py",
    ),
    RemovalChecklistItem(
        group="orm-models",
        target="Импорты 8 модульных Row-классов и 8 строк в __all__",
        action="Убрать блок ``from ...models.modules import (...)`` и записи "
               "Module*Row из ``__all__`` (строки 85-94 и 282-289).",
        reference="apps/api/src/prodavan/infrastructure/persistence/models/__init__.py",
    ),
    RemovalChecklistItem(
        group="orm-models",
        target="Импорты Module*Row в сервисах, читающих module_* напрямую",
        action="Удалить/переписать вместе с самими сервисами (см. группу services). "
               "Реальные файлы: application/agent/session_service.py, "
               "application/content/{asset_link_service,access_policy}.py, "
               "application/platform/{bootstrap_service,seed_mcp_bootstrap_service}.py, "
               "application/tenant_infra/pod_modules.py, "
               "application/project_service/module_settings.py, "
               "application/projects/{materialize_planner,workspace_sync_policy}.py, "
               "application/modules/web_products_ttl_sweep.py.",
        reference="apps/api/src/prodavan/**/*service*.py, **/materialize_planner.py",
    ),
    # ------------------------------------------------------------ 2. Роутеры API
    RemovalChecklistItem(
        group="api-routers",
        target="admin_modules.router (CRUD модулей/мета-документов/экшенов)",
        action="Удалить include_router в api/v1/router.py (строка 70) и сам файл. "
               "Это главная точка входа declarative-app.",
        reference="apps/api/src/prodavan/api/v1/admin_modules.py",
    ),
    RemovalChecklistItem(
        group="api-routers",
        target="company_modules.py, pod_modules.py",
        action="Удалить роутеры company/pod module API и их include_router.",
        reference="apps/api/src/prodavan/api/v1/{company_modules,pod_modules}.py",
    ),
    RemovalChecklistItem(
        group="api-routers",
        target="module-эндпоинты внутри cabinets.py / projects.py / external.py",
        action="Убрать обработчики, импортирующие application.modules.* "
               "(module_binding_service, module_action_executor, ModuleRowIO).",
        reference="apps/api/src/prodavan/api/v1/{cabinets,projects,external}.py",
    ),
    RemovalChecklistItem(
        group="api-routers",
        target="admin_search_index.py (equipment_catalog_opensearch)",
        action="УбратьLegacy-путь индексации equipment, если не переведён на плагин.",
        reference="apps/api/src/prodavan/api/v1/admin_search_index.py",
    ),
    # -------------------------------------------------------------- 3. Frontend
    RemovalChecklistItem(
        group="frontend-pages",
        target="EquipmentProcurementPage + EquipmentPage",
        action="Удалить импорт (pages.tsx:14), компонент EquipmentPage "
               "(pages.tsx:253-255) и весь каталог features/equipment/**. "
               "Это артефакт FINAL_STATE_ARTIFACTS в cutover.py.",
        reference="apps/web/src/routes/pages.tsx::EquipmentPage, "
                  "apps/web/src/features/equipment/EquipmentProcurementPage.tsx",
    ),
    RemovalChecklistItem(
        group="frontend-pages",
        target="ModuleRuntime.tsx + ModuleRuntimePage / createModuleRuntimeService",
        action="Удалить импорт (pages.tsx:18-19), страницу (pages.tsx:~364-368) "
               "и каталог features/modules/**. Артефакт §8 "
               "«ModuleRuntime.tsx / generic runtime».",
        reference="apps/web/src/features/modules/ModuleRuntime.tsx, "
                  "apps/web/src/routes/pages.tsx",
    ),
    RemovalChecklistItem(
        group="frontend-pages",
        target="declarative renderer (generic runtime)",
        action="Удалить apps/web/src/dynamic/declarative/** "
               "(renderer.ts, host.tsx, index.ts, README.md, acceptance.test.tsx). "
               "Префикс ``module_`` в tenant-схемах плагинов НЕ удалять.",
        reference="apps/web/src/dynamic/declarative/**",
    ),
    # --------------------------------------------------------------- 4. Сервисы
    RemovalChecklistItem(
        group="services",
        target="application/modules/** (весь каталог, ~30 файлов)",
        action="Удалить каталог целиком: module_service, module_meta_service, "
               "module_instance_service, module_binding_service, "
               "module_materialize_service, owner_module_data_service, "
               "module_action_executor, equipment_* (offers/budget/prices/docs/"
               "search/tender_readiness), web_products_*, chat_scope*, "
               "documents_file_ref, assets/. Это ~9.7k строк §8.",
        reference="apps/api/src/prodavan/application/modules/**",
    ),
    RemovalChecklistItem(
        group="services",
        target="Потребители application.modules.* вне каталога",
        action="Убрать импорты/вызовы в application/{b2b/importer.py, "
               "tenders/workflow.py, admin/company_service.py, "
               "cabinets/{instance_service,cabinet_module_service}.py, "
               "project_service/{command,module_settings}.py, "
               "projects/{materialize,rematerialize_scheduler,materialize_planner,"
               "project_runtime_module_service}.py, tenant_infra/{pod_modules,"
               "lifecycle,equipment_catalog_search_service}.py, "
               "agent/project_bind.py, content/module_upload_service.py, "
               "search_index/employee_query.py, platform/bootstrap_service.py}, "
               "application/mcp/prodavan_equipment_mcp/server.py, "
               "core/jobs/{tasks,enqueue}.py.",
        reference="apps/api/src/prodavan/application/**, core/jobs/**, api/v1/**",
    ),
    # ------------------------------------------------------- 5. Сиды / задачи
    RemovalChecklistItem(
        group="seeds-and-jobs",
        target="Сиды модулей и bootstrap-константы",
        action="Удалить product_module_seeds.py::mod_management_meta, "
               "management_module_merge.py, DEFAULT_BASIC_MODULE_IDS, "
               "GLOBAL_DEFAULT_PROJECT_MODULES; старые data-ревизии "
               "(2026082704/2026082711/2026090504 и UPDATE к module_*) оставить "
               "в истории как есть (ревизии не правим).",
        reference="apps/api/src/prodavan/application/platform/product_module_seeds.py, "
                  "application/platform/bootstrap_config.py, domain/modules/types.py",
    ),
    RemovalChecklistItem(
        group="seeds-and-jobs",
        target="Celery beat/sweep и MCP equipment",
        action="Убрать задачи equipment_*_sweep / web_products_ttl_sweep из "
               "core/jobs/tasks.py (строки ~354-463) и MCP-сервер "
               "application/mcp/prodavan_equipment_mcp/**.",
        reference="apps/api/src/prodavan/core/jobs/tasks.py, "
                  "application/mcp/prodavan_equipment_mcp/server.py",
    ),
    # --------------------------------------------------------- 6. Тесты / финал
    RemovalChecklistItem(
        group="tests-and-final",
        target="Тесты declarative-app и acceptance-страницы",
        action="Удалить/переписать тесты, зависящие от module_* и "
               "EquipmentProcurementPage/ModuleRuntime; НЕ удалять "
               "tests/unit/plugins/persistence/test_removal_revision.py "
               "(он проверяет саму ревизию).",
        reference="apps/web/src/features/equipment/**/*.test.tsx, "
                  "apps/web/src/dynamic/declarative/acceptance.test.tsx, "
                  "apps/api/tests/integration/test_shell_nav_meta.py",
    ),
    RemovalChecklistItem(
        group="tests-and-final",
        target="alembic check drift",
        action="После удаления ORM-моделей и реестра повторно запустить "
               "``alembic check``: drift по ``module_*`` должен ИСЧЕЗНУТЬ. "
               "Пока модели остаются, drift — ОЖИДАЕМ и нормален (см. отчёт).",
        reference="apps/api/alembic/env.py::target_metadata",
    ),
)


#: Явная фиксация границ (дублирует docstring для проверок/отчётов).
REMOVAL_BOUNDARIES: tuple[str, ...] = (
    "Данные НЕ переносим, НЕ бэкапируем, НЕ сверяем (§8 / §13.1).",
    "Плагиновые таблицы plugin_* НЕ трогаем (ревизия 2026110801).",
    "tenant-БД компаний tenant_<company> и схемы module_<plugin_id> НЕ трогаем.",
    "Существующие таблицы, не related к module_* (companies, projects, "
    "cabinet_instances, dynamic_app_*), НЕ трогаем.",
    "Откат = выключить plugin_enabled_modules[], а НЕ alembic downgrade.",
    "Ревизия fail-closed: требуется PLUGIN_CUTOVER_CONFIRMED=true, иначе "
    "RuntimeError без единого DROP (защита CI Gate / migrate.sh).",
)


def checklist_by_group(group: str) -> tuple[RemovalChecklistItem, ...]:
    """Вернуть пункты чек-листа одной группы (для отчётов родителю)."""

    return tuple(item for item in POST_UPGRADE_REMOVAL_CHECKLIST if item.group == group)


__all__ = [
    "POST_UPGRADE_REMOVAL_CHECKLIST",
    "REMOVAL_BOUNDARIES",
    "RemovalChecklistItem",
    "checklist_by_group",
]
