"""Пакет контроллера синхронизации controller-managed данных плагинов (v4 §6.3).

Зачем: три объекта в кластере правятся контроллером ВНЕ Git при grant/revoke,
Argo ``selfHeal`` с ``ignoreDifferences /data`` их не сотрёт, но БЕЗ контроллера
выданный grant не вступит в силу (пустой ``pg_hba``, неизвестный PgBouncer'у
пароль, ``403`` от Envoy). Подробности — в докстринге ``ports.py``.

Публичный контракт:
  * ``ports``      — Protocol-порты (ConfigMap / Secret / reload / GrantStore /
                     GrantEvent / Clock / Audit) и типы ``ActiveGrant``,
                     ``GrantChangeEvent``, ``SyncReceipt``;
  * ``renderer``   — чистые детерминированные функции рендеринга трёх артефактов;
  * ``controller`` — ``PluginInfraController.sync()``: fail-closed порядок
                     tenant-PG → PgBouncer → Envoy → audit, идемпотентность;
  * ``watcher``    — ``GrantChangeWatcher`` с дебаунсом массовых grant'ов;
  * ``errors``     — ``SyncError`` / ``RenderError`` со ссылкой на шаг.

Пакет НЕ содержит адаптеров k8s/PgBouncer/PostgreSQL/Envoy и НЕ делает сетевых
вызовов сам: реализации портов подключаются родителем в F11. Отсутствие любого
адаптера = явная ошибка, а не «тихий пропуск» (правило §0 плана миграции).
"""

from __future__ import annotations

from prodavan.infrastructure.plugins.controllers.controller import (
    AUDIT_EVENT_SYNC,
    AUDIT_EVENT_SYNC_FAILED,
    ControllerPorts,
    PluginInfraController,
)
from prodavan.infrastructure.plugins.controllers.errors import (
    ALL_STEPS,
    RENDER_FAILED_CODE,
    STEP_AUDIT,
    STEP_ENVOY_PATCH,
    STEP_ENVOY_RELOAD,
    STEP_PGBOUNCER_RELOAD,
    STEP_TENANT_HBA_PATCH,
    STEP_TENANT_PG_RELOAD,
    STEP_USERLIST_PATCH,
    SYNC_FAILED_CODE,
    RenderError,
    SyncError,
)
from prodavan.infrastructure.plugins.controllers.ports import (
    EGRESS_PROXY_CONFIGMAP,
    ENVOY_CONFIG_KEY,
    PGBOUNCER_ADMIN_USER,
    PGBOUNCER_CREDENTIALS_SECRET,
    PGBOUNCER_USERLIST_KEY,
    POD_CIDRS,
    TENANT_HBA_KEY,
    TENANT_POSTGRES_CONFIGMAP,
    ActiveGrant,
    AuditPort,
    ClockPort,
    ConfigMapPort,
    EnvoyReloadPort,
    GrantChangeEvent,
    GrantEventPort,
    GrantStorePort,
    PgBouncerReloadPort,
    PostgresReloadPort,
    SecretPort,
    SyncReceipt,
)
from prodavan.infrastructure.plugins.controllers.renderer import (
    build_envoy_policies,
    render_envoy_config,
    render_envoy_yaml,
    render_pgbouncer_userlist,
    render_pgbouncer_userlist_preserving_admin,
    render_tenant_hba,
)
from prodavan.infrastructure.plugins.controllers.watcher import (
    AUDIT_EVENT_WATCHER,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_MAX_WAIT_SECONDS,
    GrantChangeWatcher,
    WatcherOutcome,
)

__all__ = [
    # ports: имена объектов и константы
    "TENANT_POSTGRES_CONFIGMAP",
    "TENANT_HBA_KEY",
    "PGBOUNCER_CREDENTIALS_SECRET",
    "PGBOUNCER_USERLIST_KEY",
    "EGRESS_PROXY_CONFIGMAP",
    "ENVOY_CONFIG_KEY",
    "POD_CIDRS",
    "PGBOUNCER_ADMIN_USER",
    # ports: типы
    "ActiveGrant",
    "GrantChangeEvent",
    "SyncReceipt",
    # ports: Protocol
    "ConfigMapPort",
    "SecretPort",
    "PostgresReloadPort",
    "PgBouncerReloadPort",
    "EnvoyReloadPort",
    "GrantStorePort",
    "GrantEventPort",
    "ClockPort",
    "AuditPort",
    # renderer
    "render_tenant_hba",
    "render_pgbouncer_userlist",
    "render_pgbouncer_userlist_preserving_admin",
    "render_envoy_config",
    "render_envoy_yaml",
    "build_envoy_policies",
    # controller
    "PluginInfraController",
    "ControllerPorts",
    "AUDIT_EVENT_SYNC",
    "AUDIT_EVENT_SYNC_FAILED",
    # watcher
    "GrantChangeWatcher",
    "WatcherOutcome",
    "AUDIT_EVENT_WATCHER",
    "DEFAULT_DEBOUNCE_SECONDS",
    "DEFAULT_MAX_WAIT_SECONDS",
    # errors
    "SyncError",
    "RenderError",
    "SYNC_FAILED_CODE",
    "RENDER_FAILED_CODE",
    "STEP_TENANT_HBA_PATCH",
    "STEP_TENANT_PG_RELOAD",
    "STEP_USERLIST_PATCH",
    "STEP_PGBOUNCER_RELOAD",
    "STEP_ENVOY_PATCH",
    "STEP_ENVOY_RELOAD",
    "STEP_AUDIT",
    "ALL_STEPS",
]
