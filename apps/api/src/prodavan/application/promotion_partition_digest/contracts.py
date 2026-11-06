"""Exclusive canonical contracts for promotion partition identity digests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


DIGEST_PREFIX = "sha256:"
DIGEST_LENGTH = len(DIGEST_PREFIX) + 64
CANONICAL_FIELDS: tuple[str, ...] = (
    "tenant_id",
    "scope_kind",
    "scope_id",
    "plugin_id",
    "supplier_id",
    "project_id",
    "owner_id",
)
# These names are deliberately not accepted as aliases or ignored metadata.
FORBIDDEN_FIELDS = frozenset({
    "lease_id", "lease_owner", "lease_until", "secret", "secrets",
    "token", "access_token", "refresh_token", "password", "credential",
    "credentials",
})


class PromotionPartitionError(ValueError):
    """Raised when a partition or digest input is unsafe or ambiguous."""


class ScopeKind(StrEnum):
    TENANT = "tenant"
    CABINET = "cabinet"
    PROJECT = "project"
    PLUGIN_INSTANCE = "plugin_instance"


@dataclass(frozen=True, slots=True)
class PromotionPartition:
    """The complete, secret-free identity boundary for a promotion decision."""

    tenant_id: str
    scope_kind: ScopeKind | str
    scope_id: str
    plugin_id: str
    supplier_id: str
    project_id: str
    owner_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope_kind, ScopeKind):
            try:
                object.__setattr__(self, "scope_kind", ScopeKind(self.scope_kind))
            except (TypeError, ValueError) as exc:
                raise PromotionPartitionError("unsupported scope_kind") from exc
        for name in CANONICAL_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise PromotionPartitionError(f"{name} must be a non-empty string")
        if self.scope_kind is ScopeKind.TENANT and self.scope_id != self.tenant_id:
            raise PromotionPartitionError("tenant scope must equal tenant_id")
        if self.scope_kind is ScopeKind.PROJECT and self.scope_id != self.project_id:
            raise PromotionPartitionError("project scope must equal project_id")
        if self.scope_kind is ScopeKind.PLUGIN_INSTANCE and self.scope_id != self.plugin_id:
            raise PromotionPartitionError("plugin_instance scope must equal plugin_id")

    def canonical_payload(self) -> dict[str, str]:
        """Return fields in the specified order, with no operational metadata."""
        return {
            "tenant_id": self.tenant_id,
            "scope_kind": self.scope_kind.value,
            "scope_id": self.scope_id,
            "plugin_id": self.plugin_id,
            "supplier_id": self.supplier_id,
            "project_id": self.project_id,
            "owner_id": self.owner_id,
        }


@dataclass(frozen=True, slots=True)
class PromotionPartitionDecision:
    """Read/decision-only result suitable for evidence and handoff adapters."""

    partition: PromotionPartition
    digest: str
    supplied_digest: str | None = None

    def __post_init__(self) -> None:
        validate_digest(self.digest)
        if self.supplied_digest is not None:
            validate_digest(self.supplied_digest, field="supplied_digest")
            if self.supplied_digest != self.digest:
                raise PromotionPartitionError("supplied digest conflicts with canonical partition")

    @property
    def accepted(self) -> bool:
        return True


def validate_digest(value: Any, *, field: str = "digest") -> str:
    if (
        not isinstance(value, str)
        or len(value) != DIGEST_LENGTH
        or not value.startswith(DIGEST_PREFIX)
        or any(char not in "0123456789abcdef" for char in value[len(DIGEST_PREFIX):])
    ):
        raise PromotionPartitionError(f"{field} must be a lowercase sha256: digest")
    return value


def validate_mapping_keys(values: Mapping[str, Any]) -> None:
    if not isinstance(values, Mapping):
        raise PromotionPartitionError("partition input must be a mapping")
    forbidden = set(values) & FORBIDDEN_FIELDS
    if forbidden:
        names = ", ".join(sorted(str(item) for item in forbidden))
        raise PromotionPartitionError(f"forbidden partition fields: {names}")
    unknown = set(values) - set(CANONICAL_FIELDS) - {"digest"}
    if unknown:
        names = ", ".join(sorted(str(item) for item in unknown))
        raise PromotionPartitionError(f"unsupported partition fields: {names}")


__all__ = [
    "CANONICAL_FIELDS", "DIGEST_PREFIX", "FORBIDDEN_FIELDS", "PromotionPartition",
    "PromotionPartitionDecision", "PromotionPartitionError", "ScopeKind", "validate_digest",
]
