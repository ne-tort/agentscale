"""Pure identity bridge from shared envelope to M05, M06, and evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from prodavan.application.plugin_installation_context import (
    M05CasContext,
    M06GrantHandoff,
    PluginInstallationContext,
)
from prodavan.application.shared_identity import IdentityContext, IdentityEnvelope

from .contracts import AuditEvidenceLink, IdentityHandoff
from .ports import M05CasHandoffPort, M06GrantHandoffPort


class IdentityHandoffAdapter:
    """Validate one complete envelope, then perform ordered read-only handoffs."""

    def __init__(
        self,
        *,
        m05: M05CasHandoffPort,
        m06: M06GrantHandoffPort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._m05 = m05
        self._m06 = m06
        self._clock = clock or (lambda: datetime.now(UTC))

    async def bridge(
        self,
        *,
        envelope: IdentityEnvelope,
        evidence: AuditEvidenceLink,
        hop: str = "identity_handoff_adapter",
    ) -> IdentityHandoff:
        """Create and admit the exact context; reject stale or mismatched input."""
        now = self._clock()
        self._validate(envelope, evidence, now=now)
        propagation = IdentityContext(envelope=envelope, hop=hop)
        installation = PluginInstallationContext(
            tenant_id=envelope.tenant_id,
            cabinet_id=envelope.cabinet_id,
            project_id=envelope.project_id,
            scope_kind=envelope.scope_kind,
            scope_id=envelope.scope_id,
            plugin_instance_id=envelope.plugin_instance_id,
            definition_id=envelope.definition_id,
            version_id=envelope.version_id,
            artifact_digest=envelope.artifact_digest,
            subject_id=envelope.subject_id,
            session_id=envelope.session_id,
            runtime_generation=envelope.runtime_generation,
            writer_generation=envelope.writer_generation,
            lease_owner=envelope.lease_owner,
            lease_id=envelope.lease_id,
            observed_at=now,
        )
        m05 = M05CasContext(installation)
        if not await self._m05.admit(m05):
            raise ValueError("M05 CAS admission denied")
        m06 = M06GrantHandoff(installation, runtime_admitted=True)
        handoff = IdentityHandoff(
            envelope=envelope,
            context=propagation,
            installation=installation,
            m05=m05,
            m06=m06,
            evidence=evidence,
        )
        if self._m06 is not None:
            await self._m06.handoff(m06, evidence)
        return handoff

    @staticmethod
    def _validate(
        envelope: IdentityEnvelope,
        evidence: AuditEvidenceLink,
        *,
        now: datetime,
    ) -> None:
        if envelope.lease_until.tzinfo is None or envelope.lease_until.utcoffset() is None:
            raise ValueError("full timezone-aware lease_until is required")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("adapter clock must be timezone-aware")
        if envelope.lease_until <= now.astimezone(UTC):
            raise ValueError("lease_until must be in the future")
        if envelope.envelope_digest != envelope.compute_digest():
            raise ValueError("envelope digest does not match canonical identity")
        if envelope.promotion_partition is None:
            raise ValueError("promotion_partition is required")
        if evidence.envelope_digest != envelope.envelope_digest:
            raise ValueError("evidence envelope digest does not match identity")
        if evidence.correlation_id != envelope.correlation_id:
            raise ValueError("evidence correlation_id does not match identity")
        if evidence.trace_id != envelope.trace_id:
            raise ValueError("evidence trace_id does not match identity")
        if evidence.parent_evidence_digest == envelope.envelope_digest:
            raise ValueError("parent evidence must be distinct from envelope digest")

    @staticmethod
    def propagation_headers(handoff: IdentityHandoff) -> dict[str, str]:
        """Expose safe headers only, explicitly excluding lease credentials."""
        return dict(handoff.propagation_headers())


__all__ = ["IdentityHandoffAdapter"]
