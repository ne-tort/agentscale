"""Unit tests — metrics presence store + consumer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.metrics.consumer import handle_auth_event, handle_platform_envelope
from prodavan.application.metrics.presence_store import batch_is_online, clear_presence, set_presence
from prodavan.core.events.envelope import platform_envelope


@pytest.mark.asyncio
async def test_presence_store_noop_without_redis() -> None:
    with patch("prodavan.core.infra.redis_manager.get_redis_manager", return_value=None):
        assert await set_presence("employee", "emp_1") is False
        assert await clear_presence("employee", "emp_1") is False
        assert await batch_is_online("employee", ["emp_1"]) == {"emp_1": False}


@pytest.mark.asyncio
async def test_presence_store_set_and_mget() -> None:
    client = AsyncMock()
    client.set = AsyncMock(return_value=True)
    client.mget = AsyncMock(return_value=["1", None])
    mgr = MagicMock(enabled=True, client=client)
    with patch("prodavan.core.infra.redis_manager.get_redis_manager", return_value=mgr):
        assert await set_presence("employee", "emp_1") is True
        client.set.assert_awaited_once()
        online = await batch_is_online("employee", ["emp_1", "emp_2"])
        assert online == {"emp_1": True, "emp_2": False}


@pytest.mark.asyncio
async def test_handle_auth_event_set_and_clear() -> None:
    resolved = MagicMock(kind="employee", entity_id="emp_1")

    with (
        patch(
            "prodavan.application.metrics.consumer.PresenceResolver.resolve_auth_payload",
            new=AsyncMock(return_value=resolved),
        ),
        patch("prodavan.application.metrics.consumer.set_presence", new=AsyncMock(return_value=True)) as set_mock,
        patch("prodavan.application.metrics.consumer.clear_presence", new=AsyncMock(return_value=True)) as clear_mock,
        patch("prodavan.application.metrics.consumer.get_session_factory"),
    ):
        await handle_auth_event("auth.login", {"sub": "kc_1", "roles": ["employee"]})
        set_mock.assert_awaited_once_with("employee", "emp_1")

        await handle_auth_event("auth.logout", {"sub": "kc_1", "roles": ["employee"]})
        clear_mock.assert_awaited_once_with("employee", "emp_1")


@pytest.mark.asyncio
async def test_handle_platform_envelope_filters_bus() -> None:
    envelope = platform_envelope(
        event_id="e1",
        event_type="auth.login",
        payload={"sub": "kc_1"},
    )
    with patch(
        "prodavan.application.metrics.consumer.handle_auth_event",
        new=AsyncMock(),
    ) as handler:
        await handle_platform_envelope(envelope)
        handler.assert_awaited_once()

    other = platform_envelope(event_id="e2", event_type="company.created")
    with patch(
        "prodavan.application.metrics.consumer.handle_auth_event",
        new=AsyncMock(),
    ) as handler:
        await handle_platform_envelope(other)
        handler.assert_not_awaited()
