"""Shared HTTP rate-limit helpers (C-CACHE)."""

from __future__ import annotations

from prodavan.domain.errors import AppError


async def enforce_rate_limit(
    key: str,
    *,
    limit: int,
    window_sec: int = 60,
    detail: str = "rate limit exceeded",
) -> None:
    """Raise 429 when Redis fixed-window counter exceeds ``limit``. ``limit < 1`` disables."""
    if limit < 1:
        return
    from prodavan.core.infra.cache import rate_limit_allow

    allowed = await rate_limit_allow(key, limit=limit, window_sec=window_sec)
    if not allowed:
        raise AppError(
            code="RATE_LIMITED",
            title="Too Many Requests",
            status=429,
            detail=detail,
        )
