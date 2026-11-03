"""Port for the actual resource effects a durable apply performs.

The ledger owns generations, receipts and audit; it never writes a Pod workspace
itself. An adapter (workspace/Pod writer) implements this port and must respect the
supplied fence: a receipt that does not echo the exact fence token and generation
is treated as an unknown outcome and fails closed.

No in-memory implementation lives here. A missing effect port makes the executor
return ``BLOCKED``; it never degrades to a no-op success.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from prodavan.application.materialization_adapter import ResourceOperation
from prodavan.application.migration.runtime_materialization.state_machine import (
    canonical_digest,
)


def resource_operation_digest(operation: ResourceOperation) -> str:
    """Stable per-operation digest used to match an effect receipt."""
    provenance = operation.provenance
    return canonical_digest(
        {
            "kind": operation.kind.value,
            "target": operation.target,
            "payload": operation.payload,
            "required": operation.required,
            "provenance": {
                "source_type": provenance.source_type,
                "source_id": provenance.source_id,
                "source_generation": provenance.source_generation,
                "source_path": provenance.source_path,
                "source_revision": provenance.source_revision,
                "metadata": provenance.metadata,
            }
            if provenance is not None
            else None,
        }
    )


@dataclass(frozen=True, slots=True)
class OperationFence:
    """The exact generation claim an effect is allowed to act under."""

    resource_key: str
    generation: int
    fence_token: str

    def __post_init__(self) -> None:
        if not self.resource_key.strip() or not self.fence_token.strip():
            raise ValueError("fence identity is required")
        if self.generation < 1:
            raise ValueError("fence generation must be positive")


@dataclass(frozen=True, slots=True)
class OperationEffectReceipt:
    """What an effect adapter observed; the ledger verifies every field."""

    operation_digest: str
    fence_token: str
    fence_generation: int
    succeeded: bool
    reason_code: str = "applied"
    effect_unknown: bool = False

    def __post_init__(self) -> None:
        if not self.operation_digest.strip() or not self.fence_token.strip():
            raise ValueError("receipt identity is required")
        if self.fence_generation < 1:
            raise ValueError("receipt fence generation must be positive")
        if not self.reason_code.strip():
            raise ValueError("receipt reason code is required")

    def matches(self, *, operation_digest: str, fence: OperationFence) -> bool:
        return (
            self.operation_digest == operation_digest
            and self.fence_token == fence.fence_token
            and self.fence_generation == fence.generation
            and self.fence_token.strip() != ""
        )


class ResourceOperationEffectPort(Protocol):
    """Trusted effect boundary; implementations must verify the live runtime."""

    async def apply_operation(
        self, *, project_id: str, operation: ResourceOperation, fence: OperationFence
    ) -> OperationEffectReceipt: ...

    async def compensate_operation(
        self, *, project_id: str, operation: ResourceOperation, fence: OperationFence
    ) -> OperationEffectReceipt: ...


class RollbackEffectPort(Protocol):
    """Optional capability: restore a previously applied generation."""

    async def apply_rollback(
        self,
        *,
        project_id: str,
        target_generation: int,
        target_revision_id: str,
        fence: OperationFence,
    ) -> OperationEffectReceipt: ...


__all__ = [
    "OperationEffectReceipt",
    "OperationFence",
    "ResourceOperationEffectPort",
    "RollbackEffectPort",
    "resource_operation_digest",
]
