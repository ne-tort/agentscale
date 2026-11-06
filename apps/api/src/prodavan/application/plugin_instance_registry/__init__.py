"""T02 persistence-independent plugin-instance registry application adapter."""

from .contracts import (
    PluginInstanceRegistryIdentity,
    PluginInstanceRegistryOutcome,
    PluginInstanceRegistryQuery,
    PluginInstanceRegistryReason,
    PluginInstanceRegistryRecord,
    PluginInstanceRegistryResolution,
    PluginInstanceRegistryState,
)
from .ports import PluginInstanceRegistryQuarantinePort, PluginInstanceRegistrySourcePort, RegistryRow
from .service import PluginInstanceRegistryService

__all__ = [
    "PluginInstanceRegistryIdentity", "PluginInstanceRegistryOutcome",
    "PluginInstanceRegistryQuery", "PluginInstanceRegistryReason",
    "PluginInstanceRegistryRecord", "PluginInstanceRegistryResolution",
    "PluginInstanceRegistryState", "PluginInstanceRegistryQuarantinePort",
    "PluginInstanceRegistrySourcePort", "RegistryRow", "PluginInstanceRegistryService",
]
