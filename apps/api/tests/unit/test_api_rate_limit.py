"""Unit tests — shared enforce_rate_limit helper."""

from __future__ import annotations

import pytest

from prodavan.api.rate_limit import enforce_rate_limit
from prodavan.domain.errors import AppError


@pytest.mark.asyncio
async def test_enforce_rate_limit_disabled() -> None:
    await enforce_rate_limit("k", limit=0)


@pytest.mark.asyncio
async def test_enforce_rate_limit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _deny(*_a, **_k):
        return False

    monkeypatch.setattr("prodavan.core.infra.cache.rate_limit_allow", _deny)
    with pytest.raises(AppError) as ei:
        await enforce_rate_limit("k", limit=1, detail="nope")
    assert ei.value.status == 429
    assert ei.value.detail == "nope"
