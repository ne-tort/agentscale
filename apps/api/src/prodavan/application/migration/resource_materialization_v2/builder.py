"""Pure plan orchestration over trusted read-only resource ports."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from prodavan.application.migration.resources import (
    BindingState,
    ResourceKind,
    ResourceLifecycle,
    ResourceSnapshot,
    ScopeKind,
    VersionStatus,
    canonical_digest,
)
from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
from prodavan.application.migration.resources.lifecycle_contracts import safe_workspace_path
from prodavan.application.migration.resources.lifecycle_validation import (
    asset_from_payload,
    validate_resource_payload,
)

from .contracts import (
    FileRefAclEvidence,
    PlanFailure,
    PlannedResourceOperation,
    ResourcePlanPorts,
    ResourcePlanRequest,
    ResourcePlanSelection,
    ResourceOperationReadiness,
    ResourceProjectPlan,
    UpstreamEvidence,
    _REQUIRED_GATES,
)


@dataclass(frozen=True, slots=True)
class _ResolvedSelection:
    selection: ResourcePlanSelection
    snapshot: ResourceSnapshot
    target_path: str
    acl: FileRefAclEvidence | None


def _failure(code: str, message: str, selection: ResourcePlanSelection | None = None) -> PlanFailure:
    return PlanFailure(code, message, selection.resource_id if selection else None, selection.required if selection else True)


def _snapshot_key(snapshot: ResourceSnapshot) -> tuple[str, str, str, str]:
    return (
        snapshot.resource.resource_id,
        snapshot.binding.binding_id,
        snapshot.version.version_id,
        snapshot.binding.profile_id or snapshot.resource.profile_id or "",
    )


def _selection_key(selection: ResourcePlanSelection) -> tuple[str, str, str, str]:
    return (selection.resource_id, selection.binding_id, selection.version_id, selection.profile_id or "")


def _selection_order_key(selection: ResourcePlanSelection) -> tuple[object, ...]:
    return (*_selection_key(selection), selection.target_path or "", selection.required)


def _snapshot_order_key(snapshot: ResourceSnapshot) -> tuple[object, ...]:
    return (
        *_snapshot_key(snapshot),
        snapshot.version.content_digest,
        snapshot.binding.generation,
        snapshot.scope_proof.proof_id,
    )


def _evidence_order_key(item: UpstreamEvidence) -> tuple[str, ...]:
    return (
        item.gate_id,
        item.evidence_id,
        item.evidence_digest,
        item.tenant_id,
        item.project_id,
        item.revision_id,
        str(item.generation),
        item.status,
    )


def _target_path(selection: ResourcePlanSelection, snapshot: ResourceSnapshot) -> str | None:
    explicit = selection.target_path
    if explicit:
        return explicit
    metadata_path = snapshot.resource.metadata.get("path")
    if isinstance(metadata_path, str) and metadata_path.strip():
        return metadata_path
    payload: Any = snapshot.version.payload
    candidate = payload.get("path", payload.get("target_path")) if hasattr(payload, "get") else None
    if isinstance(candidate, str) and candidate.strip():
        return candidate
    if snapshot.resource.kind is ResourceKind.FILE:
        asset = asset_from_payload(payload)
        return f"files/{asset.filename}" if asset is not None else None
    if snapshot.resource.kind is ResourceKind.PROMPT:
        return f"prompts/{snapshot.resource.resource_id}.md"
    if snapshot.resource.kind is ResourceKind.MCP:
        return f"mcp/{snapshot.resource.resource_id}.json"
    return None


def _validate_upstream(
    request: ResourcePlanRequest, evidence: tuple[UpstreamEvidence, ...]
) -> tuple[PlanFailure, ...]:
    failures: list[PlanFailure] = []
    by_gate: dict[str, list[UpstreamEvidence]] = defaultdict(list)
    for item in evidence:
        by_gate[item.gate_id].append(item)
    for gate_id in sorted(_REQUIRED_GATES):
        matches = by_gate.get(gate_id, [])
        if not matches:
            failures.append(_failure("upstream_evidence_missing", f"required evidence {gate_id} is missing"))
            continue
        if len(matches) != 1:
            failures.append(_failure("upstream_evidence_ambiguous", f"evidence {gate_id} is not unique"))
            continue
        item = matches[0]
        if (
            item.status != "verified"
            or item.tenant_id != request.tenant_id
            or item.project_id != request.project_id
            or item.revision_id != request.revision_id
            or item.generation != request.desired_generation
        ):
            failures.append(_failure("upstream_evidence_mismatch", f"evidence {gate_id} does not prove this project revision"))
    if any(item.gate_id not in _REQUIRED_GATES for item in evidence):
        failures.append(_failure("upstream_evidence_unrecognized", "unrecognized upstream evidence cannot authorize planning"))
    return tuple(failures)


def _snapshot_failures(
    request: ResourcePlanRequest,
    selections: tuple[ResourcePlanSelection, ...],
    snapshots: tuple[ResourceSnapshot, ...],
) -> tuple[PlanFailure, ...]:
    failures: list[PlanFailure] = []
    expected: dict[tuple[str, str, str, str], list[ResourcePlanSelection]] = defaultdict(list)
    for selection in selections:
        expected[_selection_key(selection)].append(selection)
    actual: dict[tuple[str, str, str, str], list[ResourceSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        actual[_snapshot_key(snapshot)].append(snapshot)

    for key, selected in expected.items():
        if len(selected) != 1:
            failures.append(_failure("selection_duplicate", "resource selection identity must be unique", selected[0]))
            continue
        found = actual.get(key, [])
        if not found:
            failures.append(_failure("snapshot_missing", "trusted port did not return the exact selected snapshot", selected[0]))
        elif len(found) != 1:
            failures.append(_failure("snapshot_ambiguous", "trusted port returned duplicate selected snapshots", selected[0]))
    if any(key not in expected for key in actual):
        failures.append(_failure("snapshot_unselected", "trusted port returned an unselected snapshot"))

    for snapshot in snapshots:
        binding, resource, version, proof = snapshot.binding, snapshot.resource, snapshot.version, snapshot.scope_proof
        selected = next((item for item in selections if _selection_key(item) == _snapshot_key(snapshot)), None)
        if selected is None:
            continue
        if (
            resource.scope.tenant_id != request.tenant_id
            or binding.scope.tenant_id != request.tenant_id
            or proof.tenant_id != request.tenant_id
            or proof.subject_id != request.subject_id
            or proof.project_id != request.project_id
            or proof.scope_id != request.project_id
            or proof.kind is not ScopeKind.PROJECT
            or proof.generation != request.desired_generation
        ):
            failures.append(_failure("scope_proof_mismatch", "snapshot scope proof does not match requested project revision", selected))
        if resource.kind not in {ResourceKind.PROMPT, ResourceKind.FILE, ResourceKind.MCP}:
            failures.append(_failure("resource_kind_unsupported", "only prompt, file and MCP snapshots can be planned", selected))
        if (
            binding.state is not BindingState.ACTIVE
            or resource.lifecycle is not ResourceLifecycle.ACTIVE
            or version.status is not VersionStatus.PUBLISHED
            or resource.active_version != version.sequence
            or binding.version_id != version.version_id
            or binding.resource_id != resource.resource_id
            or version.resource_id not in (None, resource.resource_id)
        ):
            failures.append(_failure("snapshot_lifecycle_mismatch", "resource snapshot is not active and pinned to its published version", selected))
        if binding.profile_id != selected.profile_id or resource.profile_id != selected.profile_id:
            failures.append(_failure("profile_binding_mismatch", "binding and resource must match the explicitly selected profile", selected))
        policy_errors = binding.policy_errors()
        if (
            binding.selection_policy is not None
            and binding.selection_policy.value == "explicit_projects"
            and request.project_id in (binding.project_ids or ())
            and not policy_errors
        ):
            pass
        else:
            code = "ambiguous_project_selection" if binding.selection_policy is None else "project_not_explicitly_bound"
            failures.append(_failure(code, "resource requires an explicit binding to this project", selected))
        target_scope = binding.effective_target_scope
        if (
            target_scope.kind is not ScopeKind.PROJECT
            or target_scope.scope_id != request.project_id
            or target_scope.tenant_id != request.tenant_id
            or not binding.matches_project(request.project_id, (request.project_id,))
        ):
            failures.append(_failure("project_binding_mismatch", "binding does not explicitly resolve to this project", selected))
        payload_errors = validate_resource_payload(resource.kind, version.payload)
        for code in payload_errors:
            failures.append(_failure(code, "resource payload validation failed", selected))
    return tuple(failures)


def _path_conflicts(items: tuple[_ResolvedSelection, ...]) -> tuple[PlanFailure, ...]:
    paths: dict[str, list[_ResolvedSelection]] = defaultdict(list)
    failures: list[PlanFailure] = []
    for item in items:
        normalized = item.target_path.replace("\\", "/")
        if not normalized or not safe_workspace_path(normalized):
            failures.append(_failure("target_path_invalid", "resource target is not a safe workspace path", item.selection))
        paths[normalized.casefold()].append(item)
    for path, owners in sorted(paths.items()):
        if len(owners) > 1:
            for owner in owners:
                failures.append(_failure("target_path_conflict", f"multiple resources target {path}", owner.selection))
    ordered = sorted(paths)
    for index, path in enumerate(ordered):
        for other in ordered[index + 1:]:
            if other.startswith(path.rstrip("/") + "/"):
                for owner in paths[path] + paths[other]:
                    failures.append(_failure("target_path_conflict", "a resource target is also a parent directory", owner.selection))
    return tuple(failures)


def _build_operation(item: _ResolvedSelection) -> PlannedResourceOperation:
    snapshot, selection = item.snapshot, item.selection
    provenance = snapshot.provenance
    if provenance is None:
        raise ValueError("snapshot provenance is required")
    payload = snapshot.version.payload
    priority = snapshot.binding.priority if snapshot.resource.kind is ResourceKind.PROMPT else None
    asset = asset_from_payload(payload)
    return PlannedResourceOperation(
        operation_id=f"resource:{snapshot.resource.resource_id}:{snapshot.binding.binding_id}:{snapshot.version.version_id}",
        kind=snapshot.resource.kind,
        target_path=item.target_path.replace("\\", "/"),
        payload=payload,
        required=selection.required,
        resource_id=snapshot.resource.resource_id,
        binding_id=snapshot.binding.binding_id,
        profile_id=selection.profile_id or "",
        version_id=snapshot.version.version_id,
        version_digest=snapshot.version.content_digest,
        asset_id=asset.asset_id if asset is not None else None,
        asset_version_id=asset.version_id if asset is not None else None,
        asset_digest=asset.digest if asset is not None else snapshot.version.asset_digest,
        prompt_priority=priority,
        source_kind=provenance.source_kind,
        source_id=provenance.source_id,
        source_digest=provenance.source_digest,
        scope_proof_id=provenance.scope_proof_id,
        acl_evidence_id=item.acl.evidence_id if item.acl else None,
        acl_evidence_digest=item.acl.evidence_digest if item.acl else None,
    )


def _operation_order(operation: PlannedResourceOperation) -> tuple[object, ...]:
    if operation.kind is ResourceKind.PROMPT:
        return (0, -(operation.prompt_priority or 0), operation.source_kind, operation.source_id, operation.version_id, operation.binding_id)
    return (1, operation.kind.value, operation.target_path.casefold(), operation.resource_id, operation.version_id, operation.binding_id)


@dataclass(frozen=True, slots=True)
class ResourcePlanBuilder:
    """Build a deterministic project plan from trusted snapshots; never applies it."""

    ports: ResourcePlanPorts

    async def build(self, request: ResourcePlanRequest) -> ResourceProjectPlan:
        failures: list[PlanFailure] = []
        selections = tuple(sorted(request.selections, key=_selection_order_key))
        if not selections:
            failures.append(_failure("selection_empty", "at least one explicit resource selection is required"))

        try:
            upstream = tuple(await self.ports.upstream_evidence.read_upstream_evidence(request=request))
            if any(not isinstance(item, UpstreamEvidence) for item in upstream):
                upstream = ()
                failures.append(_failure("upstream_evidence_invalid", "trusted port returned malformed upstream evidence"))
        except Exception:
            upstream = ()
            failures.append(_failure("upstream_evidence_read_failed", "trusted upstream evidence could not be read"))
        failures.extend(_validate_upstream(request, upstream))

        snapshots: tuple[ResourceSnapshot, ...] = ()
        if not failures:
            try:
                snapshots = tuple(await self.ports.snapshots.read_snapshots(request=request, selections=selections))
                if any(not isinstance(snapshot, ResourceSnapshot) for snapshot in snapshots):
                    snapshots = ()
                    failures.append(_failure("snapshot_invalid", "trusted port returned a malformed resource snapshot"))
            except Exception:
                failures.append(_failure("snapshot_read_failed", "trusted resource snapshots could not be read"))
        failures.extend(_snapshot_failures(request, selections, snapshots))

        snapshot_digest = canonical_digest({
            "request": {
                "tenant_id": request.tenant_id,
                "subject_id": request.subject_id,
                "project_id": request.project_id,
                "revision_id": request.revision_id,
                "target_scope": {"kind": request.target_scope.kind.value, "id": request.target_scope.scope_id},
                "generation": request.desired_generation,
            "selections": [
                {
                    "identity": _selection_key(selection),
                    "target_path": selection.target_path,
                    "required": selection.required,
                }
                for selection in selections
            ],
            },
            "snapshots": [
                {
                    "resource_id": snapshot.resource.resource_id,
                    "resource_kind": snapshot.resource.kind.value,
                    "resource_scope": {
                        "kind": snapshot.resource.scope.kind.value,
                        "id": snapshot.resource.scope.scope_id,
                        "tenant_id": snapshot.resource.scope.tenant_id,
                    },
                    "resource_lifecycle": snapshot.resource.lifecycle.value,
                    "resource_active_version": snapshot.resource.active_version,
                    "resource_source_kind": snapshot.resource.source_kind,
                    "resource_source_id": snapshot.resource.source_id,
                    "resource_source_digest": snapshot.resource.source_digest,
                    "resource_metadata": snapshot.resource.metadata,
                    "binding_id": snapshot.binding.binding_id,
                    "binding_state": snapshot.binding.state.value,
                    "binding_generation": snapshot.binding.generation,
                    "binding_kind": snapshot.binding.binding_kind.value,
                    "binding_session_id": snapshot.binding.session_id,
                    "binding_project_id": snapshot.binding.project_id,
                    "binding_priority": snapshot.binding.priority,
                    "binding_profile_id": snapshot.binding.profile_id,
                    "binding_selection_policy": snapshot.binding.selection_policy.value if snapshot.binding.selection_policy else None,
                    "binding_project_ids": snapshot.binding.project_ids,
                    "binding_target_scope": {
                        "kind": snapshot.binding.effective_target_scope.kind.value,
                        "id": snapshot.binding.effective_target_scope.scope_id,
                    },
                    "profile_id": snapshot.resource.profile_id,
                    "version_id": snapshot.version.version_id,
                    "version_sequence": snapshot.version.sequence,
                    "version_schema": snapshot.version.schema_version,
                    "version_status": snapshot.version.status.value,
                    "version_digest": snapshot.version.content_digest,
                    "version_secret_references": [
                        {"provider": item.provider, "locator": item.locator, "version": item.version}
                        for item in snapshot.version.secret_references
                    ],
                    "asset_digest": snapshot.version.asset_digest,
                    "generation": snapshot.generation,
                    "scope_proof": {
                        "proof_id": snapshot.scope_proof.proof_id,
                        "tenant_id": snapshot.scope_proof.tenant_id,
                        "subject_id": snapshot.scope_proof.subject_id,
                        "kind": snapshot.scope_proof.kind.value,
                        "scope_id": snapshot.scope_proof.scope_id,
                        "session_id": snapshot.scope_proof.session_id,
                        "project_id": snapshot.scope_proof.project_id,
                        "generation": snapshot.scope_proof.generation,
                    },
                    "provenance": {
                        "resource_id": snapshot.provenance.resource_id,
                        "version_id": snapshot.provenance.version_id,
                        "binding_id": snapshot.provenance.binding_id,
                        "source_kind": snapshot.provenance.source_kind,
                        "source_id": snapshot.provenance.source_id,
                        "source_digest": snapshot.provenance.source_digest,
                        "asset_digest": snapshot.provenance.asset_digest,
                        "scope_proof_id": snapshot.provenance.scope_proof_id,
                        "generation": snapshot.provenance.generation,
                    } if snapshot.provenance is not None else None,
                }
                for snapshot in sorted(snapshots, key=_snapshot_order_key)
            ],
        })

        resolved: list[_ResolvedSelection] = []
        if not failures:
            by_key = {_snapshot_key(snapshot): snapshot for snapshot in snapshots}
            for selection in selections:
                snapshot = by_key.get(_selection_key(selection))
                if snapshot is None:
                    continue
                target = _target_path(selection, snapshot)
                if target is None:
                    failures.append(_failure("target_path_missing", "resource has no deterministic target path", selection))
                    continue
                try:
                    normalized_target = normalize_workspace_path(target)
                except Exception:
                    normalized_target = ""
                if not normalized_target or not safe_workspace_path(normalized_target):
                    failures.append(_failure("target_path_invalid", "resource target is not a safe workspace path", selection))
                    continue
                acl = None
                try:
                    asset = asset_from_payload(snapshot.version.payload)
                except ValueError:
                    asset = None
                if asset is not None:
                    try:
                        acl = await self.ports.file_acl.read_file_ref_acl_evidence(
                            request=request,
                            snapshot=snapshot,
                            asset_id=asset.asset_id,
                            asset_version_id=asset.version_id,
                        )
                    except Exception:
                        acl = None
                    if not isinstance(acl, FileRefAclEvidence) or not self._acl_matches(request, selection, snapshot, asset.asset_id, asset.version_id, acl):
                        failures.append(_failure("file_ref_acl_evidence_missing_or_mismatched", "FileRef requires matching trusted ACL evidence", selection))
                resolved.append(_ResolvedSelection(selection, snapshot, normalized_target, acl))

        failures.extend(_path_conflicts(tuple(resolved)))
        operations: list[PlannedResourceOperation] = []
        warnings: list[PlanFailure] = []
        if not failures:
            try:
                operations = [_build_operation(item) for item in resolved]
            except (TypeError, ValueError):
                failures.append(_failure("operation_contract_invalid", "immutable resource operation could not be constructed"))

        if not failures:
            for operation in sorted(operations, key=_operation_order):
                try:
                    readiness = await self.ports.readiness.validate_operation(request=request, operation=operation)
                except Exception:
                    readiness = None
                if not isinstance(readiness, ResourceOperationReadiness) or readiness.operation_id != operation.operation_id or readiness.ready is not True:
                    reason = "operation_readiness_missing" if not isinstance(readiness, ResourceOperationReadiness) else readiness.reason_code
                    failures.append(PlanFailure(reason, "resource operation did not pass the required readiness barrier", operation.resource_id, operation.required))

        if failures:
            operations = []
        else:
            operations.sort(key=_operation_order)

        normalized_failures = tuple(sorted(failures, key=lambda item: (item.code, item.resource_id or "", item.message)))
        gate_digest = canonical_digest([
            {
                "gate_id": item.gate_id,
                "evidence_id": item.evidence_id,
                "digest": item.evidence_digest,
                "tenant_id": item.tenant_id,
                "project_id": item.project_id,
                "revision_id": item.revision_id,
                "generation": item.generation,
                "status": item.status,
            }
            for item in sorted(upstream, key=_evidence_order_key)
        ])
        revision_digest = canonical_digest({
            "request": {
                "tenant_id": request.tenant_id,
                "subject_id": request.subject_id,
                "project_id": request.project_id,
                "revision_id": request.revision_id,
                "generation": request.desired_generation,
            },
            "snapshot_digest": snapshot_digest,
            "upstream_evidence_digest": gate_digest,
            "operations": [operation.canonical() for operation in operations],
            "failures": [{"code": item.code, "resource_id": item.resource_id, "required": item.required} for item in normalized_failures],
            "warnings": [{"code": item.code, "resource_id": item.resource_id} for item in warnings],
        })
        return ResourceProjectPlan(request, tuple(operations), normalized_failures, upstream, revision_digest, snapshot_digest, tuple(warnings))

    @staticmethod
    def _acl_matches(
        request: ResourcePlanRequest,
        selection: ResourcePlanSelection,
        snapshot: ResourceSnapshot,
        asset_id: str,
        asset_version_id: str,
        evidence: FileRefAclEvidence | None,
    ) -> bool:
        return bool(
            evidence is not None
            and evidence.allowed
            and evidence.tenant_id == request.tenant_id
            and evidence.project_id == request.project_id
            and evidence.subject_id == request.subject_id
            and evidence.resource_id == selection.resource_id
            and evidence.version_id == selection.version_id
            and evidence.asset_id == asset_id
            and evidence.asset_version_id == asset_version_id
            and evidence.generation == request.desired_generation
            and snapshot.generation == request.desired_generation
            and evidence.allowed is True
        )


__all__ = ["ResourcePlanBuilder"]
