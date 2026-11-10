"""Типизированные ошибки Plugin Gateway (F6, §7.2/§7.5 дизайна v4).

Fail-closed: каждый отказ — явная ошибка с стабильным кодом, а не «тихое»
продолжение с ослабленным trust-контуром. ``GatewayError`` наследует
``AppError``, поэтому уже зарегистрированный в ``main.create_app`` обработчик
отдаёт RFC 7807 ``problem+json`` без дополнительной обвязки в роутере.
"""

from __future__ import annotations

from enum import StrEnum

from prodavan.domain.errors import AppError


class GatewayErrorCode(StrEnum):
    """Стабильные коды отказов шлюза (логи, CI, аудит, клиентские сообщения)."""

    # Конфигурация / композиция
    RUNTIME_NOT_INSTALLED = "PLUGIN_GATEWAY_RUNTIME_NOT_INSTALLED"
    PORT_UNAVAILABLE = "PLUGIN_GATEWAY_PORT_UNAVAILABLE"
    INVALID_PATH = "PLUGIN_GATEWAY_INVALID_PATH"
    METHOD_NOT_ALLOWED = "PLUGIN_GATEWAY_METHOD_NOT_ALLOWED"
    STATIC_ORIGIN_NOT_CONFIGURED = "PLUGIN_GATEWAY_STATIC_ORIGIN_NOT_CONFIGURED"

    # Идентичность и доступ
    SESSION_REQUIRED = "PLUGIN_GATEWAY_SESSION_REQUIRED"
    EMPLOYEE_NOT_IN_COMPANY = "PLUGIN_GATEWAY_EMPLOYEE_NOT_IN_COMPANY"
    GRANT_INACTIVE = "PLUGIN_GATEWAY_GRANT_INACTIVE"
    OPERATION_NOT_ALLOWED = "PLUGIN_GATEWAY_OPERATION_NOT_ALLOWED"

    # Входящие вебхуки
    WEBHOOK_HEADERS_MISSING = "PLUGIN_GATEWAY_WEBHOOK_HEADERS_MISSING"
    INVALID_SIGNATURE = "PLUGIN_GATEWAY_INVALID_SIGNATURE"
    SECRET_UNAVAILABLE = "PLUGIN_GATEWAY_SECRET_UNAVAILABLE"
    REPLAY_DETECTED = "PLUGIN_GATEWAY_REPLAY_DETECTED"
    RATE_LIMITED = "PLUGIN_GATEWAY_RATE_LIMITED"
    INVALID_TIMESTAMP = "PLUGIN_GATEWAY_INVALID_TIMESTAMP"
    INVALID_SIGNATURE_VERSION = "PLUGIN_GATEWAY_INVALID_SIGNATURE_VERSION"
    WEBHOOK_ROUTE_NOT_ALLOWED = "PLUGIN_GATEWAY_WEBHOOK_ROUTE_NOT_ALLOWED"
    DELIVERY_IN_PROGRESS = "PLUGIN_GATEWAY_DELIVERY_IN_PROGRESS"
    DELIVERY_COMMIT_FAILED = "PLUGIN_GATEWAY_DELIVERY_COMMIT_FAILED"
    PAYLOAD_TOO_LARGE = "PLUGIN_GATEWAY_PAYLOAD_TOO_LARGE"
    INVALID_CONTENT_LENGTH = "PLUGIN_GATEWAY_INVALID_CONTENT_LENGTH"
    UPSTREAM_RESPONSE_TOO_LARGE = "PLUGIN_GATEWAY_UPSTREAM_RESPONSE_TOO_LARGE"

    # Backend / activator
    WAKE_LEASE_MISSING = "PLUGIN_GATEWAY_WAKE_LEASE_MISSING"
    BACKEND_TIMEOUT = "PLUGIN_GATEWAY_BACKEND_TIMEOUT"
    UPSTREAM_ERROR = "PLUGIN_GATEWAY_UPSTREAM_ERROR"
    AUDIT_APPEND_FAILED = "PLUGIN_GATEWAY_AUDIT_APPEND_FAILED"


#: HTTP-статус по умолчанию для каждого кода. Явный, без «угадывания».
_DEFAULT_STATUS: dict[GatewayErrorCode, int] = {
    GatewayErrorCode.RUNTIME_NOT_INSTALLED: 503,
    GatewayErrorCode.PORT_UNAVAILABLE: 503,
    GatewayErrorCode.INVALID_PATH: 400,
    GatewayErrorCode.METHOD_NOT_ALLOWED: 405,
    GatewayErrorCode.STATIC_ORIGIN_NOT_CONFIGURED: 503,
    GatewayErrorCode.SESSION_REQUIRED: 401,
    GatewayErrorCode.EMPLOYEE_NOT_IN_COMPANY: 403,
    GatewayErrorCode.GRANT_INACTIVE: 403,
    GatewayErrorCode.OPERATION_NOT_ALLOWED: 403,
    GatewayErrorCode.WEBHOOK_HEADERS_MISSING: 401,
    GatewayErrorCode.INVALID_SIGNATURE: 401,
    GatewayErrorCode.SECRET_UNAVAILABLE: 503,
    GatewayErrorCode.REPLAY_DETECTED: 401,
    GatewayErrorCode.RATE_LIMITED: 429,
    GatewayErrorCode.INVALID_TIMESTAMP: 401,
    GatewayErrorCode.INVALID_SIGNATURE_VERSION: 401,
    GatewayErrorCode.WEBHOOK_ROUTE_NOT_ALLOWED: 403,
    GatewayErrorCode.DELIVERY_IN_PROGRESS: 503,
    GatewayErrorCode.DELIVERY_COMMIT_FAILED: 503,
    GatewayErrorCode.PAYLOAD_TOO_LARGE: 413,
    GatewayErrorCode.INVALID_CONTENT_LENGTH: 400,
    GatewayErrorCode.UPSTREAM_RESPONSE_TOO_LARGE: 502,
    GatewayErrorCode.WAKE_LEASE_MISSING: 503,
    GatewayErrorCode.BACKEND_TIMEOUT: 504,
    GatewayErrorCode.UPSTREAM_ERROR: 502,
    GatewayErrorCode.AUDIT_APPEND_FAILED: 503,
}

