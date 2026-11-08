"""Транспортный слой plugin-surface ``/capabilities/v1/*`` (задача 04, F2).

Отдельный route-group в Plugin Gateway (v4 §4.1, §7.2): **НЕ** расширение
``:8001`` pod-gateway. Агентский trust (``typ=pod_bridge``, :8001,
``core/middleware.py``, ``main_pod.py``) остаётся БЕЗ ИЗМЕНЕНИЙ; критерий №17
закрывает плагину ``/agent/*`` и ``/internal/pods/*``.

Состав:
  * ``deps``    — ``get_plugin_claims``: Bearer -> verify (задача 02) ->
                   revocation (задача 02) -> ``assert_plugin_surface`` (задача 02);
  * ``bodies``  — типизированные Pydantic-тела, ``extra='forbid'``, запрет
                   ``sql``/``command``/``args``/``raw``;
  * ``bridge``  — преобразование в датаклассы ``capabilities/contracts.py`` и
                   вызов ``PluginCapabilityService.invoke`` (задача 03);
  * ``errors``  — маппинг доменных решений/исключений в HTTP problem+json.

Интеграция делает РОДИТЕЛЬ (F11), без правки общих точек входа этим модулем::

    app.state.plugin_keyring = ...                 # identity.keys.KeyRing
    app.state.plugin_revocation = ...             # identity.revocation.GrantRevocation
    app.state.plugin_capability_service = ...     # capabilities.PluginCapabilityService
    app.include_router(plugin_capabilities.router)  # БЕЗ /api/v1 prefix!

Префикс критичен: ``assert_plugin_surface`` задачи 02 разрешает только путь
``^/capabilities/v1/...``; любой внешний префикс даст 403 на всех эндпоинтах.

Fail-closed: без привязанного keyring/revocation/сервиса роутер отдаёт 503,
а не «пропускает» запрос.
"""

from __future__ import annotations

from prodavan.application.plugins.transport.deps import (
    PluginClaimsDep,
    PluginIdentityConfig,
    get_plugin_claims,
    resolve_identity_config,
)
from prodavan.application.plugins.transport.errors import (
    GENERIC_DISPATCH_FIELDS,
    PluginProblem,
    PluginTransportError,
    plugin_problem_response,
    problem_from_code,
    problem_from_decision,
    problem_from_exception,
)

__all__ = [
    "GENERIC_DISPATCH_FIELDS",
    "PluginClaimsDep",
    "PluginIdentityConfig",
    "PluginProblem",
    "PluginTransportError",
    "get_plugin_claims",
    "plugin_problem_response",
    "problem_from_code",
    "problem_from_decision",
    "problem_from_exception",
    "resolve_identity_config",
]
