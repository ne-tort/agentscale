"""Ports the durable apply path depends on.

Three narrow boundaries keep this package free of Pod/k8s and of the runtime
bridge:

* ``DurableApplyGate`` — trusted, read-only pre-flight. It answers "may this plan
  run at all right now?" with redacted reason codes. Any blocker means the plan is
  refused *before* a generation is claimed, so a blocked state can never consume a
  generation or grant authority.
* ``ResourceOperationEffectPort`` (see ``effects``) — the actual workspace effects.
* ``DurableClockPort`` — trusted time for lease expiry at apply time.

Nothing here may be satisfied by an in-memory stub in a production path: the
executor treats a missing port as ``BLOCKED``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Protocol

from prodavan.application.migration.runtime_materialization.state_machine import (
    DependencyEvidence,
)

from .contracts import ApplyPlan

_SHA256_PREFIX = "sha256:"
_REQUIRED_LINK_FIELDS = ("agent_run_id", "approval_id", "event_id")


class DurableClockPort(Protocol):
    async def now(self) -> datetime: ...


class SystemUtcClock:
    """Default trusted clock; overridable so acceptance can pin time."""

    async def now(self) -> datetime:
        return datetime.now(UTC)


class DurableApplyGate(Protocol):
    """Read-only pre-flight authority check for one plan."""

    async def blockers(self, *, plan: ApplyPlan, at: datetime) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class LinkAndLeaseGate:
    """Evidence gate over bridge lease, image provenance and approval/event links.

    ``evidence`` resolves the trusted dependency evidence of a project at apply
    time. The gate re-checks everything the state machine considers mandatory for
    authority, but without a ``DesiredRevision`` (this path materializes a resource
    set, not a runtime spec):

    * upstream contracts must be ready;
    * an opaque, non-revoked lease reference of generation >= 1 must exist and be
      unexpired at the supplied time;
    * the immutable image digest must be a sha256 value, and, when
      ``expected_image_digest`` is supplied, must match it exactly;
    * agent-run, approval and event links must be present;
    * at least one projected secret reference must be present, and when
      ``required_secret_refs`` is supplied it must be covered.

    Every failure is a redacted code; a provider error fails closed.
    """

    evidence: Callable[[str], "DependencyEvidence | None"]
    expected_image_digest: Callable[[str], str | None] | None = None
    required_secret_refs: Callable[[str], tuple[str, ...]] | None = None
    allow_projected_secret_refs_empty: bool = False

    async def blockers(self, *, plan: ApplyPlan, at: datetime) -> tuple[str, ...]:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("apply time must be timezone-aware")
        try:
            evidence = self.evidence(plan.project_id)
        except Exception:
            return ("trusted_evidence_port_error",)
        if evidence is None:
            return ("runtime_dependency_evidence_missing",)
        if not isinstance(evidence, DependencyEvidence):
            return ("runtime_dependency_evidence_invalid",)
        out: list[str] = []
        if not evidence.contracts_ready:
            out.append("upstream_resource_contract_missing")
        if not evidence.bridge_lease_ref or not evidence.bridge_lease_ref.startswith(
            ("lease://", "bridge-lease://")
        ):
            out.append("bridge_lease_missing")
        if evidence.bridge_lease_generation < 1:
            out.append("bridge_lease_generation_missing")
        if evidence.bridge_lease_expires_at is None:
            out.append("bridge_lease_expiry_missing")
        elif evidence.bridge_lease_expires_at <= at:
            out.append("bridge_lease_expired")
        if evidence.revoked:
            out.append("bridge_lease_revoked")
        image = evidence.image_digest
        if not image or not image.startswith(_SHA256_PREFIX):
            out.append("immutable_image_digest_missing")
        else:
            try:
                expected = self.expected_image_digest(plan.project_id) if self.expected_image_digest else None
            except Exception:
                return ("trusted_evidence_port_error",)
            if expected is not None and expected != image:
                out.append("image_digest_evidence_mismatch")
        for field in _REQUIRED_LINK_FIELDS:
            if not getattr(evidence, field):
                out.append(f"{field.replace('_id', '')}_link_missing")
        if not self.allow_projected_secret_refs_empty and not evidence.projected_secret_refs:
            out.append("projected_secret_reference_missing")
        if self.required_secret_refs is not None:
            try:
                required = set(self.required_secret_refs(plan.project_id))
            except Exception:
                return ("trusted_evidence_port_error",)
            if not required.issubset(set(evidence.projected_secret_refs)):
                out.append("projected_secret_reference_missing")
        return tuple(dict.fromkeys(out))


def _unused_placeholder() -> None:  # pragma: no cover - intentionally empty
    raise AssertionError("no default gate is provided; a caller must inject evidence")


__all__ = [
    "DurableApplyGate",
    "DurableClockPort",
    "LinkAndLeaseGate",
    "SystemUtcClock",
]
