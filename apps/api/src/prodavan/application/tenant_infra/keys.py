"""Tenant Infra — cache key rewrite and platform prefix deny."""

from __future__ import annotations

import re

from prodavan.domain.errors import AppError

_USER_KEY_RE = re.compile(r"^[A-Za-z0-9_.:/=+\-]{1,200}$")
_PLATFORM_PREFIXES = (
    "prodavan:",
    "celery",
    "tenant:",  # must come from rewrite only
)


def canonicalize_user_key(user_key: str) -> str:
    key = (user_key or "").strip()
    if not key or ".." in key or "\x00" in key or key.startswith("/"):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="invalid cache key",
        )
    if not _USER_KEY_RE.match(key):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="invalid cache key charset",
        )
    lowered = key.lower()
    for prefix in _PLATFORM_PREFIXES:
        if lowered.startswith(prefix) or key.startswith(prefix):
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="cache key collides with platform prefix",
            )
    return key


def rewrite_cache_key(*, company_id: str, project_id: str, user_key: str) -> str:
    safe = canonicalize_user_key(user_key)
    return f"tenant:{company_id}:proj:{project_id}:{safe}"
