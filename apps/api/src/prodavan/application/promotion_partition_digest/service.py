"""Pure canonical digest and decision helpers for promotion handoffs."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .contracts import (
    CANONICAL_FIELDS,
    DIGEST_PREFIX,
    PromotionPartition,
    PromotionPartitionDecision,
    PromotionPartitionError,
    validate_digest,
    validate_mapping_keys,
)


class PromotionPartitionDigestService:
    """Read-only helpers; this service has no persistence or write authority."""

    @staticmethod
    def canonical_json(partition: PromotionPartition) -> str:
        if not isinstance(partition, PromotionPartition):
            raise PromotionPartitionError("partition must be a PromotionPartition")
        # Explicit field insertion order and compact JSON make the byte input stable.
        return json.dumps(
            partition.canonical_payload(),
            ensure_ascii=True,
            separators=(",", ":"),
        )

    @classmethod
    def digest(cls, partition: PromotionPartition) -> str:
        encoded = cls.canonical_json(partition).encode("utf-8")
        return DIGEST_PREFIX + hashlib.sha256(encoded).hexdigest()

    @classmethod
    def decide(
        cls,
        partition: PromotionPartition,
        *,
        supplied_digest: str | None = None,
    ) -> PromotionPartitionDecision:
        expected = cls.digest(partition)
        if supplied_digest is not None:
            validate_digest(supplied_digest, field="supplied_digest")
            if supplied_digest != expected:
                raise PromotionPartitionError("supplied digest conflicts with canonical partition")
        return PromotionPartitionDecision(
            partition=partition,
            digest=expected,
            supplied_digest=supplied_digest,
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> PromotionPartitionDecision:
        """Parse and verify a transport/evidence mapping without accepting extras."""
        validate_mapping_keys(values)
        missing = [field for field in CANONICAL_FIELDS if field not in values]
        if missing:
            raise PromotionPartitionError(
                "missing required partition fields: " + ", ".join(missing)
            )
        has_supplied_digest = "digest" in values
        supplied = values.get("digest")
        if has_supplied_digest:
            validate_digest(supplied, field="digest")
        partition_values = {field: values[field] for field in CANONICAL_FIELDS}
        partition = PromotionPartition(**partition_values)
        return cls.decide(partition, supplied_digest=supplied)

    @classmethod
    def verify(cls, partition: PromotionPartition, supplied_digest: str) -> bool:
        """Return a decision boolean while retaining fail-closed validation."""
        return cls.decide(partition, supplied_digest=supplied_digest).accepted


def canonical_promotion_partition_digest(partition: PromotionPartition) -> str:
    return PromotionPartitionDigestService.digest(partition)


def decide_promotion_partition(
    values: PromotionPartition | Mapping[str, Any],
    *,
    supplied_digest: str | None = None,
) -> PromotionPartitionDecision:
    if isinstance(values, PromotionPartition):
        return PromotionPartitionDigestService.decide(values, supplied_digest=supplied_digest)
    if supplied_digest is not None:
        if "digest" in values and values["digest"] != supplied_digest:
            raise PromotionPartitionError("conflicting supplied digests")
        values = dict(values)
        values["digest"] = supplied_digest
    return PromotionPartitionDigestService.from_mapping(values)


__all__ = [
    "PromotionPartitionDigestService", "canonical_promotion_partition_digest",
    "decide_promotion_partition",
]
