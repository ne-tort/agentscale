"""M05 trusted plugin-instance repository/service adapters.

This zone is intentionally independent from the legacy plugin_instances and ORM
modules.  It is a boundary for a reviewed runtime-row source and CAS writer.
"""

from .ports import (
    PluginInstanceAdapterQuarantinePort,
    RuntimeInstanceCasPort,
    RuntimeInstanceReadPort,
)
from .repository import TrustedPluginInstanceRepository
from .service import TrustedPluginInstanceService

__all__ = [
    "PluginInstanceAdapterQuarantinePort",
    "RuntimeInstanceCasPort",
    "RuntimeInstanceReadPort",
    "TrustedPluginInstanceRepository",
    "TrustedPluginInstanceService",
]
