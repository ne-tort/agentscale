"""Auth error mapping — Keycloak responses to domain AppError codes."""

from __future__ import annotations

import pytest

from prodavan.application.auth.errors import raise_for_keycloak_token_response
from prodavan.domain.errors import AppError


def test_invalid_grant_maps_to_invalid_credentials() -> None:
    with pytest.raises(AppError) as exc:
        raise_for_keycloak_token_response(
            401,
            '{"error":"invalid_grant","error_description":"Token is not active"}',
        )
    assert exc.value.code == "INVALID_CREDENTIALS"
    assert exc.value.status == 401


def test_keycloak_5xx_maps_to_identity_provider() -> None:
    with pytest.raises(AppError) as exc:
        raise_for_keycloak_token_response(503, '{"error":"server_unavailable"}')
    assert exc.value.code == "IDENTITY_PROVIDER"
    assert exc.value.status == 502
