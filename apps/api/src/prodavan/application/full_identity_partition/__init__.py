"""Exclusive full identity partition resolution zone."""

from .contracts import FullIdentityPartitionDecision, FullIdentityPartitionError
from .ports import FullIdentityPartitionInputPort, FullIdentityPartitionResolverPort
from .service import FullIdentityPartitionResolver, canonical_full_identity_promotion_digest

__all__ = [
    "FullIdentityPartitionDecision",
    "FullIdentityPartitionError",
    "FullIdentityPartitionInputPort",
    "FullIdentityPartitionResolver",
    "FullIdentityPartitionResolverPort",
    "canonical_full_identity_promotion_digest",
]
