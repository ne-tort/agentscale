"""Pure resolver for complete identity-bound promotion digests."""

from __future__ import annotations

import json
from typing import Mapping

from prodavan.application.shared_identity import IdentityEnvelope, PromotionPartition

from .contracts import (
    FullIdentityPartitionDecision,
    FullIdentityPartitionError,
    canonical_full_identity_hash,
    canonical_full_identity_payload,
)


class FullIdentityPartitionResolver:
    """Verify exact shared identity and compute a stable full-snapshot digest."""

    @staticmethod
    def canonical_json(
        envelope: IdentityEnvelope, partition: PromotionPartition
    ) -> str:
        _validate_envelope(envelope)
        _validate_exact_partition(envelope, partition)
        return json.dumps(
            canonical_full_identity_payload(envelope, partition),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def digest(
        cls, envelope: IdentityEnvelope, partition: PromotionPartition
    ) -> str:
        cls.canonical_json(envelope, partition)
        return canonical_full_identity_hash(envelope, partition)

    @classmethod
    def resolve(
        cls,
        *,
        envelope: IdentityEnvelope,
        partition: PromotionPartition,
        supplied_digest: str | None = None,
    ) -> FullIdentityPartitionDecision:
        _validate_envelope(envelope)
        _validate_exact_partition(envelope, partition)
        expected = cls.digest(envelope, partition)
        if supplied_digest is not None:
            _validate_supplied_digest(supplied_digest)
            if supplied_digest != expected:
                raise FullIdentityPartitionError(
                    "supplied digest conflicts with complete identity partition"
                )
        return FullIdentityPartitionDecision(
            envelope=envelope,
            partition=partition,
            digest=expected,
            supplied_digest=supplied_digest,
        )

    @classmethod
    def resolve_mapping(
        cls,
        *,
        envelope: IdentityEnvelope,
        partition: Mapping[str, object],
        supplied_digest: str | None = None,
    ) -> FullIdentityPartitionDecision:
        """Reject partial/legacy plugin or supplier-shaped inputs explicitly."""
        if not isinstance(partition, Mapping):
            raise FullIdentityPartitionError("partition must be a mapping")
        forbidden_partial = {"plugin_id", "supplier_id", "owner_id"}
        if forbidden_partial.intersection(partition):
            raise FullIdentityPartitionError(
                "plugin_id-only/supplier-only partition shapes are not accepted"
            )
        required = {
            "tenant_id", "definition_id", "version_id", "scope_kind", "scope_id",
            "plugin_instance_id", "subject_id", "session_id",
        }
        missing = sorted(required - set(partition))
        if missing:
            raise FullIdentityPartitionError(
                "incomplete promotion partition; missing: " + ", ".join(missing)
            )
        unknown = set(partition) - required
        if unknown:
            raise FullIdentityPartitionError(
                "unsupported partition fields: " + ", ".join(sorted(unknown))
            )
        try:
            typed = PromotionPartition(**{key: partition[key] for key in required})
        except (TypeError, ValueError) as exc:
            raise FullIdentityPartitionError("invalid complete promotion partition") from exc
        return cls.resolve(
            envelope=envelope,
            partition=typed,
            supplied_digest=supplied_digest,
        )



def _validate_envelope(envelope: IdentityEnvelope) -> None:
    if not isinstance(envelope, IdentityEnvelope):
        raise FullIdentityPartitionError("envelope must be a validated IdentityEnvelope")
    if envelope.envelope_digest != envelope.compute_digest():
        raise FullIdentityPartitionError("envelope digest does not match canonical identity")
    if envelope.promotion_partition is None:
        raise FullIdentityPartitionError("envelope promotion partition is required")


def _validate_exact_partition(
    envelope: IdentityEnvelope, partition: PromotionPartition
) -> None:
    if not isinstance(partition, PromotionPartition):
        raise FullIdentityPartitionError("partition must be shared_identity.PromotionPartition")
    expected = envelope.promotion_partition
    if partition != expected:
        raise FullIdentityPartitionError(
            "promotion partition does not match complete identity envelope"
        )


def _validate_supplied_digest(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(char not in "0123456789abcdef" for char in value[7:])
    ):
        raise FullIdentityPartitionError(
            "supplied_digest must be a lowercase sha256: digest"
        )


def canonical_full_identity_promotion_digest(
    envelope: IdentityEnvelope, partition: PromotionPartition
) -> str:
    return FullIdentityPartitionResolver.digest(envelope, partition)


__all__ = [
    "FullIdentityPartitionResolver",
    "canonical_full_identity_promotion_digest",
]
