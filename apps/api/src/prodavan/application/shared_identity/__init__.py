"""Canonical shared identity envelope for migration M04-M06 boundaries."""

from .contracts import (
    DIGEST_PREFIX,
    IdentityContext,
    IdentityEnvelope,
    IdentityEnvelopeError,
    PromotionPartition,
    QuarantineClassification,
    QuarantineEvidence,
)
from .ports import (
    IdentityContextPort,
    IdentityEnvelopeResolverPort,
    IdentityPropagationPort,
    IdentityQuarantinePort,
)

__all__ = [
    "DIGEST_PREFIX",
    "IdentityContext",
    "IdentityContextPort",
    "IdentityEnvelope",
    "IdentityEnvelopeError",
    "IdentityEnvelopeResolverPort",
    "IdentityPropagationPort",
    "IdentityQuarantinePort",
    "PromotionPartition",
    "QuarantineClassification",
    "QuarantineEvidence",
]
