"""Unit tests for Auth Service claim decode + Keycloak error mapping."""

from __future__ import annotations

import base64
import json

import pytest

from prodavan.application.auth.errors import raise_for_keycloak_token_response
from prodavan.application.auth.service import decode_access_claims
from prodavan.domain.errors import AppError


def _jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


def test_decode_access_claims_sub_and_roles() -> None:
    token = _jwt(
        {
            "sub": "kc-user-1",
            "email": "a@b.c",
            "preferred_username": "admin",
            "realm_access": {"roles": ["platform.admin", "employee"]},
        }
    )
    claims = decode_access_claims(token)
    assert claims.sub == "kc-user-1"
    assert "platform.admin" in claims.roles
    assert claims.email == "a@b.c"
    assert claims.username == "admin"


def test_raise_for_invalid_grant() -> None:
    with pytest.raises(AppError) as ei:
        raise_for_keycloak_token_response(
            401,
            '{"error":"invalid_grant","error_description":"Invalid user credentials"}',
        )
    assert ei.value.code == "INVALID_CREDENTIALS"
    assert ei.value.status == 401
    assert "Invalid user credentials" in (ei.value.detail or "")
