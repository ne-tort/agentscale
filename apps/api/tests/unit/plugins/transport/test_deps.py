"""Unit-тесты ``get_plugin_claims`` (задача 04 / F1-стык).

Проверяется цепочка: Bearer -> verify -> revocation -> ``assert_plugin_surface``.
Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (Verification freeze).
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from prodavan.application.plugins.identity.keys import keyring_from_secret
from prodavan.application.plugins.identity.mint import mint_plugin_claims
from prodavan.application.plugins.identity.revocation import GrantRevocation, InMemoryRevocation
from prodavan.application.plugins.transport.conftest import (
    FakeGrant,
    FakeInstance,
    TEST_COMPANY,
    TEST_SECRET,
)
from prodavan.application.plugins.transport.deps import (
    UNTRUSTED_PLUGIN_HEADERS,
    PluginIdentityConfig,
    _bearer_token,
    get_plugin_claims,
)
from prodavan.application.plugins.transport.errors import PluginTransportError


def _request(path: str, token: str | None, extra_headers: dict | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    for name, value in (extra_headers or {}).items():
        headers.append((name.lower().encode(), value.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1234),
        "root_path": "",
    }
    return Request(scope)


@pytest.fixture
def config():
    return PluginIdentityConfig(
        keyring=keyring_from_secret(TEST_SECRET),
        revocation=GrantRevocation(InMemoryRevocation()),
    )


@pytest.fixture
def token() -> str:
    return mint_plugin_claims(
        grant=FakeGrant(), instance=FakeInstance(), secret=TEST_SECRET
    )


def test_bearer_extraction() -> None:
    assert _bearer_token(_request("/capabilities/v1/cache/get", "abc")) == "abc"
    assert _bearer_token(_request("/capabilities/v1/cache/get", None)) is None
    assert _bearer_token(
        _request("/capabilities/v1/cache/get", None, {"Authorization": "Basic abc"})
    ) is None


def test_valid_token_returns_claims(config, token: str) -> None:
    claims = get_plugin_claims(_request("/capabilities/v1/cache/get", token), config)
    assert claims.typ == "plugin_capability"
    assert claims.company_id == TEST_COMPANY
    assert claims.grant_id == "grant_01"


def test_missing_token_raises_401(config) -> None:
    with pytest.raises(PluginTransportError) as exc:
        get_plugin_claims(_request("/capabilities/v1/cache/get", None), config)
    assert exc.value.problem.status == 401
    assert exc.value.problem.code == "UNAUTHORIZED"


def test_assert_plugin_surface_is_called_for_agent_path(config, token: str) -> None:
    """ОБЯЗАТЕЛЬНЫЙ вызов allowlist: agent-surface закрыт (кр. 17)."""
    for path in (
        "/agent/events",
        "/api/v1/agent/events",
        "/api/v1/internal/pods/pod-1/workspace-archive",
        "/api/v1/projects/p1/modules",
        "/webhook/x/y",
        "/pluginpage/x/y/api",
    ):
        with pytest.raises(PluginTransportError) as exc:
            get_plugin_claims(_request(path, token), config)
        assert exc.value.problem.status == 403
        assert exc.value.problem.code == "FORBIDDEN_SURFACE"


def test_path_with_query_or_traversal_is_rejected(config, token: str) -> None:
    for path in ("/capabilities/v1/cache/get?x=1", "/capabilities/v1/../agent/events"):
        with pytest.raises(PluginTransportError):
            get_plugin_claims(_request(path, token), config)


def test_revoked_grant_raises_403(token: str) -> None:
    config = PluginIdentityConfig(
        keyring=keyring_from_secret(TEST_SECRET),
        revocation=GrantRevocation(InMemoryRevocation(initially_revoked=("grant_01",))),
    )
    with pytest.raises(PluginTransportError) as exc:
        get_plugin_claims(_request("/capabilities/v1/cache/get", token), config)
    assert exc.value.problem.status == 403
    assert exc.value.problem.code == "GRANT_REVOKED"


def test_untrusted_headers_are_not_read(config, token: str) -> None:
    """X-Plugin-* / X-Company-ID игнорируются: identity только из claims."""
    headers = {
        "X-Company-ID": "evil",
        "X-Plugin-ID": "evil",
        "X-Plugin-Instance-Id": "evil",
        "X-Project-ID": "evil",
        "X-Plugin-Capability-Token": "evil",
    }
    claims = get_plugin_claims(
        _request("/capabilities/v1/cache/get", token, headers), config
    )
    assert claims.company_id == TEST_COMPANY
    assert claims.plugin_id == "mod_equipment"
    assert "x-company-id" in UNTRUSTED_PLUGIN_HEADERS
    assert "x-plugin-capability-token" in UNTRUSTED_PLUGIN_HEADERS


def test_wrong_typ_token_raises_401(config) -> None:
    import jwt

    pod_token = jwt.encode(
        {"typ": "pod_bridge", "sub": "pod:1", "exp": 2_000_000_000, "iat": 1_700_000_000},
        TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(PluginTransportError) as exc:
        get_plugin_claims(_request("/capabilities/v1/cache/get", pod_token), config)
    assert exc.value.problem.status == 401
    assert exc.value.problem.code == "WRONG_TYP"


def test_invalid_signature_raises_401(config, token: str) -> None:
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    with pytest.raises(PluginTransportError) as exc:
        get_plugin_claims(_request("/capabilities/v1/cache/get", tampered), config)
    assert exc.value.problem.status == 401


def test_health_path_is_allowed(config, token: str) -> None:
    claims = get_plugin_claims(_request("/capabilities/v1/health", token), config)
    assert claims.company_id == TEST_COMPANY
