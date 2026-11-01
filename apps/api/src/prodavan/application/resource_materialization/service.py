"""Fail-closed selection and operation planning for prompt/file/MCP resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
from prodavan.application.resources import BindingKind, ResourceKind, VersionStatus
from prodavan.domain.errors import AppError
from prodavan.domain.materialization import OperationKind, SourceProvenance

from .contracts import (
    MaterializationApplyOutcome,
    ResourceMaterializationContext,
    ResourceMaterializationResolution,
    ResourceSelection,
    ResolutionFailure,
)
from .ports import AtomicMaterializationApplyPort, ResourceMaterializationSourcePort
from prodavan.application.materialization_adapter import ResourceOperation


_KIND_MAP = {
    ResourceKind.PROMPT: OperationKind.PROMPT,
    ResourceKind.FILE: OperationKind.FILE,
    ResourceKind.MCP: OperationKind.MCP,
}


@dataclass(slots=True)
class ResourceMaterializationResolver:
    source: ResourceMaterializationSourcePort

    async def resolve(self, context: ResourceMaterializationContext) -> ResourceMaterializationResolution:
        failures: list[ResolutionFailure] = []
        operations: list[ResourceOperation] = []
        provenance_items: list[dict[str, Any]] = []

        # Resolve in canonical identity order so the request and provenance are reproducible.
        selections = tuple(
            sorted(
                context.selections,
                key=lambda selection: (
                    selection.resource_id,
                    selection.binding_id,
                    selection.version_number,
                    selection.target or "",
                ),
            )
        )
        for selection in selections:
            resource = await self.source.get_resource(selection.resource_id)
            binding_candidates = [
                binding
                for binding in await self.source.list_bindings(context.target_scope)
                if binding.id == selection.binding_id and binding.resource_id == selection.resource_id
            ]
            if resource is None:
                failures.append(ResolutionFailure("resource_missing", "resource does not exist", selection.resource_id, selection.required))
                continue
            if resource.scope != selection.scope:
                failures.append(ResolutionFailure("resource_scope_mismatch", "resource owner scope differs from explicit selection", resource.id, selection.required))
                continue
            if len(binding_candidates) != 1:
                code = "binding_missing" if not binding_candidates else "binding_ambiguous"
                failures.append(ResolutionFailure(code, "explicit resource binding was not uniquely resolved", resource.id, selection.required))
                continue
            binding = binding_candidates[0]
            if binding.target != context.target_scope:
                failures.append(ResolutionFailure("binding_scope_mismatch", "binding target differs from materialization scope", resource.id, selection.required))
                continue
            if binding.binding not in (BindingKind.LOCAL, BindingKind.GLOBAL):
                failures.append(ResolutionFailure("binding_invalid", "unsupported binding kind", resource.id, selection.required))
                continue
            if binding.version_number != selection.version_number:
                failures.append(ResolutionFailure("binding_stale", "binding version differs from explicit selection", resource.id, selection.required))
                continue
            version = await self.source.get_version(resource.id, selection.version_number)
            if version is None:
                failures.append(ResolutionFailure("version_missing", "selected resource version does not exist", resource.id, selection.required))
                continue
            if version.resource_id != resource.id or version.number != selection.version_number:
                failures.append(ResolutionFailure("version_mismatch", "version identity does not match selection", resource.id, selection.required))
                continue
            if version.status is not VersionStatus.PUBLISHED:
                failures.append(ResolutionFailure("version_not_published", "only published resource versions can materialize", resource.id, selection.required))
                continue
            if resource.active_version != version.number:
                failures.append(ResolutionFailure("resource_stale", "resource active version changed or is unset since selection", resource.id, selection.required))
                continue
            operation_kind = _KIND_MAP.get(resource.kind)
            if operation_kind is None:
                failures.append(ResolutionFailure("resource_kind_unsupported", "resource kind cannot be materialized", resource.id, selection.required))
                continue
            try:
                target = normalize_workspace_path(selection.target or _target(resource.kind, resource, version))
            except AppError:
                failures.append(ResolutionFailure("target_invalid", "resource target is not a safe workspace path", resource.id, selection.required))
                continue
            if not target:
                failures.append(ResolutionFailure("target_invalid", "resource target must not be empty", resource.id, selection.required))
                continue
            provenance = SourceProvenance(
                source_type=f"resource:{resource.kind.value}",
                source_id=resource.id,
                source_generation=context.desired_generation,
                source_path=target,
                source_revision=version.id,
                metadata={"binding_id": binding.id, "version_number": version.number, "scope_kind": resource.scope.kind.value, "scope_id": resource.scope.id},
            )
            operations.append(ResourceOperation(kind=operation_kind, target=target, payload=version.payload, provenance=provenance, required=selection.required))
            provenance_items.append({"resource_id": resource.id, "version": version.number, "binding_id": binding.id})

        # Any failure, including optional selection failure, invalidates the complete set.
        # A downstream caller must never interpret a partial selection as ready.
        if failures:
            operations = []
        source = SourceProvenance(
            source_type="resource-resolution",
            source_id=context.revision_id,
            source_generation=context.desired_generation,
            source_revision=context.revision_id,
            metadata={"resources": provenance_items},
        ) if operations else None
        return ResourceMaterializationResolution(
            context=context,
            operations=tuple(operations),
            failures=tuple(failures),
            source=source,
            snapshot_digest="",
            snapshot_generation=context.desired_generation,
            diagnostics={"fail_closed": bool(failures), "resolved_count": len(operations), "failure_codes": tuple(f.code for f in failures), "ready": not failures},
        )


def _target(kind: ResourceKind, resource: Any, version: Any) -> str:
    metadata = resource.metadata
    if kind is ResourceKind.PROMPT:
        return str(metadata.get("path") or f"prompts/{resource.slug}.md")
    if kind is ResourceKind.FILE:
        return str(metadata.get("path") or f"files/{resource.slug}")
    return str(metadata.get("path") or f"mcp/{resource.slug}.json")


async def apply_resolution(
    resolution: ResourceMaterializationResolution,
    *,
    apply_port: AtomicMaterializationApplyPort | None = None,
) -> MaterializationApplyOutcome:
    """Apply only a complete snapshot through the trusted atomic CAS boundary."""
    if apply_port is None:
        return MaterializationApplyOutcome.BLOCKED
    if not resolution.is_complete:
        return MaterializationApplyOutcome.BLOCKED
    return await apply_port.apply_cas(
        project_id=resolution.context.project_id,
        expected_generation=resolution.context.desired_generation,
        snapshot_digest=resolution.snapshot_digest,
        operation_set_digest=resolution.operation_set_digest,
        operations=resolution.operations,
    )


__all__ = ["ResourceMaterializationResolver", "apply_resolution"]
