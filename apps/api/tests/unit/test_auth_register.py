"""Unit tests — Auth register command + Fake user admin + bind client_ref."""

from __future__ import annotations

import pytest

from prodavan.application.auth.register import (
    AUTH_USER_REGISTERED,
    RegisterUserCommand,
    handle_auth_command_envelope,
    register_user,
)
from prodavan.application.auth.user_admin import FakeUserAdmin, reset_user_admin
from prodavan.application.identity.auth_bind import parse_client_ref
from prodavan.core.events.envelope import auth_command_envelope
from prodavan.domain.identity import ROLE_COMPANY, ROLE_EMPLOYEE


@pytest.fixture(autouse=True)
def _reset_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_user_admin()
    fake = FakeUserAdmin()
    monkeypatch.setattr(
        "prodavan.application.auth.register.get_user_admin",
        lambda: fake,
    )
    yield fake
    reset_user_admin()


@pytest.mark.asyncio
async def test_register_user_company_emits_registered(_reset_admin: FakeUserAdmin) -> None:
    event = await register_user(
        RegisterUserCommand(
            request_id="req-1",
            client_ref="company:co_1",
            username="co_1",
            email="co_1@companies.prodavan.local",
            password="secure-pass-1",
            realm_roles=[ROLE_COMPANY],
            display_name="Acme",
        )
    )
    assert event.event_type == AUTH_USER_REGISTERED
    assert event.payload["client_ref"] == "company:co_1"
    assert event.payload["sub"].startswith("kc_co_fake_")
    assert event.payload["username"] == "co_1"
    assert len(_reset_admin.registrations) == 1


@pytest.mark.asyncio
async def test_register_user_employee_idempotent(_reset_admin: FakeUserAdmin) -> None:
    cmd = RegisterUserCommand(
        request_id="req-2",
        client_ref="employee:emp_1",
        username="a@test.com",
        email="a@test.com",
        password=None,
        realm_roles=[ROLE_EMPLOYEE],
        display_name=None,
    )
    e1 = await register_user(cmd)
    e2 = await register_user(cmd)
    assert e1.payload["sub"] == e2.payload["sub"]
    assert len(_reset_admin.registrations) == 2


@pytest.mark.asyncio
async def test_handle_auth_command_envelope(_reset_admin: FakeUserAdmin) -> None:
    envelope = auth_command_envelope(
        event_id="e1",
        event_type="auth.user.register",
        payload={
            "request_id": "e1",
            "client_ref": "employee:x",
            "username": "u@t.com",
            "email": "u@t.com",
            "password": None,
            "realm_roles": ["employee"],
        },
    )
    result = await handle_auth_command_envelope(envelope)
    assert result is not None
    assert result.event_type == AUTH_USER_REGISTERED
    assert result.payload["client_ref"] == "employee:x"


def test_parse_client_ref() -> None:
    assert parse_client_ref("company:abc") == ("company", "abc")
    assert parse_client_ref("employee:xyz") == ("employee", "xyz")
    assert parse_client_ref("weird") is None
    assert parse_client_ref("") is None
