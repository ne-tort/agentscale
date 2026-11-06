"""Immutable bridge contracts for shared identity handoff.

The adapter owns no persistence and does not broaden the identity represented by
``IdentityEnvelope``.  Every downstream handoff keeps the same envelope digest,
lease fencing fields, promotion partition, and propagation identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from prodavan.application.m06_evidence_transaction import AuditOutcomeEvidenceV2
from prodavan.application.plugin_installation_context import (
    M05CasContext,
    M06GrantHandoff,
    PluginInstallationContext,
)
from prodavan.application.shared_identity import IdentityContext, IdentityEnvelope


@dataclass(frozen=True, slots=True)
class AuditEvidenceLink:
    """Digest-only linkage from a handoff to its parent evidence record."""

    envelope_digest: str
    evidence_digest: str
    parent_evidence_digest: str
    correlation_id: str
    trace_id: str
    promotion_partition_digest: str

    def __post_init__(self) -> None:
        for name in (
            "envelope_digest", "evidence_digest", "parent_evidence_digest",
            "promotion_partition_digest",
        ):
            _digest(getattr(self, name), name)
        if self.parent_evidence_digest == self.evidence_digest:
            raise ValueError("parent evidence digest must be distinct from evidence digest")
        for name in ("correlation_id", "trace_id"):
            _required(getattr(self, name), name)

    @classmethod
    def from_evidence(
        cls,
        evidence: AuditOutcomeEvidenceV2,
        *,
        parent_evidence_digest: str,
        promotion_partition_digest: str,
    ) -> "AuditEvidenceLink":
        """Build a redacted linkage and require the evidence to be complete."""
        identity = evidence.identity
        if evidence.identity_digest != identity.envelope_digest:
            raise ValueError("evidence identity digest does not match envelope")
        if evidence.promotion.lineage_reference != identity.correlation_id:
            raise ValueError("evidence parent lineage does not match correlation")
        return cls(
            envelope_digest=identity.envelope_digest,
            evidence_digest=evidence.bundle.bundle_digest,
            parent_evidence_digest=parent_evidence_digest,
            correlation_id=identity.correlation_id,
            trace_id=identity.trace_id,
            promotion_partition_digest=promotion_partition_digest,
        )


@dataclass(frozen=True, slots=True)
class IdentityHandoff:
    """One exact identity snapshot and all downstream handoff representations."""

    envelope: IdentityEnvelope
    context: IdentityContext
    installation: PluginInstallationContext
    m05: M05CasContext
    m06: M06GrantHandoff
    evidence: AuditEvidenceLink

    def __post_init__(self) -> None:
        if self.context.envelope is not self.envelope:
            raise ValueError("propagation context must preserve the exact envelope")
        if self.m05.installation is not self.installation:
            raise ValueError("M05 must receive the exact installation context")
        if self.m06.installation is not self.installation:
            raise ValueError("M06 must receive the exact installation context")
        _assert_installation_matches(self.envelope, self.installation)
        if self.evidence.envelope_digest != self.envelope.envelope_digest:
            raise ValueError("evidence envelope digest does not match identity")
        if self.evidence.correlation_id != self.envelope.correlation_id:
            raise ValueError("evidence correlation does not match identity")
        if self.evidence.trace_id != self.envelope.trace_id:
            raise ValueError("evidence trace does not match identity")

    @property
    def lease_until(self) -> datetime:
        return self.envelope.lease_until

    def propagation_headers(self) -> Mapping[str, str]:
        """Return only safe IDs/digests; lease credentials never cross transport."""
        return self.context.propagation_headers()


def _assert_installation_matches(
    envelope: IdentityEnvelope, installation: PluginInstallationContext
) -> None:
    fields = (
        "tenant_id", "cabinet_id", "project_id", "scope_kind", "scope_id",
        "plugin_instance_id", "definition_id", "version_id", "artifact_digest",
        "subject_id", "session_id", "runtime_generation", "writer_generation",
        "lease_owner", "lease_id",
    )
    for field in fields:
        if getattr(envelope, field) != getattr(installation, field):
            raise ValueError(f"installation {field} does not match identity envelope")


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _digest(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
        or any(char not in "0123456789abcdef" for char in value[7:])
    ):
        raise ValueError(f"{name} must be a sha256: digest")


__all__ = ["AuditEvidenceLink", "IdentityHandoff"]
