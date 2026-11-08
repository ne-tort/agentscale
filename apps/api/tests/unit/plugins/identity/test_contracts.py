"""F1: совместимость ``PluginClaims`` со структурным протоколом задачи 03.

Тест **написан, но не запускается** (verification freeze).
"""

from __future__ import annotations

from prodavan.application.plugins.capabilities.ports import PluginClaims as CapabilityPluginClaimsPort
from prodavan.application.plugins.identity import (
    PLUGIN_TOKEN_ERROR_CODES,
    PluginClaims,
    PluginTokenErrorCode,
)


def test_plugin_token_error_codes_match_required_set() -> None:
    """Все коды из ТЗ на месте; константа и StrEnum не расходятся."""
    required = {
        "INVALID_SIGNATURE",
        "WRONG_AUDIENCE",
        "WRONG_TYP",
        "EXPIRED",
        "MALFORMED",
        "MISSING_CLAIM",
        "UNKNOWN_KID",
    }
    assert required <= PLUGIN_TOKEN_ERROR_CODES
    assert {code.value for code in PluginTokenErrorCode} == PLUGIN_TOKEN_ERROR_CODES


def test_plugin_claims_satisfies_capability_protocol() -> None:


def test_plugin_claims_satisfies_capability_protocol() -> None:
    """F2 описал PluginClaims структурно; наш dataclass должен ему отвечать."""
    claims = PluginClaims(
        sub="plugin:mod_equipment:co_123:inst_abc",
        company_id="co_123",
        plugin_instance_id="inst_abc",
        artifact_digest="sha256:" + "a" * 64,
        grant_id="grant_01",
        scopes=("cache",),
    )
    assert isinstance(claims, CapabilityPluginClaimsPort)  # runtime_checkable
    assert claims.sub.startswith("plugin:")
    assert claims.scopes == ("cache",)
