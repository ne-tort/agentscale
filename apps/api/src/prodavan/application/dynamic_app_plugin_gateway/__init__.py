"""Exclusive dynamic app/plugin backend capability facade zone."""

from .contracts import (
    GatewayOutcome,
    GatewayReason,
    GatewayResult,
    PluginContext,
    PluginHandle,
    PluginInvokeRequest,
    PluginMethod,
    PluginOpenRequest,
    PluginRecord,
    QuarantineEvidence,
)
from .ports import (
    CapabilityExecutor,
    CapabilityQuotaPort,
    CapabilityScopePort,
    DynamicPluginRepository,
    PluginQuarantinePort,
)
from .service import DynamicAppPluginGatewayService

__all__ = [
    "CapabilityExecutor", "CapabilityQuotaPort", "CapabilityScopePort",
    "DynamicAppPluginGatewayService", "DynamicPluginRepository", "GatewayOutcome",
    "GatewayReason", "GatewayResult", "PluginContext", "PluginHandle",
    "PluginInvokeRequest", "PluginMethod", "PluginOpenRequest", "PluginQuarantinePort",
    "PluginRecord", "QuarantineEvidence",
]
