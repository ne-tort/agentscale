"""Compatibility import for the MIG-AUTO-03 durable control plane.

The implementation lives in :mod:`.service`; this module keeps the old local
module path stable while preventing callers from composing the legacy run,
approval, and outbox repositories.
"""

from .service import ControlPlaneResultError, ControlPlaneWriteError, DurableAgentControlPlane

__all__ = ["ControlPlaneResultError", "ControlPlaneWriteError", "DurableAgentControlPlane"]
