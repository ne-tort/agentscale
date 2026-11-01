"""Resource/materialization plan adapter over runtime revision contracts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable

from prodavan.application.materialization_adapter.contracts import (
    ResourceMaterializationPlan,
    ResourceMaterializationRequest,
    ResourceOperation,
)
from prodavan.application.materialization.identity import build_digest, build_idempotency_key, canonicalize
from prodavan.application.runtime_revision import MaterializationEntry
from prodavan.domain.materialization import MaterializationOperation, OperationKind, SourceProvenance


class ResourceMaterializationAdapter:
    """Translate resource operations into immutable runtime revision entries.

    The adapter is deliberately a planner only.  It does not import or call the
    legacy ``MaterializeExecutor`` and it refuses to report a complete plan when
    a required operation kind is absent.
    """

    def build_plan(self, request: ResourceMaterializationRequest) -> ResourceMaterializationPlan:
        operations = tuple(
            sorted(
                _normalize_operations(request.operations, request.source),
                key=lambda operation: (
                    operation.kind.value,
                    operation.target,
                    json.dumps(_canonical(operation.payload), ensure_ascii=False, sort_keys=True),
                    json.dumps(
                        _canonical(_provenance(operation.provenance, operation)),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
        )
        duplicate_targets = tuple(
            sorted(
                target
                for target, count in _counts(operation.target for operation in operations).items()
                if count > 1
            )
        )
        required = request.required_kinds or frozenset(op.kind for op in operations if op.required)
        present = frozenset(op.kind for op in operations)
        missing = tuple(sorted(required - present, key=lambda kind: kind.value))
        entries = tuple(
            self._entry(request=request, operation=operation, ordinal=index)
            for index, operation in enumerate(operations)
        )
        payload = {
            "project_id": request.project_id,
            "revision_id": request.revision_id,
            "desired_generation": request.desired_generation,
            "applied_generation": request.applied_generation,
            "required_operations": sorted(kind.value for kind in required),
            "missing_required": [kind.value for kind in missing],
            "duplicate_targets": list(duplicate_targets),
            "entries": [
                {
                    "id": entry.entry_id,
                    "path": entry.workspace_path,
                    "kind": entry.kind,
                    "generation": entry.generation,
                    "idempotency_key": entry.idempotency_key,
                    "operation_digest": entry.operation_digest,
                    "provenance": entry.provenance,
                }
                for entry in entries
            ],
        }
        digest = _digest(payload)
        return ResourceMaterializationPlan(
            project_id=request.project_id,
            revision_id=request.revision_id,
            desired_generation=request.desired_generation,
            applied_generation=request.applied_generation,
            entries=entries,
            digest=digest,
            idempotency_key=_digest(
                {"revision_id": request.revision_id, "project_id": request.project_id, "digest": digest}
            ),
            required_operations=tuple(sorted(kind.value for kind in required)),
            missing_required=missing,
            diagnostics={
                "fail_closed": bool(missing or duplicate_targets),
                "source_generation": request.source.source_generation if request.source else None,
                "duplicate_targets": duplicate_targets,
            },
        )

    def _entry(
        self,
        *,
        request: ResourceMaterializationRequest,
        operation: ResourceOperation,
        ordinal: int,
    ) -> MaterializationEntry:
        source = operation.provenance or request.source
        provenance = _provenance(source, operation)
        frozen_provenance = MappingProxyType(provenance)
        operation_payload = {
            "kind": operation.kind.value,
            "target": operation.target,
            "payload": operation.payload,
            "provenance": frozen_provenance,
        }
        effective_provenance = source or SourceProvenance(
            source_type="resource",
            source_id=operation.target,
            source_generation=0,
        )
        operation_digest = build_digest(
            project_id=request.project_id,
            operation=MaterializationOperation(
                kind=operation.kind,
                target=operation.target,
                payload=operation.payload,
            ),
            provenance=effective_provenance,
            desired_generation=request.desired_generation,
        )
        entry_id = f"{request.revision_id}:materialization:{ordinal}:{operation.kind.value}"
        idempotency_key = build_idempotency_key(
            project_id=request.project_id,
            target=operation.target,
            digest=operation_digest,
        )
        return MaterializationEntry(
            entry_id=entry_id,
            revision_id=request.revision_id,
            workspace_path=operation.target,
            kind=operation.kind.value,
            generation=request.desired_generation,
            source_asset_id=_source_id(source, "asset"),
            source_capability_id=_source_id(source, "capability"),
            provenance=frozen_provenance,
            idempotency_key=idempotency_key,
            operation_digest=operation_digest,
        )


def _counts(values: Iterable[str]) -> Counter[str]:
    return Counter(values)


def _normalize_operations(
    operations: Iterable[ResourceOperation], source: SourceProvenance | None
) -> tuple[ResourceOperation, ...]:
    normalized: list[ResourceOperation] = []
    for operation in operations:
        normalized.append(
            operation
            if operation.provenance is not None or source is None
            else replace(operation, provenance=source)
        )
    return tuple(normalized)


def _provenance(source: SourceProvenance | None, operation: ResourceOperation) -> dict[str, Any]:
    if source is None:
        return {"source_type": "resource", "source_id": operation.target, "source_generation": 0}
    return {
        "source_type": source.source_type,
        "source_id": source.source_id,
        "source_generation": source.source_generation,
        "source_path": source.source_path,
        "source_revision": source.source_revision,
        "metadata": source.metadata,
    }


def _source_id(source: SourceProvenance | None, key: str) -> str | None:
    if source is None:
        return None
    value = source.metadata.get(f"{key}_id")
    return str(value) if value is not None else None


def _digest(value: Any) -> str:
    encoded = json.dumps(_canonical(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, Enum):
        return _canonical(value.value)
    return canonicalize(value)
