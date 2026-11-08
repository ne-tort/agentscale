"""F1: совместимость ``PluginClaims`` со структурным протоколом задачи 03.

Тест **написан, но не запускается** (verification freeze).
"""

from __future__ import annotations

from prodavan.application.plugins.capabilities.ports import PluginClaims as CapabilityPluginClaimsPort
from prodavan.application.plugins.identity import PluginClaims


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
