"""Exclusive promotion partition identity digest zone."""

from .contracts import (
    CANONICAL_FIELDS,
    DIGEST_PREFIX,
    FORBIDDEN_FIELDS,
    PromotionPartition,
    PromotionPartitionDecision,
    PromotionPartitionError,
    ScopeKind,
    validate_digest,
)
from .service import (
    PromotionPartitionDigestService,
    canonical_promotion_partition_digest,
    decide_promotion_partition,
)

__all__ = [
    "CANONICAL_FIELDS", "DIGEST_PREFIX", "FORBIDDEN_FIELDS", "PromotionPartition",
    "PromotionPartitionDecision", "PromotionPartitionDigestService",
    "PromotionPartitionError", "ScopeKind", "canonical_promotion_partition_digest",
    "decide_promotion_partition", "validate_digest",
]
