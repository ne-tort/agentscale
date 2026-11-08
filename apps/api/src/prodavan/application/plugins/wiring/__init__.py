"""F11: единая точка сборки plugin-платформы (wiring / composition root).

Этот пакет — «клей» между готовыми зонами
(``identity`` / ``capabilities`` / ``artifacts`` / ``tenantdb`` / ``objects`` /
``egress`` / ``activation`` / ``events`` / ``gateway`` / ``admin`` /
``transport``) и ``main.py`` родителя. Ни один общий файл здесь НЕ редактируется:
родитель сам вызывает ``build_plugin_platform`` + ``attach_plugin_platform``.

Минимальная интеграция (делает РОДИТЕЛЬ в ``main.py``)::

    from prodavan.application.plugins.wiring import (
        attach_plugin_platform,
        build_plugin_platform,
    )
    from prodavan.config.settings import settings

    platform = build_plugin_platform(
        settings,
        grant_store=...,        # GrantStorePort поверх CapabilityGrantRow.status (A10)
        cosign=...,             # CosignPort (keyless verify)
        admin_ports=...,        # PluginAdminPorts (задача 12)
        # остальные порты зон — по мере готовности адаптеров
    )
    if platform is not None:   # enabled=False -> сервисы НЕ создаются
        attach_plugin_platform(app, platform)

Интеграционная цепочка каждого запроса (v4 §4.1/§4.2, критерии №12, №17)::

    verify_plugin_token(token, keyring=platform.keyring)   # 1. verify
      -> platform.revocation.check(claims)                  # 2. check (grant_id в БД)
      -> assert_plugin_surface(claims, request.url.path)    # 3. surface (/capabilities/v1/*)
      -> platform.capability_service.invoke(...)            #    ядро + адаптеры + аудит

Fail-closed на всех уровнях: выключено -> ``None``/503; нет секрета -> ошибка
сборки; нет БД grant'ов -> отказ; нет cosign -> отказ; незаполненный адаптер ->
``UnavailableAdapter`` -> ``AdapterMissingError`` -> system deny, а не «200 с
None». Никаких dev-фолбэков на ``AUTH_TEST_SECRET`` / pod-bridge (другой контур).

Русские комментарии, идентификаторы английские. Тесты написаны, но НЕ
запускались (verification freeze, план миграции §0.2 / §9 дизайна).
"""

from __future__ import annotations

from prodavan.application.plugins.wiring.adapters import (
    STUB_ADAPTER_NAMES,
    STUB_ADAPTER_WORK,
    WIRED_CAPABILITIES,
    UnavailableAdapter,
    adapter_report,
    build_adapter_registry,
    stub_names,
)
from prodavan.application.plugins.wiring.config import (
    ALLOWED_EGRESS_MODES,
    MAX_CLAIMS_TTL_SECONDS,
    MIN_CLAIMS_TTL_SECONDS,
    PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV,
    PLUGIN_SETTING_FIELDS,
    PluginPlatformConfig,
    PluginPlatformConfigError,
    read_previous_secrets,
)
from prodavan.application.plugins.wiring.install import (
    PLUGIN_SURFACE_GUARD_STATE_KEY,
    PluginCompositionError,
    install_plugin_platform,
    install_plugin_platform_for_tests,
)
from prodavan.application.plugins.wiring.keyring import (
    ALGORITHM,
    active_kid_for,
    build_keyring,
    jwks_for,
    keyring_report,
    verification_kids,
)
from prodavan.application.plugins.wiring.platform import (
    ADAPTER_OVERRIDES,
    OVERRIDE_NAMES,
    PORT_OVERRIDES,
    SERVICE_OVERRIDES,
    PluginPlatform,
    build_plugin_platform,
)
from prodavan.application.plugins.wiring.revocation import (
    DEFAULT_CACHE_TTL_SECONDS,
    GRANT_STATUS_ACTIVE,
    GRANT_STATUS_REVOKED,
    DurableGrantRevocation,
    GrantRevocationUnavailable,
    GrantSnapshot,
    GrantStorePort,
)
from prodavan.application.plugins.wiring.state import (
    PLATFORM_UNAVAILABLE_CODE,
    REQUIRED_STATE_KEYS,
    STATE_KEYS,
    AppStateLike,
    PluginPlatformNotAttached,
    RequestLike,
    attach_plugin_platform,
    detach_plugin_platform,
    get_capability_service_factory,
    get_plugin_platform,
)

__all__ = [
    # config
    "ALLOWED_EGRESS_MODES",
    "MAX_CLAIMS_TTL_SECONDS",
    "MIN_CLAIMS_TTL_SECONDS",
    "PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV",
    "PLUGIN_SETTING_FIELDS",
    "PluginPlatformConfig",
    "PluginPlatformConfigError",
    "read_previous_secrets",
    # install (F11: ОДИН вызов для main.py)
    "PLUGIN_SURFACE_GUARD_STATE_KEY",
    "PluginCompositionError",
    "install_plugin_platform",
    "install_plugin_platform_for_tests",
    # keyring
    "ALGORITHM",
    "active_kid_for",
    "build_keyring",
    "jwks_for",
    "keyring_report",
    "verification_kids",
    # revocation
    "DEFAULT_CACHE_TTL_SECONDS",
    "GRANT_STATUS_ACTIVE",
    "GRANT_STATUS_REVOKED",
    "DurableGrantRevocation",
    "GrantRevocationUnavailable",
    "GrantSnapshot",
    "GrantStorePort",
    # adapters
    "STUB_ADAPTER_NAMES",
    "STUB_ADAPTER_WORK",
    "WIRED_CAPABILITIES",
    "UnavailableAdapter",
    "adapter_report",
    "build_adapter_registry",
    "stub_names",
    # platform
    "ADAPTER_OVERRIDES",
    "OVERRIDE_NAMES",
    "PORT_OVERRIDES",
    "SERVICE_OVERRIDES",
    "PluginPlatform",
    "build_plugin_platform",
    # state
    "PLATFORM_UNAVAILABLE_CODE",
    "REQUIRED_STATE_KEYS",
    "STATE_KEYS",
    "AppStateLike",
    "PluginPlatformNotAttached",
    "RequestLike",
    "attach_plugin_platform",
    "detach_plugin_platform",
    "get_capability_service_factory",
    "get_plugin_platform",
]
