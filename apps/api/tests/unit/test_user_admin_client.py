"""Unit tests — Keycloak UserAdmin HTTP client (mocked) + FakeUserAdmin."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.auth.user_admin import FakeUserAdmin
from prodavan.domain.errors import AppError
from prodavan.domain.identity import ROLE_COMPANY, ROLE_EMPLOYEE
from prodavan.infrastructure.keycloak.user_admin_client import HttpUserAdminClient


def _client() -> HttpUserAdminClient:
    return HttpUserAdminClient(
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
async def test_register_employee_happy_path() -> None:
    client = _client()

    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "adm-token", "expires_in": 60}

    create_resp = MagicMock(status_code=201, text="")
    create_resp.headers = {"Location": "http://keycloak.test/admin/realms/prodavan/users/kc-123"}

    role_resp = MagicMock(status_code=200)
    role_resp.json.return_value = {"id": "role-emp", "name": "employee"}

    map_resp = MagicMock(status_code=204)

    mock_http = _mock_http(
        post_side_effect=[token_resp, create_resp, map_resp],
        get_return=role_resp,
        put_return=MagicMock(status_code=204),
    )

    with patch(
        "prodavan.infrastructure.keycloak.user_admin_client.httpx.AsyncClient",
        return_value=mock_http,
    ):
        result = await client.register_user(
            username="user@test.com",
            email="user@test.com",
            password=None,
            realm_roles=[ROLE_EMPLOYEE],
            display_name="Test User",
        )

    assert result.keycloak_user_id == "kc-123"
    assert result.email == "user@test.com"
    assert "employee" in result.realm_roles
    assert mock_http.get.await_count >= 1
    assert mock_http.post.await_count == 3
    assert mock_http.put.await_count >= 1


@pytest.mark.asyncio
async def test_register_company_principal() -> None:
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

    with patch(
        "prodavan.infrastructure.keycloak.user_admin_client.httpx.AsyncClient",
        return_value=mock_http,
    ):
        result = await client.register_user(
            username="co_abc123",
            email="co_abc123@companies.prodavan.local",
            password="secure-pass-1",
            realm_roles=[ROLE_COMPANY],
            display_name="Acme",
        )

    assert result.username == "co_abc123"
    assert result.keycloak_user_id == "kc-co"
    assert result.realm_roles == ["company"]
    found = False
    for call in mock_http.post.await_args_list:
        json_body = call.kwargs.get("json")
        if isinstance(json_body, dict) and json_body.get("username") == "co_abc123":
            found = True
            assert "credentials" in json_body
    assert found


@pytest.mark.asyncio
async def test_register_fails_when_role_missing() -> None:
    client = _client()
    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "adm-token", "expires_in": 60}
    role_missing = MagicMock(status_code=404)
    mock_http = _mock_http(post_side_effect=[token_resp], get_return=role_missing)

    with patch(
        "prodavan.infrastructure.keycloak.user_admin_client.httpx.AsyncClient",
        return_value=mock_http,
    ):
        with pytest.raises(AppError) as ei:
            await client.register_user(
                username="norole@test.com",
                email="norole@test.com",
                password=None,
                realm_roles=[ROLE_EMPLOYEE],
                display_name=None,
            )
    assert ei.value.code == "KEYCLOAK_ADMIN"


@pytest.mark.asyncio
async def test_register_reuses_existing_email() -> None:
    client = _client()
    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "adm-token", "expires_in": 60}
    conflict = MagicMock(status_code=409)
    lookup = MagicMock(status_code=200)
    lookup.json.return_value = [{"id": "kc-dup"}]
    role_resp = MagicMock(status_code=200)
    role_resp.json.return_value = {"id": "role-emp", "name": "employee"}
    map_resp = MagicMock(status_code=204)

    mock_http = _mock_http(
        post_side_effect=[token_resp, conflict, map_resp],
        get_side_effect=[role_resp, lookup],
        put_return=MagicMock(status_code=204),
    )

    with patch(
        "prodavan.infrastructure.keycloak.user_admin_client.httpx.AsyncClient",
        return_value=mock_http,
    ):
        result = await client.register_user(
            username="dup@test.com",
            email="dup@test.com",
            password=None,
            realm_roles=[ROLE_EMPLOYEE],
            display_name=None,
        )
    assert result.keycloak_user_id == "kc-dup"


@pytest.mark.asyncio
async def test_fake_user_admin_idempotent() -> None:
    fake = FakeUserAdmin()
    a = await fake.register_user(
        username="Same@Test.com",
        email="Same@Test.com",
        password=None,
        realm_roles=[ROLE_EMPLOYEE],
        display_name="A",
    )
    b = await fake.register_user(
        username="same@test.com",
        email="same@test.com",
        password=None,
        realm_roles=[ROLE_EMPLOYEE],
        display_name="B",
    )
    assert a.keycloak_user_id == b.keycloak_user_id

    c = await fake.register_user(
        username="co1",
        email="co1@companies.prodavan.local",
        password="pass-pass-1",
        realm_roles=[ROLE_COMPANY],
        display_name="Co",
    )
    assert c.keycloak_user_id.startswith("kc_co_fake_")
