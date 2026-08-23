"""Unit tests — ingress rate limit (C-CACHE call site)."""

from __future__ import annotations

import pytest

from prodavan.api.v1.projects import _enforce_ingress_rate_limit
from prodavan.domain.errors import AppError


@pytest.mark.asyncio
async def test_ingress_rate_limit_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.config.settings.settings.ingress_rate_limit_per_minute",
        0,
    )
    calls: list[tuple] = []

    async def _rl(*args, **kwargs):
        calls.append((args, kwargs))
        return False

    monkeypatch.setattr("prodavan.core.infra.cache.rate_limit_allow", _rl)
    await _enforce_ingress_rate_limit("proj-1", channel="webhook")
    assert calls == []


@pytest.mark.asyncio
async def test_ingress_rate_limit_raises_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.config.settings.settings.ingress_rate_limit_per_minute",
        5,
    )

    async def _deny(*_a, **_k):
        return False

    monkeypatch.setattr("prodavan.core.infra.cache.rate_limit_allow", _deny)
    with pytest.raises(AppError) as ei:
        await _enforce_ingress_rate_limit("proj-1", channel="telegram")
    assert ei.value.status == 429
    assert ei.value.code == "RATE_LIMITED"
