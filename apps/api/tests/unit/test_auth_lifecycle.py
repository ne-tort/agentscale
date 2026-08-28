"""Unit tests — Auth lifecycle disable/delete + FakeUserAdmin."""

from __future__ import annotations

import pytest

from prodavan.application.auth.lifecycle import (
    AUTH_USER_DELETED,
    AUTH_USER_DISABLED,
    AUTH_USER_ENABLED,
    LifecycleUserCommand,
    apply_lifecycle,
    handle_auth_lifecycle_command,
)
from prodavan.application.auth.user_admin import FakeUserAdmin, reset_user_admin
from prodavan.core.events.envelope import auth_command_envelope


@pytest.fixture(autouse=True)
def _fake_admin(monkeypatch: pytest.MonkeyPatch) -> FakeUserAdmin:
    reset_user_admin()
    fake = FakeUserAdmin()
    monkeypatch.setattr("prodavan.application.auth.lifecycle.get_user_admin", lambda: fake)
    yield fake
    reset_user_admin()


@pytest.mark.asyncio
async def test_disable_user_event(_fake_admin: FakeUserAdmin) -> None:
    event = await apply_lifecycle(
        LifecycleUserCommand(
            request_id="r1",
            client_ref="employee:e1",
            sub="kc_1",
            username="a@t.com",
            email="a@t.com",
            action="disable",
        )
    )
    assert event.event_type == AUTH_USER_DISABLED
    assert _fake_admin.disabled == ["kc_1"]


@pytest.mark.asyncio
async def test_delete_user_event(_fake_admin: FakeUserAdmin) -> None:
    await _fake_admin.register_user(
        username="u@t.com",
        email="u@t.com",
        password=None,
        realm_roles=["employee"],
        display_name=None,
    )
    sub = _fake_admin._by_email["u@t.com"]
    event = await apply_lifecycle(
        LifecycleUserCommand(
            request_id="r2",
            client_ref="employee:e2",
            sub=sub,
            username="u@t.com",
            email="u@t.com",
            action="delete",
        )
    )
    assert event.event_type == AUTH_USER_DELETED
    assert sub in _fake_admin.deleted


@pytest.mark.asyncio
async def test_enable_user_event(_fake_admin: FakeUserAdmin) -> None:
    await _fake_admin.register_user(
        username="u2@t.com",
        email="u2@t.com",
        password=None,
        realm_roles=["employee"],
        display_name=None,
    )
    sub = _fake_admin._by_email["u2@t.com"]
    _fake_admin.disabled.append(sub)
    event = await apply_lifecycle(
        LifecycleUserCommand(
            request_id="r3",
            client_ref="employee:e3",
            sub=sub,
            username="u2@t.com",
            email="u2@t.com",
            action="enable",
        )
    )
    assert event.event_type == AUTH_USER_ENABLED
    assert sub not in _fake_admin.disabled


@pytest.mark.asyncio
async def test_handle_delete_command_envelope(_fake_admin: FakeUserAdmin) -> None:
    envelope = auth_command_envelope(
        event_id="e1",
        event_type="auth.user.delete",
        payload={"request_id": "e1", "client_ref": "company:c1", "username": "c1", "sub": None},
    )
    result = await handle_auth_lifecycle_command(envelope)
    assert result is not None
    assert result.event_type == AUTH_USER_DELETED
