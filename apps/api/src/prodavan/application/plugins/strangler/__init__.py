"""F10: strangler-переход с declarative-app на плагины (дизайн v4, §8).

Зона владеет **механизмом strangler'а и картой переноса контекстов**:

* ``flags``     — ``FeatureFlagStorePort`` (Protocol: ``enabled``/``set``/``list``),
  ``InMemoryFeatureFlagStore`` и fail-closed ``EnabledModulesPolicy`` (сторона
  чтения ``plugin_enabled_modules[]``);
* ``routing``   — ``StranglerRouter``: ровно ОДИН handler на запрос, ``NotEnabled``
  → legacy, ошибка plugin-пути → типизированная ``PluginRouteError`` без молчаливого
  отката;
* ``contexts``  — карта переноса ``mod_equipment`` по bounded context'ам
  (offers → prices → budget → documents → tenders + ``mod_management`` последним),
  **данными**, без правки существующего кода; ``module_*`` таблицы дропаются без
  переноса содержимого;
* ``cutover``   — ``CutoverPlan``: последовательность §8, ``current_step``,
  ``advance()`` без пропуска шагов, ``final_state()`` со списком из 5 артефактов;
* ``removal``   — ЯВНЫЙ чек-лист для родителя: какие ORM-классы/импорты/роутеры/
  страницы/сервисы удалить ПОСЛЕ применения ревизии ``2026110802``
  (``alembic upgrade head``). До cutover'а код остаётся: другие подсистемы ещё
  читают ``module_*``. Данные НЕ переносим.

ЧЕГО ЗДЕСЬ НЕТ И НЕ БУДЕТ (§8, §13.1, план миграции §4): dual-read/dual-write,
parity/метрики парности, snapshot/бэкапы, ``migrate_data``, «N недель наблюдения»,
сравнение выходов старого и нового путей. Данные dev эфемерны; «всё под нож» —
про КОНЕЧНОЕ СОСТОЯНИЕ, а strangler — про СПОСОБ.

ГРАНИЦЫ (не дублируем чужое):
* запись флага, аудит и ``EnabledModulesReceipt`` — ``application/plugins/admin``
  (``PluginAdminService.set_enabled_modules``, ``GrantStorePort``);
* подключение ``EnabledModulesPolicy`` к admin-роутеру, реальные адаптеры хранилища
  и ревизии Alembic — **родитель (F11)**. Здесь только контракты, политика чтения,
  роутер, карта и план.

Интеграция (выполняет родитель):

.. code-block:: python

    from prodavan.application.plugins.strangler import (
        EnabledModulesPolicy,
        StranglerRouter,
        InMemoryFeatureFlagStore,     # только для тестов; в проде — адаптер к БД
    )

    policy = EnabledModulesPolicy(InMemoryFeatureFlagStore())  # F11: адаптер к GrantStore
    router = StranglerRouter(policy)
    result = await router.route(
        company_id, "mod_equipment",
        legacy_handler=legacy_equipment_service,
        plugin_handler=plugin_equipment_client,
    )
"""

from __future__ import annotations

from .contexts import (
    CONTEXT_ORDER,
    DATA_TRANSFER_POLICY,
    MIGRATION_CONTEXTS,
    MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER,
    SHARED_LEGACY_ENTRYPOINTS,
    ContextMigration,
    RemovalStep,
    context_by_id,
)
from .cutover import (
    ARTIFACT_EQUIPMENT_PAGE,
    ARTIFACT_GENERIC_RUNTIME,
    ARTIFACT_MODULE_INSTANCE_DATA_ROWS,
    ARTIFACT_MODULE_META_DOCUMENTS,
    ARTIFACT_MODULE_RUNTIME_TSX,
    CUTOVER_STEPS,
    FINAL_STATE_ARTIFACTS,
    CutoverOrderError,
    CutoverPlan,
    CutoverPlanComplete,
    CutoverStep,
    CutoverStepKind,
    FinalState,
)
from .flags import (
    FORBIDDEN_PARITY_NAMES,
    EnabledModulesPolicy,
    FeatureFlagStorePort,
    InMemoryFeatureFlagStore,
)
from .routing import (
    Handler,
    NotEnabled,
    PluginRouteError,
    StranglerRouter,
)

__all__ = [
    # flags
    "FeatureFlagStorePort",
    "InMemoryFeatureFlagStore",
    "EnabledModulesPolicy",
    "FORBIDDEN_PARITY_NAMES",
    # routing
    "Handler",
    "NotEnabled",
    "PluginRouteError",
    "StranglerRouter",
    # contexts
    "MIGRATION_CONTEXTS",
    "CONTEXT_ORDER",
    "MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER",
    "DATA_TRANSFER_POLICY",
    "SHARED_LEGACY_ENTRYPOINTS",
    "ContextMigration",
    "RemovalStep",
    "context_by_id",
    # cutover
    "CutoverPlan",
    "CutoverStep",
    "CutoverStepKind",
    "CutoverOrderError",
    "CutoverPlanComplete",
    "FinalState",
    "CUTOVER_STEPS",
    "FINAL_STATE_ARTIFACTS",
    "ARTIFACT_MODULE_META_DOCUMENTS",
    "ARTIFACT_MODULE_RUNTIME_TSX",
    "ARTIFACT_MODULE_INSTANCE_DATA_ROWS",
    "ARTIFACT_GENERIC_RUNTIME",
    "ARTIFACT_EQUIPMENT_PAGE",
]
