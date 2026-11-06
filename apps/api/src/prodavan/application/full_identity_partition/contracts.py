"""Contracts for the complete identity-bound promotion partition.

This zone is deliberately independent from persistence, transport, and the
existing partition-digest zone.  A partition decision is evidence only; it is
not an authorization or write decision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from prodavan.application.shared_identity import (
    IdentityEnvelope,
    PromotionPartition,
)


def canonical_full_identity_payload(
    envelope: IdentityEnvelope, partition: PromotionPartition
) -> dict[str, Any]:
    """Build the digest-bound payload without transport metadata or secrets."""
    return {
        "definition_id": envelope.definition_id,
        "version_id": envelope.version_id,
        "artifact_digest": envelope.artifact_digest,
        "tenant_id": envelope.tenant_id,
        "cabinet_id": envelope.cabinet_id,
        "project_id": envelope.project_id,
        "scope_kind": envelope.scope_kind,
        "scope_id": envelope.scope_id,
        "plugin_instance_id": envelope.plugin_instance_id,
        "subject_id": envelope.subject_id,
        "session_id": envelope.session_id,
        "runtime_generation": envelope.runtime_generation,
        "writer_generation": envelope.writer_generation,
        "lease_snapshot": {
            "lease_id": envelope.lease_id,
            "lease_owner": envelope.lease_owner,
            "lease_until": envelope.lease_until.isoformat(),
        },
        "correlation_id": envelope.correlation_id,
        "trace_id": envelope.trace_id,
        "promotion_partition": {
            "tenant_id": partition.tenant_id,
            "definition_id": partition.definition_id,
            "version_id": partition.version_id,
            "scope_kind": partition.scope_kind,
            "scope_id": partition.scope_id,
            "plugin_instance_id": partition.plugin_instance_id,
            "subject_id": partition.subject_id,
            "session_id": partition.session_id,
        },
    }


def canonical_full_identity_hash(
    envelope: IdentityEnvelope, partition: PromotionPartition
) -> str:
    """Hash the canonical full identity payload."""
    encoded = json.dumps(
        canonical_full_identity_payload(envelope, partition),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class FullIdentityPartitionError(ValueError):
    """Raised when complete identity evidence cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class FullIdentityPartitionDecision:
    """Canonical digest linked to one validated identity snapshot."""

    envelope: IdentityEnvelope
    partition: PromotionPartition
    digest: str
    supplied_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, IdentityEnvelope):
            raise FullIdentityPartitionError("envelope must be a validated IdentityEnvelope")
        if not isinstance(self.partition, PromotionPartition):
            raise FullIdentityPartitionError("partition must be shared_identity.PromotionPartition")
        if self.envelope.envelope_digest != self.envelope.compute_digest():
            raise FullIdentityPartitionError("envelope digest does not match canonical identity")
        if self.envelope.promotion_partition != self.partition:
            raise FullIdentityPartitionError(
                "promotion partition does not match complete identity envelope"
            )
        _validate_digest(self.digest, "digest")
        if self.digest != canonical_full_identity_hash(self.envelope, self.partition):
            raise FullIdentityPartitionError(
                "digest does not match canonical full identity payload"
            )
        if self.supplied_digest is not None:
            _validate_digest(self.supplied_digest, "supplied_digest")
            if self.supplied_digest != self.digest:
                raise FullIdentityPartitionError(
                    "supplied digest conflicts with complete identity partition"
                )

    @property
    def accepted(self) -> bool:
        return True

    def canonical_payload(self) -> dict[str, Any]:
        """Return a copy of the digest evidence, without headers or secrets."""
        return canonical_full_identity_payload(self.envelope, self.partition)


def _validate_digest(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(char not in "0123456789abcdef" for char in value[7:])
    ):
        raise FullIdentityPartitionError(f"{name} must be a lowercase sha256: digest")
    return value


__all__ = [
    "FullIdentityPartitionDecision",
    "FullIdentityPartitionError",
    "canonical_full_identity_hash",
    "canonical_full_identity_payload",
]
