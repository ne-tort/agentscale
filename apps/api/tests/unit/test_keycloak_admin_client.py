"""Unit tests — Keycloak Admin client disable / password (mocked)."""

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


@pytest.mark.asyncio
async def test_set_company_password() -> None:
    client = _client()
    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "adm-token", "expires_in": 60}
    lookup = MagicMock(status_code=200)
    lookup.json.return_value = [{"id": "kc-co"}]
    reset = MagicMock(status_code=204)

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=[token_resp])
    mock_http.get = AsyncMock(return_value=lookup)
    mock_http.put = AsyncMock(return_value=reset)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("prodavan.infrastructure.keycloak.admin_client.httpx.AsyncClient", return_value=mock_http):
        await client.set_company_password(username="co_1", password="new-pass-99")

    assert mock_http.put.await_count == 1
    assert "reset-password" in str(mock_http.put.await_args.args[0])


@pytest.mark.asyncio
async def test_set_company_password_not_found() -> None:
    client = _client()
    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "adm-token", "expires_in": 60}
    lookup = MagicMock(status_code=200)
    lookup.json.return_value = []

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=[token_resp])
    mock_http.get = AsyncMock(return_value=lookup)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("prodavan.infrastructure.keycloak.admin_client.httpx.AsyncClient", return_value=mock_http):
        with pytest.raises(AppError) as ei:
            await client.set_company_password(username="missing", password="new-pass-99")
    assert ei.value.code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_fake_set_company_password() -> None:
    fake = FakeIdentityProvisioning()
    await fake.set_company_password(username="co_1", password="new-pass-99")
    assert any(
        p.get("action") == "set_password" and p.get("password") == "new-pass-99"
        for p in fake.company_principals
    )
