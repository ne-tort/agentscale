"""Unit tests — Keycloak Admin HTTP client (mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.infrastructure.keycloak.admin_client import HttpKeycloakInviteClient


@pytest.mark.asyncio
async def test_keycloak_invite_user_happy_path() -> None:
    client = HttpKeycloakInviteClient(
        base_url="http://keycloak.test",
        realm="prodavan",
        client_id="admin-cli",
        client_secret="secret",
    )

    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "adm-token"}

    create_resp = MagicMock(status_code=201, text="")
    create_resp.headers = {"Location": "http://keycloak.test/admin/realms/prodavan/users/kc-123"}

    actions_resp = MagicMock(status_code=204)

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=[token_resp, create_resp])
    mock_http.put = AsyncMock(return_value=actions_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("prodavan.infrastructure.keycloak.admin_client.httpx.AsyncClient", return_value=mock_http):
        result = await client.invite_user(email="user@test.com", display_name="Test User")

    assert result.keycloak_user_id == "kc-123"
    assert result.email == "user@test.com"
