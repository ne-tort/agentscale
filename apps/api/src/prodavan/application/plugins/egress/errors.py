"""Ошибки egress-контура плагинов и агентов (v4, §6, fail-closed).

Любая неопределённость — отсутствующий grant, отозванный grant, битая политика,
небезопасный хост, недоступный аудит — трактуется как ОТКАЗ, а не как «разрешить
по умолчанию». Исключения наследуют ``AppError`` (RFC 7807, см. ``domain/errors.py``),
поэтому HTTP-слой может отдать problem+json без дополнительного маппинга.
"""

from __future__ import annotations

from typing import Any

from prodavan.domain.errors import AppError

EGRESS_DENIED_CODE = "EGRESS_DENIED"
EGRESS_POLICY_INVALID_CODE = "EGRESS_POLICY_INVALID"


class EgressDenied(AppError):
    """Отказ в egress-запросе.

    ``reason`` — стабильный машинный код причины (``grant_missing``,
    ``grant_revoked``, ``host_not_allowed``, ``quota_exceeded`` …). Он попадает в
    audit-событие, но никогда не содержит тела запроса, токена или секрета.
    """

    def __init__(
        self,
        detail: str,
        *,
        reason: str = "denied",
        status: int = 403,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.reason = reason
        super().__init__(
            code=EGRESS_DENIED_CODE,
            title="Egress Denied",
            status=status,
            detail=detail,
            extra=extra,
        )


class EgressPolicyError(EgressDenied):
    """Невалидная egress-политика в ``CapabilityGrant``.

    Наследник ``EgressDenied`` осознанно: битая конфигурация — это тоже отказ, а не
    повод собрать «широкую» политику. Так ``pytest.raises(EgressDenied)`` ловит и
    отсутствие grant, и некорректный allowlist.
    """

    def __init__(
        self,
        detail: str,
        *,
        reason: str = "policy_invalid",
        status: int = 422,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail, reason=reason, status=status, extra=extra)
        self.code = EGRESS_POLICY_INVALID_CODE
        self.title = "Invalid Egress Policy"


__all__ = [
    "EGRESS_DENIED_CODE",
    "EGRESS_POLICY_INVALID_CODE",
    "EgressDenied",
    "EgressPolicyError",
]