_TITLES: dict[GatewayErrorCode, str] = {
    GatewayErrorCode.RUNTIME_NOT_INSTALLED: "Plugin Gateway not configured",
    GatewayErrorCode.PORT_UNAVAILABLE: "Plugin Gateway port unavailable",
    GatewayErrorCode.INVALID_PATH: "Invalid plugin path",
    GatewayErrorCode.METHOD_NOT_ALLOWED: "Method not allowed",
    GatewayErrorCode.STATIC_ORIGIN_NOT_CONFIGURED: "Static origin not configured",
    GatewayErrorCode.SESSION_REQUIRED: "Session required",
    GatewayErrorCode.EMPLOYEE_NOT_IN_COMPANY: "Employee is not a company member",
    GatewayErrorCode.GRANT_INACTIVE: "Plugin grant is not active",
    GatewayErrorCode.OPERATION_NOT_ALLOWED: "Plugin operation is not allowed",
    GatewayErrorCode.WEBHOOK_HEADERS_MISSING: "Webhook headers missing",
    GatewayErrorCode.INVALID_SIGNATURE: "Invalid webhook signature",
    GatewayErrorCode.SECRET_UNAVAILABLE: "Webhook secret unavailable",
    GatewayErrorCode.REPLAY_DETECTED: "Webhook delivery replay detected",
    GatewayErrorCode.RATE_LIMITED: "Rate limit exceeded",
    GatewayErrorCode.INVALID_TIMESTAMP: "Webhook timestamp is invalid or stale",
    GatewayErrorCode.INVALID_SIGNATURE_VERSION: "Unsupported webhook signature version",
    GatewayErrorCode.WEBHOOK_ROUTE_NOT_ALLOWED: "Webhook route is not declared",
    GatewayErrorCode.DELIVERY_IN_PROGRESS: "Webhook delivery is already in progress",
    GatewayErrorCode.DELIVERY_COMMIT_FAILED: "Webhook delivery could not be committed",
    GatewayErrorCode.PAYLOAD_TOO_LARGE: "Request payload too large",
    GatewayErrorCode.INVALID_CONTENT_LENGTH: "Invalid Content-Length",
    GatewayErrorCode.UPSTREAM_RESPONSE_TOO_LARGE: "Plugin response too large",
    GatewayErrorCode.WAKE_LEASE_MISSING: "Wake-up lease missing",
    GatewayErrorCode.BACKEND_TIMEOUT: "Plugin backend timeout",
    GatewayErrorCode.UPSTREAM_ERROR: "Plugin backend error",
    GatewayErrorCode.AUDIT_APPEND_FAILED: "Audit append failed",
}


class GatewayError(AppError):
    """Отказ шлюза. Несёт стабильный код и, при необходимости, ``Retry-After``."""

    def __init__(
        self,
        code: GatewayErrorCode,
        *,
        detail: str,
        status: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        if not isinstance(code, GatewayErrorCode):
            raise TypeError("code must be a GatewayErrorCode")
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError("GatewayError requires a non-empty detail")
        resolved_status = status if status is not None else _DEFAULT_STATUS[code]
        super().__init__(
            code=code.value,
            title=_TITLES[code],
            status=resolved_status,
            detail=detail,
        )
        self.gateway_code = code
        self.retry_after_seconds = retry_after_seconds


class BackendResponseTooLargeError(GatewayError):
    """Non-streaming upstream превысил configured cap; тело уже закрыто."""

    def __init__(self, *, limit_bytes: int) -> None:
        super().__init__(
            GatewayErrorCode.UPSTREAM_RESPONSE_TOO_LARGE,
            detail=f"plugin response exceeds the configured {limit_bytes} byte limit",
        )
        self.limit_bytes = limit_bytes


class BackendNotReadyError(Exception):
    """Backend ещё не Ready после wake-up. Шлюз отвечает ``202 + Retry-After``.

    Адаптер ``BackendHttpPort`` поднимает её вместо «молчаливого» пустого
    ответа; это сигнал «принято, повторите позже», а не успешная обработка.
    """

    def __init__(self, reason: str = "backend not ready") -> None:
        super().__init__(reason)
        self.reason = reason


class BackendTimeoutError(Exception):
    """Серверный таймаут ожидания backend (не таймаут из тела запроса)."""

    def __init__(self, reason: str = "backend timeout") -> None:
        super().__init__(reason)
        self.reason = reason


__all__ = [
    "BackendNotReadyError",
    "BackendResponseTooLargeError",
    "BackendTimeoutError",
    "GatewayError",
    "GatewayErrorCode",
]
