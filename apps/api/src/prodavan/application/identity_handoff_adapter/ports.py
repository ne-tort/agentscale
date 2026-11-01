"""Read-only ports for identity handoff validation and downstream admission."""

from __future__ import annotations

from typing import Mapping, Protocol

from prodavan.application.plugin_installation_context import (
    M05CasContext,
    M06GrantHandoff,
)
from prodavan.application.shared_identity import IdentityEnvelope

from .contracts import AuditEvidenceLink, IdentityHandoff


class M05CasHandoffPort(Protocol):
    """Admit the exact M05 CAS context; implementations own no identity data."""

    async def admit(self, context: M05CasContext) -> bool: ...


class M06GrantHandoffPort(Protocol):
    """Receive only a successful M05 handoff and its immutable identity link."""

    async def handoff(self, context: M06GrantHandoff, evidence: AuditEvidenceLink) -> None: ...


class IdentityHandoffPort(Protocol):
    """Produce a validated handoff without persistence or route concerns."""

    async def bridge(
        self, *, envelope: IdentityEnvelope, evidence: AuditEvidenceLink
    ) -> IdentityHandoff: ...


class SafePropagationPort(Protocol):
    """Transport boundary limited to non-secret correlation and digest headers."""

    def inject(self, handoff: IdentityHandoff) -> Mapping[str, str]: ...


__all__ = [
    "IdentityHandoffPort",
    "M05CasHandoffPort",
    "M06GrantHandoffPort",
    "SafePropagationPort",
]
