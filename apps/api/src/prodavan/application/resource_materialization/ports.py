"""Ports used by the resource selection/materialization resolver."""

from __future__ import annotations

from typing import Protocol

from prodavan.application.materialization_adapter import ResourceOperation
from .contracts import MaterializationApplyOutcome

from prodavan.application.resources import (
    ContentResource,
    ResourceBinding,
    ResourceScope,
    ResourceVersion,
)


class AtomicMaterializationApplyPort(Protocol):
    """Trusted persistence boundary: validate all predicates before side effects."""

    async def apply_cas(
        self,
        *,
        project_id: str,
        expected_generation: int,
        snapshot_digest: str,
        operation_set_digest: str,
        operations: tuple[ResourceOperation, ...],
    ) -> MaterializationApplyOutcome: ...


class ResourceMaterializationSourcePort(Protocol):
    async def get_resource(self, resource_id: str) -> ContentResource | None: ...

    async def get_version(self, resource_id: str, number: int) -> ResourceVersion | None: ...

    async def list_bindings(self, target: ResourceScope) -> list[ResourceBinding]: ...


__all__ = ["ResourceMaterializationSourcePort"]
