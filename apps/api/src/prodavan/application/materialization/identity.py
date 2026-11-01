"""Stable identities for persisted materialization plan entries."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from prodavan.domain.materialization.types import MaterializationOperation, SourceProvenance


def canonicalize(value: Any) -> Any:
    """Return JSON-compatible data with deterministic mapping key ordering."""
    if isinstance(value, dict):
        return {str(key): canonicalize(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if hasattr(value, "value"):
        return canonicalize(value.value)
    return value


def build_digest(
    *,
    project_id: str,
    operation: MaterializationOperation,
    provenance: SourceProvenance,
    desired_generation: int,
) -> str:
    """Hash the complete desired operation, excluding mutable apply state."""
    payload = {
        "project_id": project_id,
        "operation": {
            "kind": operation.kind,
            "target": operation.target,
            "payload": operation.payload,
        },
        "provenance": {
            "source_type": provenance.source_type,
            "source_id": provenance.source_id,
            "source_generation": provenance.source_generation,
            "source_path": provenance.source_path,
            "source_revision": provenance.source_revision,
            "metadata": provenance.metadata,
        },
        "desired_generation": desired_generation,
    }
    encoded = json.dumps(canonicalize(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_idempotency_key(*, project_id: str, target: str, digest: str) -> str:
    """Create a scoped key suitable for a unique persistence constraint."""
    if not project_id.strip() or not target.strip() or not digest.strip():
        raise ValueError("project_id, target, and digest are required")
    raw = f"materialization:{project_id}:{target}:{digest}"
    return hashlib.sha256(raw.encode()).hexdigest()
