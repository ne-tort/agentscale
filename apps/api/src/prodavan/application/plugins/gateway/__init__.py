"""Plugin Gateway (F6, дизайн v4 §7.1/§7.2/§7.4/§7.5).

Всегда-running единая точка входа для::

    /webhook/{plugin}/{company}/*      — подписанные входящие вебхуки
    /pluginpage/{plugin}/{company}/*   — frontend (static) + /api/* (backend)

и проксирования в спящий backend плагина (wake → hold-until-Ready → forward).

Публичный контракт пакета — ``GatewayAuth`` (решение), ``GatewayProxy``
(исполнение), ``ports`` (Protocol), ``headers`` (allowlist доверенных
заголовков), ``errors`` (типизированные отказы). Композиция портов — задача
интегратора (F11); родитель подключает ``api/v1/plugin_gateway.py::router``.
"""

from __future__ import annotations

from prodavan.application.plugins.gateway.auth import (
    DEFAULT_LIMITS,
    GatewayAuth,
    GatewayLimits,
    PageAuthResult,
    Sha256HmacVerifier,
    WebhookAuthResult,
)
from prodavan.application.plugins.gateway.errors import (
    BackendNotReadyError,
    BackendTimeoutError,
    GatewayError,
    GatewayErrorCode,
)
from prodavan.application.plugins.gateway.headers import (
    HEADER_CAPABILITY_TOKEN,
    HEADER_COMPANY_ID,
    HEADER_PLUGIN_ID,
    HEADER_PROJECT_ID,
    TRUSTED_HEADERS,
    build_forward_headers,
    build_trusted_headers,
    sanitize_incoming,
)
from prodavan.application.plugins.gateway.proxy import (
    IDEMPOTENT_METHODS,
    GatewayProxy,
    sanitize_response_headers,
)

__all__ = [
    "DEFAULT_LIMITS",
    "HEADER_CAPABILITY_TOKEN",
    "HEADER_COMPANY_ID",
    "HEADER_PLUGIN_ID",
    "HEADER_PROJECT_ID",
    "IDEMPOTENT_METHODS",
    "TRUSTED_HEADERS",
    "BackendNotReadyError",
    "BackendTimeoutError",
    "GatewayAuth",
    "GatewayError",
    "GatewayErrorCode",
    "GatewayLimits",
    "GatewayProxy",
    "PageAuthResult",
    "Sha256HmacVerifier",
    "WebhookAuthResult",
    "build_forward_headers",
    "build_trusted_headers",
    "sanitize_incoming",
    "sanitize_response_headers",
]
