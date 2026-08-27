"""Map Keycloak / network failures → AppError for Auth Service."""

from __future__ import annotations

import json
from typing import Any

from prodavan.domain.errors import AppError


def raise_for_keycloak_token_response(status_code: int, body: str) -> None:
    """Raise AppError from a Keycloak token endpoint error response."""
    payload = _try_json(body)
    oauth_error = str(payload.get("error") or "") if payload else ""
    oauth_desc = str(payload.get("error_description") or payload.get("errorMessage") or "")
    detail = oauth_desc or oauth_error or (body.strip()[:400] if body else None)

    if status_code in (400, 401):
        code = "INVALID_CREDENTIALS"
        if oauth_error in ("invalid_grant", "unauthorized_client", "invalid_client"):
            code = "INVALID_CREDENTIALS"
        raise AppError(
            code=code,
            title="Authentication failed",
            status=401,
            detail=detail or "invalid_grant",
        )
    if status_code == 429:
        raise AppError(
            code="RATE_LIMITED",
            title="Too many requests",
            status=429,
            detail=detail,
        )
    if status_code >= 500:
        raise AppError(
            code="IDENTITY_PROVIDER",
            title="Identity provider error",
            status=502,
            detail=detail or f"Keycloak returned {status_code}",
        )
    raise AppError(
        code="IDENTITY_PROVIDER",
        title="Identity provider error",
        status=502,
        detail=detail or f"Keycloak returned {status_code}",
    )


def auth_misconfigured(detail: str) -> AppError:
    return AppError(
        code="AUTH_MISCONFIGURED",
        title="Auth misconfigured",
        status=503,
        detail=detail,
    )


def identity_unreachable(detail: str, *, status: int = 502) -> AppError:
    return AppError(
        code="IDENTITY_PROVIDER",
        title="Identity provider unreachable",
        status=status,
        detail=detail,
    )


def _try_json(body: str) -> dict[str, Any] | None:
    try:
        decoded = json.loads(body)
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None
