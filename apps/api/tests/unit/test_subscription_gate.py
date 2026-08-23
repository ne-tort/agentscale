"""Unit tests — subscription gate transition dedupe."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from prodavan.application.admin.subscription_gate import CompanySubscriptionGate


@pytest.mark.asyncio
async def test_ensure_suspended_skips_when_latest_is_suspended() -> None:
    session = AsyncMock()
    gate = CompanySubscriptionGate(session)
    gate._latest_subscription_transition = AsyncMock(return_value="company.suspended")  # type: ignore[method-assign]
    gate._emit_suspended = AsyncMock()  # type: ignore[method-assign]

    sub = {"subscription_expired": True, "subscription_ends_at": "2020-01-01T00:00:00Z"}
    emitted = await gate.ensure_suspended_platform_event("co_1", sub)

    assert emitted is False
    gate._emit_suspended.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_suspended_emits_after_reactivated_cycle() -> None:
    session = AsyncMock()
    gate = CompanySubscriptionGate(session)
    gate._latest_subscription_transition = AsyncMock(return_value="company.reactivated")  # type: ignore[method-assign]
    gate._emit_suspended = AsyncMock()  # type: ignore[method-assign]

    sub = {"subscription_expired": True, "subscription_ends_at": "2020-01-01T00:00:00Z"}
    emitted = await gate.ensure_suspended_platform_event("co_1", sub)

    assert emitted is True
    gate._emit_suspended.assert_awaited_once()


@pytest.mark.asyncio
async def test_emit_transition_events_suspended_on_expire() -> None:
    session = AsyncMock()
    gate = CompanySubscriptionGate(session)
    gate._emit_suspended = AsyncMock()  # type: ignore[method-assign]
    gate._emit_reactivated = AsyncMock()  # type: ignore[method-assign]

    sub = {"subscription_expired": True}
    await gate.emit_transition_events("co_1", was_expired=False, now_expired=True, subscription=sub)

    gate._emit_suspended.assert_awaited_once()
    gate._emit_reactivated.assert_not_called()


@pytest.mark.asyncio
async def test_emit_transition_events_reactivated_on_renew() -> None:
    session = AsyncMock()
    gate = CompanySubscriptionGate(session)
    gate._emit_suspended = AsyncMock()  # type: ignore[method-assign]
    gate._emit_reactivated = AsyncMock()  # type: ignore[method-assign]

    sub = {"subscription_expired": False, "subscription_lifetime": True}
    await gate.emit_transition_events("co_1", was_expired=True, now_expired=False, subscription=sub)

    gate._emit_reactivated.assert_awaited_once()
    gate._emit_suspended.assert_not_called()
