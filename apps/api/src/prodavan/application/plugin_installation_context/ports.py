"""Read-only ports for installation admission and M05/M06 handoff."""

from __future__ import annotations

from typing import Protocol

from .contracts import M05CasContext, M06GrantHandoff, PluginInstallationContext


class InstallationContextReadPort(Protocol):
    """Resolve authoritative anchors; request/plugin values are assertions only."""

    async def resolve(self, *, plugin_instance_id: str, session_id: str) -> PluginInstallationContext | None: ...


class InstallationContextConsistencyPort(Protocol):
    """Prove the complete context is still active and internally compatible."""

    async def is_active(self, context: PluginInstallationContext) -> bool: ...


class M05RuntimeAdmissionPort(Protocol):
    """CAS/runtime admission consumes the exact context and returns only a boolean."""

    async def admit(self, context: M05CasContext) -> bool: ...


class M06GrantAdmissionPort(Protocol):
    """M06 receives only a context admitted by M05; it does not resolve anchors."""

    async def handoff(self, context: M06GrantHandoff) -> None: ...


__all__ = [
    "InstallationContextConsistencyPort", "InstallationContextReadPort",
    "M05RuntimeAdmissionPort", "M06GrantAdmissionPort",
]
