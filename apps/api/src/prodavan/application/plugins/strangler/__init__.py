"""F10: strangler routing и historical compatibility inventory.

Актуальные runtime contracts не изменяются:

* ``flags`` — ``FeatureFlagStorePort``, ``InMemoryFeatureFlagStore`` и fail-closed
  ``EnabledModulesPolicy`` для чтения ``plugin_enabled_modules[]``;
* ``routing`` — ``StranglerRouter``: ровно один handler на запрос, ``NotEnabled``
  → legacy, ошибка plugin-пути → ``PluginRouteError`` без молчаливого отката;
* ``cutover`` — compatibility state machine (``CutoverPlan``), а не разрешение на
  schema cleanup, deploy или включение plugin platform;
* ``contexts`` — исторические data-only records по bounded contexts. Их legacy
  field names и slugs сохранены для поведенческой совместимости и не являются
  исполняемым cutover планом;
* ``removal`` — неисполняемый ``CODE_FIRST_REMOVAL_INVENTORY`` для отдельного
  будущего S4 review, а не runbook.

``2026110802`` — dormant/not active historical inventory. Перенос, регистрация и
активация запрещены; никакого runtime cutover или startup/deploy удаления нет.
Migration-only helpers (product upsert/seeds, ``mod_management_meta``,
``seed_rows``, ``management_module_merge`` и bootstrap constants) сохраняются для
fresh/historical upgrades и не представляются deletion entrypoints.

Любая будущая destructive revision допускается только после code-first S4 и
отдельного S5 ADR/review/acceptance, создаётся заново от тогдашней current head,
имеет schema-only downgrade и не использует env guard. Текущий STOP-SHIP эти
compatibility exports не снимают.

Запись флага, аудит и ``EnabledModulesReceipt`` принадлежат
``application/plugins/admin``. Реальные адаптеры хранилища и любые новые ревизии
требуют отдельных решений и evidence.

Интеграция routing остаётся прежней:

.. code-block:: python

    from prodavan.application.plugins.strangler import (
        EnabledModulesPolicy,
        StranglerRouter,
        InMemoryFeatureFlagStore,     # только для тестов; в проде — адаптер к БД
    )

    policy = EnabledModulesPolicy(InMemoryFeatureFlagStore())
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
