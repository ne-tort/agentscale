"""Unit tests — Keycloak Admin HTTP client (mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.domain.errors import AppError
from prodavan.infrastructure.keycloak.admin_client import HttpKeycloakAdminClient
from prodavan.infrastructure.keycloak.provisioning import FakeIdentityProvisioning


def _client() -> HttpKeycloakAdminClient:
    return HttpKeycloakAdminClient(
        base_url="http://keycloak.test",
        realm="prodavan",
        client_id="admin-cli",
        client_secret="secret",
    )


def _mock_http(*, post_side_effect, get_side_effect=None, get_return=None, put_return=None):
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=post_side_effect)
    if get_side_effect is not None:
        mock_http.get = AsyncMock(side_effect=get_side_effect)
    else:
        mock_http.get = AsyncMock(return_value=get_return)
    mock_http.put = AsyncMock(return_value=put_return or MagicMock(status_code=204))
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    return mock_http


@pytest.mark.asyncio
async def test_invite_employee_happy_path() -> None:
    client = _client()

    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "adm-token", "expires_in": 60}

    create_resp = MagicMock(status_code=201, text="")
    create_resp.headers = {"Location": "http://keycloak.test/admin/realms/prodavan/users/kc-123"}

    role_resp = MagicMock(status_code=200)
    role_resp.json.return_value = {"id": "role-emp", "name": "employee"}

    map_resp = MagicMock(status_code=204)
    actions_resp = MagicMock(status_code=204)

    # Order: token POST → role GET → create POST → map POST → actions PUT
    mock_http = _mock_http(
        post_side_effect=[token_resp, create_resp, map_resp],
        get_return=role_resp,
        put_return=actions_resp,
    )

    with patch("prodavan.infrastructure.keycloak.admin_client.httpx.AsyncClient", return_value=mock_http):
        result = await client.invite_employee(email="user@test.com", display_name="Test User")

    assert result.keycloak_user_id == "kc-123"
    assert result.email == "user@test.com"
    assert "employee" in result.realm_roles
    # Role resolved before create (no orphan user if role missing)
    assert mock_http.get.await_count >= 1
    assert mock_http.post.await_count == 3


@pytest.mark.asyncio
async def test_create_company_principal() -> None:
    client = _client()

    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "adm-token", "expires_in": 60}
    create_resp = MagicMock(status_code=201, text="")
    create_resp.headers = {"Location": "http://keycloak.test/admin/realms/prodavan/users/kc-co"}
    role_resp = MagicMock(status_code=200)
    role_resp.json.return_value = {"id": "role-co", "name": "company"}
    map_resp = MagicMock(status_code=204)

    mock_http = _mock_http(
        post_side_effect=[token_resp, create_resp, map_resp],
        get_return=role_resp,
    )

    with patch("prodavan.infrastructure.keycloak.admin_client.httpx.AsyncClient", return_value=mock_http):
        result = await client.create_company_principal(
            username="co_abc123",
            password="secure-pass-1",
            display_name="Acme",
        )

    assert result.username == "co_abc123"
    assert result.keycloak_user_id == "kc-co"
    assert result.realm_roles == ["company"]
    found = False
    for call in mock_http.post.await_args_list:
        json_body = call.kwargs.get("json")
        if isinstance(json_body, dict) and json_body.get("username") == "co_abc123":
            assert json_body["credentials"][0]["value"] == "secure-pass-1"
            assert "email" not in json_body
            found = True
    assert found


@pytest.mark.asyncio
async def test_fail_fast_missing_realm_role_before_create() -> None:
    client = _client()

    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "adm-token", "expires_in": 60}
    missing_role = MagicMock(status_code=404)
    missing_role.json.return_value = {}

    mock_http = _mock_http(
        post_side_effect=[token_resp],
        get_return=missing_role,
    )

    with patch("prodavan.infrastructure.keycloak.admin_client.httpx.AsyncClient", return_value=mock_http):
        with pytest.raises(AppError) as exc:
            await client.invite_employee(email="norole@test.com", display_name=None)
    assert exc.value.code == "KEYCLOAK_ADMIN"
    assert "employee" in (exc.value.detail or "")
    # Must not create user when role is missing
    assert mock_http.post.await_count == 1  # token only


@pytest.mark.asyncio
async def test_idempotent_invite_on_409() -> None:
    client = _client()

    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "adm-token", "expires_in": 60}
    conflict = MagicMock(status_code=409, text="exists")
    lookup = MagicMock(status_code=200)
    lookup.json.return_value = [{"id": "kc-existing"}]
    role_resp = MagicMock(status_code=200)
    role_resp.json.return_value = {"id": "role-emp", "name": "employee"}
    map_resp = MagicMock(status_code=204)
    actions_resp = MagicMock(status_code=204)

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=[token_resp, conflict, map_resp])
    mock_http.get = AsyncMock(side_effect=[role_resp, lookup])
    mock_http.put = AsyncMock(return_value=actions_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("prodavan.infrastructure.keycloak.admin_client.httpx.AsyncClient", return_value=mock_http):
        result = await client.invite_employee(email="dup@test.com", display_name=None)

    assert result.keycloak_user_id == "kc-existing"


@pytest.mark.asyncio
async def test_company_principal_409_resets_password() -> None:
    client = _client()

    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "adm-token", "expires_in": 60}
    conflict = MagicMock(status_code=409, text="exists")
    lookup = MagicMock(status_code=200)
    lookup.json.return_value = [{"id": "kc-co-existing"}]
    role_resp = MagicMock(status_code=200)
    role_resp.json.return_value = {"id": "role-co", "name": "company"}
    map_resp = MagicMock(status_code=204)
    reset_resp = MagicMock(status_code=204)

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=[token_resp, conflict, map_resp])
    mock_http.get = AsyncMock(side_effect=[role_resp, lookup])
    mock_http.put = AsyncMock(return_value=reset_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("prodavan.infrastructure.keycloak.admin_client.httpx.AsyncClient", return_value=mock_http):
        result = await client.create_company_principal(
            username="co_reuse",
            password="new-secret-99",
            display_name="Reuse",
        )

    assert result.keycloak_user_id == "kc-co-existing"
    reset_urls = [str(c.args[0]) for c in mock_http.put.await_args_list if c.args]
    assert any("reset-password" in u for u in reset_urls)


@pytest.mark.asyncio
async def test_admin_token_cache() -> None:
    client = _client()

    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "cached-token", "expires_in": 120}

    role_ok = MagicMock(status_code=200)
    role_ok.json.return_value = {"id": "role-emp", "name": "employee"}

    create1 = MagicMock(status_code=201, text="")
    create1.headers = {"Location": "http://keycloak.test/admin/realms/prodavan/users/u1"}
    create2 = MagicMock(status_code=201, text="")
    create2.headers = {"Location": "http://keycloak.test/admin/realms/prodavan/users/u2"}
    map_resp = MagicMock(status_code=204)
    actions = MagicMock(status_code=204)

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=[token_resp, create1, map_resp, create2, map_resp])
    mock_http.get = AsyncMock(return_value=role_ok)
    mock_http.put = AsyncMock(return_value=actions)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("prodavan.infrastructure.keycloak.admin_client.httpx.AsyncClient", return_value=mock_http):
        r1 = await client.invite_employee(email="a@test.com", display_name=None)
        r2 = await client.invite_employee(email="b@test.com", display_name=None)

    assert r1.keycloak_user_id == "u1"
    assert r2.keycloak_user_id == "u2"
    token_calls = 0
    for c in mock_http.post.await_args_list:
        url = c.args[0] if c.args else c.kwargs.get("url", "")
        if "openid-connect/token" in str(url):
            token_calls += 1
    assert token_calls == 1


@pytest.mark.asyncio
async def test_fake_provisioning_idempotent_by_email() -> None:
    fake = FakeIdentityProvisioning()
    a = await fake.invite_employee(email="Same@Test.com", display_name="A")
    b = await fake.invite_employee(email="same@test.com", display_name="B")
    assert a.keycloak_user_id == b.keycloak_user_id
    assert len(fake.invites) == 2


@pytest.mark.asyncio
async def test_fake_set_company_password() -> None:
    fake = FakeIdentityProvisioning()
    await fake.create_company_principal(
        username="co_1", password="old-pass-99", display_name="Co"
    )
    await fake.set_company_password(username="co_1", password="new-pass-99")
    assert any(
        p.get("action") == "set_password" and p.get("password") == "new-pass-99"
        for p in fake.company_principals
    )
