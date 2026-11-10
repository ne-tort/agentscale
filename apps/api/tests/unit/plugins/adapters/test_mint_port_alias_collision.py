"""Маркер: коллизия имён портов и проверка `__init__` адаптеров.

Отдельный файл — по требованию задания «коллизия MintPort разрешена
алиасами»: родитель/ревьюер должен найти этот тест по имени, не листая
все модули.
"""

from __future__ import annotations

from prodavan.application.plugins.adapters import (
    ActivatorMintPort,
    AdminActivationPort,
    AdminMintPort,
    AdminRevocationPort,
    GatewayActivationPort,
    IdentityAdminMintPort,
    IdentityRevocationPort,
)
from prodavan.application.plugins.activation.ports import (
    MintPort as RealActivatorMintPort,
)
from prodavan.application.plugins.admin.ports import (
    ActivationPort as RealAdminActivationPort,
)
from prodavan.application.plugins.admin.ports import (
    MintPort as RealAdminMintPort,
)
from prodavan.application.plugins.admin.ports import (
    RevocationPort as RealAdminRevocationPort,
)
from prodavan.application.plugins.gateway.ports import (
    ActivationPort as RealGatewayActivationPort,
)
from prodavan.application.plugins.identity.revocation import (
    RevocationPort as RealIdentityRevocationPort,
)


def test_mint_port_alias_collision_is_resolved() -> None:
    """``MintPort`` admin ≠ ``MintPort`` activation (разные протоколы)."""

    assert AdminMintPort is RealAdminMintPort
    assert IdentityAdminMintPort is RealAdminMintPort
    assert ActivatorMintPort is RealActivatorMintPort
    assert AdminMintPort is not ActivatorMintPort
    # Сигнатуры разные: admin.mint(request=...) против activator.mint(
    # plugin_instance_id=..., reason=...).
    assert "request" in RealAdminMintPort.mint.__annotations__
    assert "plugin_instance_id" in RealActivatorMintPort.mint.__annotations__


def test_revocation_port_alias_collision_is_resolved() -> None:
    """``RevocationPort`` admin (async, bool) ≠ identity (sync, is_active)."""

    assert AdminRevocationPort is RealAdminRevocationPort
    assert IdentityRevocationPort is RealIdentityRevocationPort
    assert AdminRevocationPort is not IdentityRevocationPort
    assert "reason" in RealAdminRevocationPort.revoke.__annotations__
    assert "now" in RealIdentityRevocationPort.is_active.__annotations__


def test_activation_port_alias_collision_is_resolved() -> None:
    """``ActivationPort`` admin ≠ gateway (WakeResult vs WakeLease)."""

    assert AdminActivationPort is RealAdminActivationPort
    assert GatewayActivationPort is RealGatewayActivationPort
    assert AdminActivationPort is not GatewayActivationPort
    assert "wait_ready" in dir(RealGatewayActivationPort)
    assert "wait_ready" not in dir(RealAdminActivationPort)
