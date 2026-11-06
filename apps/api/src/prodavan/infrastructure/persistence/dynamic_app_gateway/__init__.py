"""SQL adapters behind the legacy dynamic-app gateway ports (M06 shadow parity).

See :mod:`prodavan.infrastructure.persistence.dynamic_app_gateway.legacy_ports`
for what is implemented, what is deliberately absent (the quota port, pending a
table that can express a per-invocation limit) and why.
"""

from __future__ import annotations

from .legacy_ports import (
    QUOTA_PORT_BLOCKER,
    QUOTA_PORT_REASON,
    QUOTA_REQUIRED_COLUMNS,
    QUOTA_REQUIRED_TABLE,
    AmbiguousPluginRecord,
    LegacyGatewayPorts,
    SqlCapabilityScopePort,
    SqlDynamicPluginRepository,
    build_legacy_gateway_ports,
)

__all__ = [
    "QUOTA_PORT_BLOCKER",
    "QUOTA_PORT_REASON",
    "QUOTA_REQUIRED_COLUMNS",
    "QUOTA_REQUIRED_TABLE",
    "AmbiguousPluginRecord",
    "LegacyGatewayPorts",
    "SqlCapabilityScopePort",
    "SqlDynamicPluginRepository",
    "build_legacy_gateway_ports",
]
