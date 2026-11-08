"""F1: отдельная ветка allowlist (критерий №17, §4.1 / §13.3).

Тесты **написаны, но не запускаются** (verification freeze).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.identity import (
    PLUGIN_TOKEN_TYP,
    PluginClaims,
    PluginSurfaceAllowlist,
    PluginTokenError,
    PluginTokenErrorCode,
    assert_plugin_surface,
    is_plugin_capability_token,
    is_plugin_surface_path,
    mint_plugin_claims,
)
from tests.unit.plugins.identity.conftest import TEST_SECRET, FakeGrant, FakeInstance


# --- capabilities surface ----------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/capabilities/v1/cache",
        "/capabilities/v1/cache/get",
        "/capabilities/v1/objects",
        "/capabilities/v1/events/publish",
        "/capabilities/v1/db.direct",
        "/capabilities/v1/network/egress",
        "/capabilities/v1/cache/",
    ],
)
def test_capabilities_paths_are_allowed(path: str, allowlist: PluginSurfaceAllowlist, claims: PluginClaims) -> None:
    assert allowlist.is_allowed(claims, path) is True
    assert is_plugin_surface_path(path) is True
    assert allowlist.assert_plugin_surface(claims, path) is claims
    assert assert_plugin_surface(claims, path) is claims


# --- agent-surface ЗАКРЫТ для typ=plugin_capability --------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/agent/events",
        "/api/v1/agent/events",
        "/projects/p1/agent/events",
        "/internal/pods/x/workspace-archive",
        "/api/v1/internal/pods/x/workspace-archive",
        "/internal/pods/x/hydrate",
        "/projects/p1/modules/m1/data/t",
        "/api/v1/projects/p1/modules/m1/data/t",
        "/projects/p1/infra/cache",
        "/projects/p1/documents",
        "/projects/p1/web-search",
        "/projects/p1/web-fetch",
    ],
)
def test_agent_surface_is_closed_for_plugin_claims(path: str, allowlist: PluginSurfaceAllowlist, claims: PluginClaims) -> None:
    """Критерий №17: плагин с PluginClaims не имеет доступа к agent-surface."""
    assert allowlist.is_allowed(claims, path) is False
    assert is_plugin_surface_path(path) is False
    with pytest.raises(PluginTokenError) as ei:
        allowlist.assert_plugin_surface(claims, path)
    assert ei.value.code == PluginTokenErrorCode.FORBIDDEN_SURFACE
    with pytest.raises(PluginTokenError) as ei:
        assert_plugin_surface(claims, path)
    assert ei.value.code == PluginTokenErrorCode.FORBIDDEN_SURFACE


# --- внешние пути плагинов тоже не capability-surface ------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/webhook/mod_equipment/co_123/order",
        "/pluginpage/mod_equipment/co_123/api/rows",
        "/gateway/plugins/mod_equipment/revoke",
        "/gateway/cron/register",
        "/health",
        "/api/v1/projects",
        "",
    ],
)
def test_non_capability_paths_are_closed(path: str, allowlist: PluginSurfaceAllowlist, claims: PluginClaims) -> None:
    assert allowlist.is_allowed(claims, path) is False


# --- обходы ------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/capabilities/v1/../agent/events",
        "/capabilities/v1/../../internal/pods/x/workspace-archive",
        "/capabilities/v1//agent/events",
        "/capabilities/v1/cache?redirect=/agent/events",
        "/capabilities/v1/cache#/agent/events",
        "/capabilities/v1/cache\\agent\\events",
        "capabilities/v1/cache",
        "https://evil.example.com/capabilities/v1/cache",
        "/capabilities/v1",              # без имени capability
        "/capabilities/v2/cache",        # другая версия
        "/CAPABILITIES/V1/CACHE",        # регистр важен
    ],
)
def test_path_traversal_and_query_are_closed(path: str) -> None:
    assert is_plugin_surface_path(path) is False


def test_wrong_typ_is_closed_even_on_valid_capabilities_path(allowlist: PluginSurfaceAllowlist, claims: PluginClaims) -> None:
    """Токен агентского контура не должен пройти plugin-ветку allowlist."""
    wrong = PluginClaims(
        typ="pod_bridge",
        company_id=claims.company_id,
        grant_id=claims.grant_id,
    )
    assert allowlist.is_allowed(wrong, "/capabilities/v1/cache") is False
    with pytest.raises(PluginTokenError) as ei:
        allowlist.assert_plugin_surface(wrong, "/capabilities/v1/cache")
    assert ei.value.code == PluginTokenErrorCode.FORBIDDEN_SURFACE


# --- peeking / ветвление middleware -----------------------------------------

def test_is_plugin_capability_token_detects_typ_without_verification() -> None:
    token = mint_plugin_claims(grant=FakeGrant(), instance=FakeInstance(), secret=TEST_SECRET)
    assert is_plugin_capability_token(token) is True
    assert is_plugin_capability_token("garbage") is False


def test_pod_bridge_token_is_not_classified_as_plugin_token() -> None:
    import jwt

    bridge = jwt.encode({"typ": "pod_bridge", "sub": "pod:p1"}, "x", algorithm="HS256")
    assert is_plugin_capability_token(bridge) is False


def test_default_allowlist_instance_is_exported() -> None:
    from prodavan.application.plugins.identity import plugin_surface_allowlist

    assert isinstance(plugin_surface_allowlist, PluginSurfaceAllowlist)
    claims = PluginClaims(typ=PLUGIN_TOKEN_TYP)
    assert plugin_surface_allowlist.is_allowed(claims, "/capabilities/v1/cache") is True
