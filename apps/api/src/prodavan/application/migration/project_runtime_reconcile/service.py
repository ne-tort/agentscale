"""Port-only desired/applied reconciliation for project runtimes.

The service reads ProjectRuntimeRevision and runtime-materialization evidence,
then plans fenced actions. It contains no Kubernetes, API, database, or ORM
adapter and never writes the source revision/materialization records.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from prodavan.application.migration.runtime_materialization.state_machine import canonical_digest
from prodavan.application.migration.runtime_api.contracts import RuntimeApiProjectScope
from prodavan.application.runtime_revision.interfaces import (
    MaterializationEntry,
    MaterializationStatus,
    ProjectRuntimeRevision,
    RevisionState,
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SECRET_MARKERS = ("password", "secret", "token", "credential", "private_key", "api_key")


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip().lower() == "latest":
        raise ValueError(f"{name}_missing_or_unversioned")
    cleaned = value.strip()
    if cleaned.lower().endswith(":latest"):
        raise ValueError(f"{name}_missing_or_unversioned")
    return cleaned


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name}_digest_invalid")
    return value


def _secret_ref(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(("vault://", "secret://", "file://", "${")) and not any(
        lowered.startswith(f"{marker}=") for marker in _SECRET_MARKERS
    )


def _safe_config_provenance(value: Mapping[str, object]) -> dict[str, object]:
    """Allow structured config and opaque secret references, never secret values."""
    clean: dict[str, object] = {}
    for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
        name = _required(str(key), "config_key")
        lowered = name.lower()
        secret_field = any(marker in lowered for marker in _SECRET_MARKERS)
        if secret_field:
            if not (lowered.endswith(("_ref", "_reference")) and isinstance(item, str) and _secret_ref(item)):
                raise ValueError("inline_secret_config_forbidden")
            clean[name] = item
        else:
            clean[name] = _safe_config_value(item)
    return clean


def _safe_config_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(_safe_config_provenance(value))
    if isinstance(value, (tuple, list)):
        return tuple(_safe_config_value(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and value.lower().startswith(("password=", "secret=", "token=", "api_key=")):
            raise ValueError("inline_secret_config_forbidden")
        if isinstance(value, str) and (value.strip().lower() == "latest" or value.strip().lower().endswith(":latest")):
            raise ValueError("unversioned_config_value_forbidden")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("config_provenance_value_invalid")
        return value
    raise ValueError("config_provenance_value_invalid")


def _safe_materialization_provenance(value: object) -> object:
    if isinstance(value, Mapping):
        clean: dict[str, object] = {}
        for key, item in value.items():
            name = _required(str(key), "provenance_key")
            lowered = name.lower()
            if any(marker in lowered for marker in _SECRET_MARKERS):
                if not (lowered.endswith(("_ref", "_reference")) and isinstance(item, str) and _secret_ref(item)):
                    raise ValueError("inline_secret_provenance_forbidden")
                clean[name] = item
            else:
                clean[name] = _safe_materialization_provenance(item)
        return clean
    if isinstance(value, (tuple, list)):
        return [_safe_materialization_provenance(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and value.lower().startswith(("password=", "secret=", "token=", "api_key=")):
            raise ValueError("inline_secret_provenance_forbidden")
        if isinstance(value, str) and (value.strip().lower() == "latest" or value.strip().lower().endswith(":latest")):
            raise ValueError("unversioned_provenance_value_forbidden")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("materialization_provenance_invalid")
        return value
    raise ValueError("materialization_provenance_invalid")


@dataclass(frozen=True, slots=True)
class SourceVersionEvidence:
    source_kind: str
    source_id: str
    source_version: str
    content_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_kind", _required(self.source_kind, "source_kind"))
        object.__setattr__(self, "source_id", _required(self.source_id, "source_id"))
        object.__setattr__(self, "source_version", _required(self.source_version, "source_version"))
        object.__setattr__(self, "content_digest", _digest(self.content_digest, "source_content"))

    def canonical(self) -> dict[str, str]:
        return {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "source_version": self.source_version,
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True, slots=True)
class ModuleBindingEvidence:
    module_id: str
    binding_id: str
    bind_kind: str
    instance_id: str
    version: SourceVersionEvidence
    child_may_edit: bool = False

    def __post_init__(self) -> None:
        for name in ("module_id", "binding_id", "bind_kind", "instance_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.bind_kind not in {"local", "global"}:
            raise ValueError("module_bind_kind_invalid")
        if not isinstance(self.version, SourceVersionEvidence):
            raise ValueError("module_binding_version_evidence_missing")
        if not isinstance(self.child_may_edit, bool):
            raise ValueError("module_binding_edit_policy_invalid")

    def canonical(self) -> dict[str, object]:
        return {
            "module_id": self.module_id,
            "binding_id": self.binding_id,
            "bind_kind": self.bind_kind,
            "instance_id": self.instance_id,
            "version": self.version.canonical(),
            "child_may_edit": self.child_may_edit,
        }


@dataclass(frozen=True, slots=True)
class ProviderBindingEvidence:
    provider_id: str
    provider_binding_id: str
    model_id: str
    version: SourceVersionEvidence

    def __post_init__(self) -> None:
        for name in ("provider_id", "provider_binding_id", "model_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if not isinstance(self.version, SourceVersionEvidence):
            raise ValueError("provider_version_evidence_missing")

    def canonical(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "provider_binding_id": self.provider_binding_id,
            "model_id": self.model_id,
            "version": self.version.canonical(),
        }


@dataclass(frozen=True, slots=True)
class ConfigProvenance:
    config_key: str
    source: SourceVersionEvidence
    values: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "config_key", _required(self.config_key, "config_key"))
        if not isinstance(self.source, SourceVersionEvidence):
            raise ValueError("config_source_version_evidence_missing")
        if not isinstance(self.values, Mapping):
            raise ValueError("config_provenance_values_missing")
        clean = _safe_config_provenance(self.values)
        object.__setattr__(self, "values", MappingProxyType(clean))

    def canonical(self) -> dict[str, object]:
        return {
            "config_key": self.config_key,
            "source": self.source.canonical(),
            "values": dict(self.values),
            "values_digest": canonical_digest(self.values),
        }


@dataclass(frozen=True, slots=True)
class MaterializationReadSnapshot:
    scope: RuntimeApiProjectScope
    revision_id: str
    generation: int
    entries: tuple[MaterializationEntry, ...]
    complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RuntimeApiProjectScope):
            raise ValueError("exact_materialization_scope_required")
        object.__setattr__(self, "revision_id", _required(self.revision_id, "materialization_revision"))
        if not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation < 1:
            raise ValueError("materialization_generation_missing")
        if self.complete is not True:
            raise ValueError("materialization_snapshot_incomplete")
        entries = tuple(self.entries)
        if any(not isinstance(entry, MaterializationEntry) for entry in entries):
            raise ValueError("materialization_entry_invalid")
        object.__setattr__(self, "entries", entries)


@dataclass(frozen=True, slots=True)
class RuntimeBindingsReadSnapshot:
    scope: RuntimeApiProjectScope
    revision_id: str
    module_bindings: tuple[ModuleBindingEvidence, ...]
    provider_bindings: tuple[ProviderBindingEvidence, ...]
    config_provenance: tuple[ConfigProvenance, ...]
    complete: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RuntimeApiProjectScope):
            raise ValueError("exact_runtime_bindings_scope_required")
        object.__setattr__(self, "revision_id", _required(self.revision_id, "bindings_revision"))
        modules, providers, configs = tuple(self.module_bindings), tuple(self.provider_bindings), tuple(self.config_provenance)
        if any(not isinstance(row, ModuleBindingEvidence) for row in modules):
            raise ValueError("module_binding_evidence_invalid")
        if any(not isinstance(row, ProviderBindingEvidence) for row in providers):
            raise ValueError("provider_binding_evidence_invalid")
        if any(not isinstance(row, ConfigProvenance) for row in configs):
            raise ValueError("config_provenance_evidence_invalid")
        if self.complete is not True:
            raise ValueError("runtime_bindings_snapshot_incomplete")
        object.__setattr__(self, "module_bindings", modules)
        object.__setattr__(self, "provider_bindings", providers)
        object.__setattr__(self, "config_provenance", configs)


@dataclass(frozen=True, slots=True)
class DesiredRuntimeSnapshot:
    scope: RuntimeApiProjectScope
    revision_id: str
    revision: int
    bundle_id: str
    bundle_digest: str
    manifest_digest: str
    config_digest: str
    runtime_image_digest: str
    materialization_generation: int
    hydrate_generation: int
    workspace_digest: str
    module_bindings: tuple[ModuleBindingEvidence, ...]
    provider_bindings: tuple[ProviderBindingEvidence, ...]
    config_provenance: tuple[ConfigProvenance, ...]
    expected_pod_id: str | None
    expected_pod_uid: str | None
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RuntimeApiProjectScope):
            raise ValueError("exact_project_scope_required")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("project_runtime_revision_evidence_invalid")
        _required(self.revision_id, "project_runtime_revision_id")
        _required(self.bundle_id, "capability_bundle_id")
        if (not isinstance(self.materialization_generation, int)
                or isinstance(self.materialization_generation, bool) or self.materialization_generation < 1):
            raise ValueError("materialization_generation_missing")
        if not isinstance(self.hydrate_generation, int) or isinstance(self.hydrate_generation, bool) or self.hydrate_generation < 0:
            raise ValueError("project_hydrate_generation_invalid")
        for field_name in ("bundle_digest", "manifest_digest", "config_digest", "runtime_image_digest", "workspace_digest"):
            _digest(getattr(self, field_name), field_name)
        modules = tuple(sorted(self.module_bindings, key=lambda row: (row.module_id, row.binding_id)))
        providers = tuple(sorted(self.provider_bindings, key=lambda row: (row.provider_id, row.provider_binding_id)))
        configs = tuple(sorted(self.config_provenance, key=lambda row: row.config_key))
        object.__setattr__(self, "module_bindings", modules)
        object.__setattr__(self, "provider_bindings", providers)
        object.__setattr__(self, "config_provenance", configs)
        if not self.provider_bindings or not self.config_provenance:
            raise ValueError("runtime_binding_or_config_version_evidence_missing")
        if (any(not isinstance(row, ModuleBindingEvidence) for row in self.module_bindings)
                or any(not isinstance(row, ProviderBindingEvidence) for row in self.provider_bindings)
                or any(not isinstance(row, ConfigProvenance) for row in self.config_provenance)):
            raise ValueError("runtime_binding_or_config_evidence_invalid")
        if (len({row.module_id for row in self.module_bindings}) != len(self.module_bindings)
                or len({row.provider_binding_id for row in self.provider_bindings}) != len(self.provider_bindings)
                or len({row.config_key for row in self.config_provenance}) != len(self.config_provenance)):
            raise ValueError("duplicate_runtime_binding_or_config_evidence")
        if self.manifest_digest != self.bundle_digest:
            raise ValueError("capability_bundle_manifest_digest_mismatch")
        if self.config_digest != canonical_digest([row.canonical() for row in self.config_provenance]):
            raise ValueError("config_provenance_digest_mismatch")
        if self.expected_pod_id is not None:
            if _required(self.expected_pod_id, "expected_pod_id") != self.scope.pod_id:
                raise ValueError("expected_pod_id_scope_mismatch")
        if self.expected_pod_uid is not None:
            _required(self.expected_pod_uid, "expected_pod_uid")
        if self.digest and self.digest != canonical_digest(self.canonical()):
            raise ValueError("desired_runtime_snapshot_digest_mismatch")

    def canonical(self) -> dict[str, object]:
        return {
            "scope": _scope_canonical(self.scope),
            "revision_id": self.revision_id,
            "revision": self.revision,
            "bundle_id": self.bundle_id,
            "bundle_digest": self.bundle_digest,
            "manifest_digest": self.manifest_digest,
            "config_digest": self.config_digest,
            "runtime_image_digest": self.runtime_image_digest,
            "materialization_generation": self.materialization_generation,
            "hydrate_generation": self.hydrate_generation,
            "workspace_digest": self.workspace_digest,
            "module_bindings": [row.canonical() for row in self.module_bindings],
            "provider_bindings": [row.canonical() for row in self.provider_bindings],
            "config_provenance": [row.canonical() for row in self.config_provenance],
            "expected_pod_id": self.expected_pod_id,
            "expected_pod_uid": self.expected_pod_uid,
        }

    @classmethod
    def build(
        cls,
        *,
        scope: RuntimeApiProjectScope,
        revision: ProjectRuntimeRevision,
        materialization: MaterializationReadSnapshot,
        bindings: RuntimeBindingsReadSnapshot,
    ) -> "DesiredRuntimeSnapshot":
        if not isinstance(scope, RuntimeApiProjectScope):
            raise ValueError("exact_project_scope_required")
        if not isinstance(revision, ProjectRuntimeRevision):
            raise ValueError("project_runtime_revision_missing")
        if revision.project_id != scope.project_id or revision.bundle.project_id != scope.project_id:
            raise ValueError("project_runtime_revision_scope_mismatch")
        if revision.pod_id is not None and revision.pod_id != scope.pod_id:
            raise ValueError("project_runtime_pod_scope_mismatch")
        if not isinstance(materialization, MaterializationReadSnapshot):
            raise ValueError("materialization_snapshot_missing")
        if not isinstance(bindings, RuntimeBindingsReadSnapshot):
            raise ValueError("runtime_bindings_snapshot_missing")
        if materialization.scope != scope or materialization.revision_id != revision.revision_id:
            raise ValueError("materialization_scope_or_revision_mismatch")
        if bindings.scope != scope or bindings.revision_id != revision.revision_id:
            raise ValueError("runtime_bindings_scope_or_revision_mismatch")
        if (revision.revision < 1 or revision.bundle.revision != revision.revision
                or revision.state not in {RevisionState.VALIDATED, RevisionState.READY}):
            raise ValueError("project_runtime_revision_evidence_invalid")
        if not isinstance(revision.bundle.manifest, Mapping):
            raise ValueError("capability_manifest_missing")
        manifest = _safe_materialization_provenance(revision.bundle.manifest)
        bundle_digest = _digest(revision.bundle.digest, "capability_bundle")
        if bundle_digest != canonical_digest(manifest):
            raise ValueError("capability_bundle_manifest_digest_mismatch")
        image_digest = _digest(revision.runtime_image_digest, "runtime_image")
        module_bindings = tuple(sorted(bindings.module_bindings, key=lambda row: (row.module_id, row.binding_id)))
        provider_bindings = tuple(sorted(bindings.provider_bindings, key=lambda row: (row.provider_id, row.provider_binding_id)))
        config_provenance = tuple(sorted(bindings.config_provenance, key=lambda row: row.config_key))
        if not provider_bindings or not config_provenance:
            raise ValueError("runtime_binding_or_config_version_evidence_missing")
        if len({row.module_id for row in module_bindings}) != len(module_bindings):
            raise ValueError("duplicate_project_module_binding")
        if len({row.provider_binding_id for row in provider_bindings}) != len(provider_bindings):
            raise ValueError("duplicate_provider_binding")
        if len({row.config_key for row in config_provenance}) != len(config_provenance):
            raise ValueError("duplicate_config_provenance")

        materialized: list[dict[str, object]] = []
        seen_paths: set[str] = set()
        for entry in materialization.entries:
            if not isinstance(entry, MaterializationEntry):
                raise ValueError("materialization_entry_invalid")
            path = _required(entry.workspace_path, "workspace_path")
            parts = path.split("/")
            if (
                path.startswith("/") or ".." in path.split("/") or "\\" in path
                or re.match(r"^[A-Za-z]:", path) is not None
                or any(part in {"", "."} for part in parts)
                or "\x00" in path
            ):
                raise ValueError("workspace_path_invalid")
            if path in seen_paths:
                raise ValueError("duplicate_workspace_path")
            seen_paths.add(path)
            if entry.revision_id != revision.revision_id or entry.generation != materialization.generation:
                raise ValueError("materialization_entry_version_mismatch")
            if entry.status not in {
                MaterializationStatus.PLANNED,
                MaterializationStatus.STAGED,
                MaterializationStatus.APPLIED,
            }:
                raise ValueError("materialization_entry_not_desired")
            materialized.append({
                "path": path,
                "kind": _required(entry.kind, "materialization_kind"),
                "generation": entry.generation,
                "content_hash": _digest(entry.content_hash, "workspace_content"),
                "source_asset_id": entry.source_asset_id,
                "source_capability_id": entry.source_capability_id,
                "provenance": _safe_materialization_provenance(entry.provenance or {}),
            })
        materialized.sort(key=lambda row: str(row["path"]))
        manifest_digest = canonical_digest(manifest)
        config_digest = canonical_digest([row.canonical() for row in config_provenance])
        workspace_digest = canonical_digest({
            "project_id": scope.project_id,
            "revision_id": revision.revision_id,
            "generation": materialization.generation,
            "entries": materialized,
        })
        snapshot = cls(
            scope=scope,
            revision_id=revision.revision_id,
            revision=revision.revision,
            bundle_id=revision.bundle.bundle_id,
            bundle_digest=bundle_digest,
            manifest_digest=manifest_digest,
            config_digest=config_digest,
            runtime_image_digest=image_digest,
            materialization_generation=materialization.generation,
            hydrate_generation=revision.hydrate_generation,
            workspace_digest=workspace_digest,
            module_bindings=module_bindings,
            provider_bindings=provider_bindings,
            config_provenance=config_provenance,
            expected_pod_id=revision.pod_id,
            expected_pod_uid=revision.pod_uid,
            digest="",
        )
        return replace(snapshot, digest=canonical_digest(snapshot.canonical()))


class PodPresence(StrEnum):
    ABSENT = "absent"
    PRESENT = "present"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ObservedRuntime:
    scope: RuntimeApiProjectScope
    presence: PodPresence
    project_id: str | None = None
    pod_id: str | None = None
    pod_uid: str | None = None
    revision_id: str | None = None
    config_digest: str | None = None
    workspace_digest: str | None = None
    workspace_generation: int | None = None
    runtime_image_digest: str | None = None
    healthy: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RuntimeApiProjectScope):
            raise ValueError("exact_project_scope_required")
        object.__setattr__(self, "presence", PodPresence(self.presence))
        for field_name in ("project_id", "pod_id", "pod_uid", "revision_id"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise ValueError("observed_runtime_identity_invalid")
        for field_name in ("config_digest", "workspace_digest", "runtime_image_digest"):
            value = getattr(self, field_name)
            if value is not None:
                _digest(value, f"observed_{field_name}")
        if self.workspace_generation is not None and (
            not isinstance(self.workspace_generation, int) or isinstance(self.workspace_generation, bool)
            or self.workspace_generation < 0
        ):
            raise ValueError("observed_workspace_generation_invalid")
        if self.healthy is not None and not isinstance(self.healthy, bool):
            raise ValueError("observed_runtime_health_invalid")


class ReconcileAction(StrEnum):
    NOOP = "noop"
    APPLY = "apply"
    REPLACE = "replace"
    QUARANTINE = "quarantine"
    ROLLBACK = "rollback"


class ReconcileOutcome(StrEnum):
    NOOP = "noop"
    APPLIED = "applied"
    REPLACED = "replaced"
    ROLLED_BACK = "rolled_back"
    QUARANTINED = "quarantined"
    BLOCKED = "blocked"
    CONFLICT = "conflict"


class ClaimStatus(StrEnum):
    CLAIMED = "claimed"
    REPLAY = "replay"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class ReconcilePlan:
    scope: RuntimeApiProjectScope
    action: ReconcileAction
    expected_generation: int
    next_generation: int
    desired_digest: str
    mismatch_codes: tuple[str, ...]
    idempotency_key: str
    recovery_digest: str | None
    observed_digest: str
    plan_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RuntimeApiProjectScope):
            raise ValueError("exact_project_scope_required")
        object.__setattr__(self, "action", ReconcileAction(self.action))
        if (not isinstance(self.expected_generation, int) or isinstance(self.expected_generation, bool)
                or self.expected_generation < 0 or not isinstance(self.next_generation, int)
                or isinstance(self.next_generation, bool)):
            raise ValueError("reconcile_plan_generation_invalid")
        expected_next = self.expected_generation if self.action is ReconcileAction.NOOP else self.expected_generation + 1
        if self.next_generation != expected_next:
            raise ValueError("reconcile_plan_next_generation_invalid")
        _digest(self.desired_digest, "desired_runtime")
        if self.recovery_digest is not None:
            _digest(self.recovery_digest, "recovery_runtime")
        _digest(self.observed_digest, "observed_runtime")
        _required(self.idempotency_key, "idempotency_key")
        mismatches = tuple(self.mismatch_codes)
        if any(not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", code) for code in mismatches):
            raise ValueError("reconcile_plan_mismatch_codes_invalid")
        object.__setattr__(self, "mismatch_codes", mismatches)
        expected_digest = canonical_digest({
            "scope": _scope_canonical(self.scope), "action": self.action.value,
            "expected_generation": self.expected_generation, "next_generation": self.next_generation,
            "desired_digest": self.desired_digest, "mismatches": self.mismatch_codes,
            "idempotency_key": self.idempotency_key, "recovery_digest": self.recovery_digest,
            "observed_digest": self.observed_digest,
        })
        if self.plan_digest != expected_digest:
            raise ValueError("reconcile_plan_digest_mismatch")


@dataclass(frozen=True, slots=True)
class ReconcileRecord:
    scope: RuntimeApiProjectScope
    generation: int
    last_known_good: DesiredRuntimeSnapshot | None = None
    applied_digest: str | None = None
    applied_observed: ObservedRuntime | None = None
    last_outcome: ReconcileOutcome | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RuntimeApiProjectScope):
            raise ValueError("exact_project_scope_required")
        if not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation < 0:
            raise ValueError("reconcile_generation_invalid")
        if self.applied_digest is not None:
            _digest(self.applied_digest, "applied_runtime")
        if self.last_known_good is not None and not _lkg_valid(self.last_known_good, self.scope):
            raise ValueError("last_known_good_snapshot_invalid")
        if self.applied_observed is not None and self.applied_observed.scope != self.scope:
            raise ValueError("applied_observation_scope_mismatch")
        if self.applied_digest is not None and (
            self.last_known_good is None or self.last_known_good.digest != self.applied_digest
            or self.applied_observed is None
        ):
            raise ValueError("applied_runtime_evidence_incomplete")
        if self.last_outcome is not None:
            object.__setattr__(self, "last_outcome", ReconcileOutcome(self.last_outcome))


@dataclass(frozen=True, slots=True)
class FencedClaim:
    scope: RuntimeApiProjectScope
    expected_generation: int
    generation: int
    fence_token: str
    desired_digest: str
    plan_digest: str
    idempotency_key: str
    observed_digest: str
    recovery_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RuntimeApiProjectScope):
            raise ValueError("exact_project_scope_required")
        if (not isinstance(self.expected_generation, int) or isinstance(self.expected_generation, bool)
                or self.expected_generation < 0 or not isinstance(self.generation, int)
                or isinstance(self.generation, bool) or self.generation != self.expected_generation + 1):
            raise ValueError("fenced_claim_generation_invalid")
        if not isinstance(self.fence_token, str) or not self.fence_token.strip():
            raise ValueError("fence_token_missing")
        _digest(self.desired_digest, "desired_runtime")
        _digest(self.plan_digest, "reconcile_plan")
        _digest(self.observed_digest, "observed_runtime")
        if self.recovery_digest is not None:
            _digest(self.recovery_digest, "recovery_runtime")
        object.__setattr__(self, "idempotency_key", _required(self.idempotency_key, "idempotency_key"))


@dataclass(frozen=True, slots=True)
class ClaimResult:
    status: ClaimStatus
    claim: FencedClaim | None = None
    reason_code: str = ""


@dataclass(frozen=True, slots=True)
class RuntimeExecutionReceipt:
    scope: RuntimeApiProjectScope
    action: ReconcileAction
    fence_token: str
    generation: int
    snapshot_digest: str
    accepted: bool
    healthy: bool
    observed: ObservedRuntime | None
    reason_code: str = ""
    compared_observation_digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RuntimeApiProjectScope):
            raise ValueError("runtime_receipt_scope_invalid")
        object.__setattr__(self, "action", ReconcileAction(self.action))
        if not isinstance(self.fence_token, str) or not self.fence_token.strip():
            raise ValueError("runtime_receipt_fence_missing")
        if not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation < 1:
            raise ValueError("runtime_receipt_generation_invalid")
        _digest(self.snapshot_digest, "runtime_receipt_snapshot")
        _digest(self.compared_observation_digest, "runtime_receipt_observed")
        if not isinstance(self.accepted, bool) or not isinstance(self.healthy, bool):
            raise ValueError("runtime_receipt_status_invalid")
        if self.observed is not None and not isinstance(self.observed, ObservedRuntime):
            raise ValueError("runtime_receipt_observation_invalid")
        object.__setattr__(
            self,
            "reason_code",
            _safe_reason(self.reason_code) if isinstance(self.reason_code, str) and self.reason_code else "unspecified",
        )


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    outcome: ReconcileOutcome
    plan: ReconcilePlan | None
    desired: DesiredRuntimeSnapshot | None
    observed: ObservedRuntime | None
    mismatch_codes: tuple[str, ...] = ()
    reason_code: str = ""
    last_known_good_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ReconcileReplay:
    scope: RuntimeApiProjectScope
    plan: ReconcilePlan
    claim: FencedClaim
    outcome: ReconcileOutcome
    observed: ObservedRuntime | None
    last_known_good_digest: str | None


class ProjectRuntimeReadPort(Protocol):
    """Read-only projection of project revisions, materialization, and bindings."""

    async def get_latest(self, *, project_id: str) -> ProjectRuntimeRevision | None: ...

    async def read_materialization(self, *, scope: RuntimeApiProjectScope, revision_id: str) -> MaterializationReadSnapshot: ...

    async def read_bindings(self, *, scope: RuntimeApiProjectScope, revision_id: str) -> RuntimeBindingsReadSnapshot: ...


class ObservedRuntimePort(Protocol):
    async def observe(self, *, scope: RuntimeApiProjectScope) -> ObservedRuntime: ...


class ReconcileStorePort(Protocol):
    """CAS/fence store; keys are unique per exact scope, even when desired digest changes."""

    async def load(self, *, scope: RuntimeApiProjectScope) -> ReconcileRecord: ...

    async def find_replay(
        self, *, scope: RuntimeApiProjectScope, idempotency_key: str,
    ) -> ReconcileReplay | None: ...

    async def claim(
        self, *, scope: RuntimeApiProjectScope, expected_generation: int,
        desired_digest: str, plan_digest: str, idempotency_key: str,
        recovery_digest: str | None, observed_digest: str,
    ) -> ClaimResult: ...

    async def finish(
        self, *, claim: FencedClaim, outcome: ReconcileOutcome,
        applied_digest: str | None, last_known_good: DesiredRuntimeSnapshot | None,
        applied_observed: ObservedRuntime | None,
    ) -> bool: ...


class RuntimeReconcilePort(Protocol):
    """Fenced operation port; revalidate source evidence and observed identity at execution time.

    Rollback must be bound to the last-known-good digest carried by the claim;
    adapters must not infer project scope from observed Pod labels.
    A positive quarantine receipt means containment was confirmed for the exact
    claimed scope, even when the quarantined Pod identity is ambiguous.
    """

    async def execute(
        self, *, action: ReconcileAction, scope: RuntimeApiProjectScope,
        snapshot: DesiredRuntimeSnapshot, observed: ObservedRuntime | None,
        claim: FencedClaim,
    ) -> RuntimeExecutionReceipt: ...


class ProjectRuntimeReconcileService:
    """Build and reconcile one exact project runtime using injected ports only."""

    def __init__(
        self,
        *,
        source: ProjectRuntimeReadPort,
        observed: ObservedRuntimePort,
        store: ReconcileStorePort,
        runtime: RuntimeReconcilePort,
    ) -> None:
        self._source = source
        self._observed = observed
        self._store = store
        self._runtime = runtime

    async def desired_snapshot(self, *, scope: RuntimeApiProjectScope) -> DesiredRuntimeSnapshot:
        if not isinstance(scope, RuntimeApiProjectScope):
            raise ValueError("exact_project_scope_required")
        revision = await self._source.get_latest(project_id=scope.project_id)
        if revision is None:
            raise ValueError("project_runtime_revision_missing")
        materialization = await self._source.read_materialization(scope=scope, revision_id=revision.revision_id)
        bindings = await self._source.read_bindings(scope=scope, revision_id=revision.revision_id)
        return DesiredRuntimeSnapshot.build(
            scope=scope, revision=revision, materialization=materialization, bindings=bindings
        )

    async def reconcile(
        self, *, scope: RuntimeApiProjectScope, expected_generation: int, idempotency_key: str
    ) -> ReconcileResult:
        if not isinstance(scope, RuntimeApiProjectScope):
            return self._blocked("exact_project_scope_required")
        if (not isinstance(expected_generation, int) or isinstance(expected_generation, bool)
                or expected_generation < 0):
            return self._blocked("expected_generation_invalid")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            return self._blocked("idempotency_key_missing")
        try:
            desired = await self.desired_snapshot(scope=scope)
        except Exception as exc:
            reason = "desired_runtime_evidence_invalid" if isinstance(exc, ValueError) else "runtime_evidence_read_failed"
            return self._blocked(reason)
        try:
            replay = await self._store.find_replay(
                scope=scope, idempotency_key=idempotency_key,
            )
        except Exception:
            return self._blocked("idempotency_replay_read_failed", desired=desired)
        if replay is not None:
            if not _valid_replay(replay, scope, idempotency_key, desired.digest):
                return self._blocked("idempotency_replay_binding_mismatch", desired=desired)
            return ReconcileResult(
                replay.outcome, replay.plan, desired, replay.observed,
                replay.plan.mismatch_codes, "idempotent_replay", replay.last_known_good_digest,
            )
        try:
            observed = await self._observed.observe(scope=scope)
            record = await self._store.load(scope=scope)
        except Exception:
            return self._blocked("runtime_evidence_read_failed", desired=desired)
        if not isinstance(observed, ObservedRuntime) or observed.scope != scope:
            return self._blocked("observed_runtime_scope_mismatch", desired=desired)
        if not isinstance(record, ReconcileRecord) or record.scope != scope:
            return self._blocked("reconcile_record_scope_mismatch", desired=desired, observed=observed)
        if record.generation != expected_generation:
            return self._result(ReconcileOutcome.CONFLICT, None, desired, observed, (), "generation_conflict", record)

        recovery_digest = record.last_known_good.digest if record.last_known_good else None
        expected_pod_uid = (
            record.applied_observed.pod_uid
            if record.applied_digest == desired.digest and record.applied_observed is not None
            else desired.expected_pod_uid
        )
        action, mismatches = compare_runtime(desired, observed, expected_pod_uid=expected_pod_uid)
        plan = _make_plan(scope, action, expected_generation, desired.digest, mismatches, idempotency_key,
                          recovery_digest=recovery_digest, observed_digest=observed_runtime_digest(observed))
        if action is ReconcileAction.NOOP and record.applied_digest == desired.digest:
            return self._result(ReconcileOutcome.NOOP, plan, desired, observed, (), "already_healthy", record)
        if action is ReconcileAction.NOOP:
            action = ReconcileAction.APPLY
            mismatches = ("healthy_runtime_not_recorded_as_applied",)
            plan = _make_plan(scope, action, expected_generation, desired.digest, mismatches, idempotency_key,
                              recovery_digest=recovery_digest, observed_digest=observed_runtime_digest(observed))
        try:
            claim_result = await self._store.claim(
                scope=scope, expected_generation=expected_generation, desired_digest=desired.digest,
                plan_digest=plan.plan_digest, idempotency_key=idempotency_key,
                recovery_digest=plan.recovery_digest, observed_digest=plan.observed_digest,
            )
        except Exception:
            return self._result(ReconcileOutcome.BLOCKED, plan, desired, observed, mismatches,
                                "reconcile_claim_failed", record)
        if not isinstance(claim_result, ClaimResult):
            return self._result(ReconcileOutcome.BLOCKED, plan, desired, observed, mismatches,
                                "reconcile_claim_invalid", record)
        if claim_result.status is ClaimStatus.REPLAY:
            if not _valid_claim(claim_result.claim, plan):
                return self._result(ReconcileOutcome.CONFLICT, plan, desired, observed, mismatches,
                                    "replay_claim_binding_mismatch", record)
            latest = await self._safe_load(scope, record)
            outcome = (
                ReconcileOutcome.NOOP if latest.applied_digest == desired.digest
                else ReconcileOutcome.QUARANTINED if latest.last_outcome is ReconcileOutcome.QUARANTINED
                else ReconcileOutcome.CONFLICT
            )
            return self._result(outcome, plan, desired, observed, mismatches, "idempotent_replay", latest)
        if claim_result.status is not ClaimStatus.CLAIMED or not _valid_claim(claim_result.claim, plan):
            return self._result(ReconcileOutcome.CONFLICT, plan, desired, observed, mismatches,
                                _safe_reason(claim_result.reason_code or "fence_conflict"), record)
        claim = claim_result.claim
        if claim is None:
            return self._result(ReconcileOutcome.CONFLICT, plan, desired, observed, mismatches,
                                "fenced_claim_missing", record)

        receipt = await self._execute(action, desired, observed, claim)
        if action is ReconcileAction.QUARANTINE:
            if _receipt_bound(receipt, action, desired, claim, observed) and receipt.accepted:
                saved = await self._finish(claim, ReconcileOutcome.QUARANTINED, None,
                                           record.last_known_good, None)
                if saved:
                    return self._result(
                        ReconcileOutcome.QUARANTINED, plan, desired, receipt.observed,
                        mismatches, "observed_runtime_quarantined",
                        ReconcileRecord(scope, claim.generation, record.last_known_good, None, None,
                                        ReconcileOutcome.QUARANTINED),
                    )
                return self._result(ReconcileOutcome.CONFLICT, plan, desired, receipt.observed,
                                    mismatches, "stale_generation_fence", record)
            saved = await self._finish(claim, ReconcileOutcome.BLOCKED, None, record.last_known_good, None)
            if not saved:
                return self._result(ReconcileOutcome.CONFLICT, plan, desired, receipt.observed,
                                    mismatches, "stale_generation_fence", record)
            return self._result(ReconcileOutcome.BLOCKED, plan, desired, receipt.observed,
                                mismatches, "quarantine_unconfirmed",
                                ReconcileRecord(scope, claim.generation, record.last_known_good, None, None,
                                                ReconcileOutcome.BLOCKED))
        receipt_is_bound = _receipt_bound(receipt, action, desired, claim, observed)
        recovery_observed = observed
        if not receipt_is_bound or not receipt.accepted or not receipt.healthy:
            failure_codes = (
                ("runtime_receipt_binding_mismatch",) if not receipt_is_bound else ()
            ) + ("runtime_health_mismatch",)
            mismatches = tuple(dict.fromkeys(mismatches + failure_codes))
        if _receipt_healthy(receipt, action, desired, claim, observed):
            saved = await self._finish(claim, ReconcileOutcome.APPLIED if action is ReconcileAction.APPLY else ReconcileOutcome.REPLACED,
                                       desired.digest, desired, receipt.observed)
            if not saved:
                return self._result(ReconcileOutcome.CONFLICT, plan, desired, receipt.observed, (),
                                    "stale_generation_fence", record)
            return self._result(ReconcileOutcome.APPLIED if action is ReconcileAction.APPLY else ReconcileOutcome.REPLACED,
                                plan, desired, receipt.observed, mismatches, "runtime_healthy", 
                                ReconcileRecord(scope, claim.generation, desired, desired.digest, receipt.observed,
                                                ReconcileOutcome.APPLIED if action is ReconcileAction.APPLY
                                                else ReconcileOutcome.REPLACED))

        previous = record.last_known_good
        if previous is not None and _lkg_valid(previous, scope):
            rollback = await self._execute(ReconcileAction.ROLLBACK, previous, recovery_observed, claim)
            if _receipt_healthy(rollback, ReconcileAction.ROLLBACK, previous, claim, recovery_observed):
                saved = await self._finish(claim, ReconcileOutcome.ROLLED_BACK, previous.digest, previous,
                                           rollback.observed)
                if saved:
                    return self._result(ReconcileOutcome.ROLLED_BACK, plan, desired, rollback.observed,
                                        mismatches, "last_known_good_restored",
                                        ReconcileRecord(scope, claim.generation, previous, previous.digest,
                                                        rollback.observed, ReconcileOutcome.ROLLED_BACK))
                return self._result(ReconcileOutcome.CONFLICT, plan, desired, rollback.observed,
                                    mismatches, "stale_generation_fence", record)
        quarantine = await self._execute(ReconcileAction.QUARANTINE, desired, recovery_observed, claim)
        if _receipt_bound(quarantine, ReconcileAction.QUARANTINE, desired, claim, recovery_observed) and quarantine.accepted:
            saved = await self._finish(claim, ReconcileOutcome.QUARANTINED, None, previous, None)
            if saved:
                return self._result(ReconcileOutcome.QUARANTINED, plan, desired, quarantine.observed,
                                    mismatches, "runtime_unhealthy_quarantined",
                                    ReconcileRecord(scope, claim.generation, previous,
                                                    previous.digest if previous else None, None,
                                                    ReconcileOutcome.QUARANTINED))
        saved = await self._finish(claim, ReconcileOutcome.BLOCKED, None, previous, None)
        if not saved:
            return self._result(ReconcileOutcome.CONFLICT, plan, desired, receipt.observed,
                                mismatches, "stale_generation_fence", record)
        return self._result(ReconcileOutcome.BLOCKED, plan, desired, receipt.observed, mismatches,
                            "runtime_unhealthy_and_recovery_unconfirmed",
                            ReconcileRecord(scope, claim.generation, previous, None, None, ReconcileOutcome.BLOCKED))

    async def _execute(
        self, action: ReconcileAction, snapshot: DesiredRuntimeSnapshot,
        observed: ObservedRuntime | None, claim: FencedClaim,
    ) -> RuntimeExecutionReceipt:
        try:
            result = await self._runtime.execute(
                action=action, scope=claim.scope, snapshot=snapshot, observed=observed, claim=claim
            )
        except Exception:
            return RuntimeExecutionReceipt(claim.scope, action, claim.fence_token, claim.generation,
                                           snapshot.digest, False, False, None, "runtime_execution_failed",
                                           claim.observed_digest)
        if not isinstance(result, RuntimeExecutionReceipt):
            return RuntimeExecutionReceipt(claim.scope, action, claim.fence_token, claim.generation,
                                           snapshot.digest, False, False, None, "runtime_receipt_invalid",
                                           claim.observed_digest)
        return result

    async def _finish(
        self, claim: FencedClaim, outcome: ReconcileOutcome,
        applied_digest: str | None, last_known_good: DesiredRuntimeSnapshot | None,
        applied_observed: ObservedRuntime | None,
    ) -> bool:
        try:
            return await self._store.finish(
                claim=claim, outcome=outcome, applied_digest=applied_digest,
                last_known_good=last_known_good, applied_observed=applied_observed,
            ) is True
        except Exception:
            return False

    async def _safe_load(self, scope: RuntimeApiProjectScope, fallback: ReconcileRecord) -> ReconcileRecord:
        try:
            record = await self._store.load(scope=scope)
            return record if isinstance(record, ReconcileRecord) and record.scope == scope else fallback
        except Exception:
            return fallback

    @staticmethod
    def _blocked(
        reason: str, *, desired: DesiredRuntimeSnapshot | None = None,
        observed: ObservedRuntime | None = None,
    ) -> ReconcileResult:
        return ReconcileResult(ReconcileOutcome.BLOCKED, None, desired, observed, (), reason)

    @staticmethod
    def _result(
        outcome: ReconcileOutcome, plan: ReconcilePlan | None, desired: DesiredRuntimeSnapshot | None,
        observed: ObservedRuntime | None, mismatches: tuple[str, ...], reason: str,
        record: ReconcileRecord,
    ) -> ReconcileResult:
        return ReconcileResult(outcome, plan, desired, observed, mismatches, reason,
                               record.last_known_good.digest if record.last_known_good else None)


def compare_runtime(
    desired: DesiredRuntimeSnapshot, observed: ObservedRuntime, *, expected_pod_uid: str | None = None,
) -> tuple[ReconcileAction, tuple[str, ...]]:
    """Compare owner identity first, then runtime/config/workspace health."""
    if observed.scope != desired.scope or observed.presence is PodPresence.UNKNOWN:
        return ReconcileAction.QUARANTINE, ("observed_scope_or_presence_unknown",)
    if observed.presence is PodPresence.ABSENT:
        return ReconcileAction.APPLY, ("pod_absent",)
    if observed.project_id != desired.scope.project_id or observed.pod_id != desired.scope.pod_id:
        return ReconcileAction.QUARANTINE, ("observed_pod_owner_mismatch",)
    if not isinstance(observed.pod_uid, str) or not observed.pod_uid.strip():
        return ReconcileAction.QUARANTINE, ("observed_pod_uid_missing",)
    mismatch: list[str] = []
    expected_uid = expected_pod_uid or desired.expected_pod_uid
    if expected_uid and expected_uid != observed.pod_uid:
        mismatch.append("pod_uid_mismatch")
    if observed.revision_id != desired.revision_id:
        mismatch.append("runtime_revision_mismatch")
    if observed.config_digest != desired.config_digest:
        mismatch.append("runtime_config_mismatch")
    if observed.workspace_digest != desired.workspace_digest:
        mismatch.append("workspace_digest_mismatch")
    if observed.workspace_generation != desired.hydrate_generation:
        mismatch.append("workspace_generation_mismatch")
    if observed.runtime_image_digest != desired.runtime_image_digest:
        mismatch.append("runtime_image_mismatch")
    if observed.healthy is not True:
        mismatch.append("runtime_health_mismatch")
    if not mismatch:
        return ReconcileAction.NOOP, ()
    return ReconcileAction.REPLACE, tuple(mismatch)


def _make_plan(
    scope: RuntimeApiProjectScope, action: ReconcileAction, expected_generation: int,
    desired_digest: str, mismatches: tuple[str, ...], idempotency_key: str,
    *, recovery_digest: str | None = None, observed_digest: str,
) -> ReconcilePlan:
    next_generation = expected_generation if action is ReconcileAction.NOOP else expected_generation + 1
    digest = canonical_digest({
        "scope": _scope_canonical(scope), "action": action.value,
        "expected_generation": expected_generation, "next_generation": next_generation,
        "desired_digest": desired_digest, "mismatches": mismatches,
        "idempotency_key": idempotency_key, "recovery_digest": recovery_digest,
        "observed_digest": observed_digest,
    })
    return ReconcilePlan(scope, action, expected_generation, next_generation, desired_digest,
                         mismatches, idempotency_key, recovery_digest, observed_digest, digest)


def _valid_claim(claim: FencedClaim | None, plan: ReconcilePlan) -> bool:
    return bool(
        isinstance(claim, FencedClaim) and claim.scope == plan.scope
        and claim.expected_generation == plan.expected_generation
        and isinstance(claim.generation, int) and not isinstance(claim.generation, bool)
        and claim.generation == plan.next_generation
        and isinstance(claim.fence_token, str) and claim.fence_token.strip()
        and claim.desired_digest == plan.desired_digest and claim.plan_digest == plan.plan_digest
        and claim.idempotency_key == plan.idempotency_key and claim.recovery_digest == plan.recovery_digest
        and claim.observed_digest == plan.observed_digest
    )


def _valid_replay(
    replay: ReconcileReplay, scope: RuntimeApiProjectScope,
    idempotency_key: str, desired_digest: str,
) -> bool:
    if (not isinstance(replay, ReconcileReplay)
            or not isinstance(replay.plan, ReconcilePlan)
            or not isinstance(replay.claim, FencedClaim)
            or replay.scope != scope or replay.plan.scope != scope):
        return False
    if (replay.plan.idempotency_key != idempotency_key or replay.plan.desired_digest != desired_digest
            or replay.claim.idempotency_key != idempotency_key):
        return False
    if not _valid_claim(replay.claim, replay.plan):
        return False
    try:
        expected_plan = _make_plan(
            scope, replay.plan.action, replay.plan.expected_generation, desired_digest,
            replay.plan.mismatch_codes, idempotency_key,
            recovery_digest=replay.plan.recovery_digest, observed_digest=replay.plan.observed_digest,
        )
        return (
            replay.plan == expected_plan
            and isinstance(replay.outcome, ReconcileOutcome)
            and replay.outcome in {
                ReconcileOutcome.APPLIED, ReconcileOutcome.REPLACED, ReconcileOutcome.ROLLED_BACK,
                ReconcileOutcome.QUARANTINED, ReconcileOutcome.BLOCKED,
            }
            and (replay.observed is None or replay.observed.scope == scope)
            and (replay.last_known_good_digest is None or bool(_SHA256.fullmatch(replay.last_known_good_digest)))
        )
    except Exception:
        return False


def _receipt_bound(
    receipt: RuntimeExecutionReceipt, action: ReconcileAction,
    snapshot: DesiredRuntimeSnapshot, claim: FencedClaim, observed: ObservedRuntime | None,
) -> bool:
    target_digest = claim.recovery_digest if action is ReconcileAction.ROLLBACK else claim.desired_digest
    compared_digest = observed_runtime_digest(observed) if observed is not None else None
    return bool(
        isinstance(receipt, RuntimeExecutionReceipt) and receipt.scope == claim.scope
        and receipt.action is action and receipt.fence_token == claim.fence_token
        and receipt.generation == claim.generation and receipt.snapshot_digest == snapshot.digest
        and target_digest == snapshot.digest
        and compared_digest == claim.observed_digest
        and receipt.compared_observation_digest == compared_digest
    )


def observed_runtime_digest(observed: ObservedRuntime) -> str:
    return canonical_digest({
        "scope": _scope_canonical(observed.scope),
        "presence": observed.presence.value,
        "project_id": observed.project_id,
        "pod_id": observed.pod_id,
        "pod_uid": observed.pod_uid,
        "revision_id": observed.revision_id,
        "config_digest": observed.config_digest,
        "workspace_digest": observed.workspace_digest,
        "workspace_generation": observed.workspace_generation,
        "runtime_image_digest": observed.runtime_image_digest,
        "healthy": observed.healthy,
    })


def _receipt_healthy(
    receipt: RuntimeExecutionReceipt, action: ReconcileAction,
    snapshot: DesiredRuntimeSnapshot, claim: FencedClaim, observed: ObservedRuntime | None,
) -> bool:
    if not _receipt_bound(receipt, action, snapshot, claim, observed) or not receipt.accepted or not receipt.healthy:
        return False
    if action is ReconcileAction.QUARANTINE or receipt.observed is None:
        return False
    observed = receipt.observed
    if observed.scope != snapshot.scope or observed.presence is not PodPresence.PRESENT:
        return False
    if (observed.project_id != snapshot.scope.project_id or observed.pod_id != snapshot.scope.pod_id
            or not observed.pod_uid or observed.revision_id != snapshot.revision_id
            or observed.config_digest != snapshot.config_digest or observed.workspace_digest != snapshot.workspace_digest
            or observed.workspace_generation != snapshot.hydrate_generation
            or observed.runtime_image_digest != snapshot.runtime_image_digest or observed.healthy is not True):
        return False
    return True


def _lkg_valid(snapshot: DesiredRuntimeSnapshot, scope: RuntimeApiProjectScope) -> bool:
    return (
        isinstance(snapshot, DesiredRuntimeSnapshot)
        and snapshot.scope == scope
        and bool(_SHA256.fullmatch(snapshot.runtime_image_digest))
        and bool(_SHA256.fullmatch(snapshot.digest))
        and snapshot.digest == canonical_digest(snapshot.canonical())
    )


def _scope_canonical(scope: RuntimeApiProjectScope) -> dict[str, str]:
    return {
        "project_id": scope.project_id,
        "company_id": scope.company_id,
        "cabinet_id": scope.cabinet_id,
        "pod_id": scope.pod_id,
        "resource_key": scope.resource_key,
    }


def _safe_reason(reason: str) -> str:
    if not isinstance(reason, str):
        return "runtime_reconcile_blocked"
    candidate = reason.strip().lower().replace("-", "_")
    return candidate if re.fullmatch(r"[a-z][a-z0-9_.]{0,95}", candidate) else "runtime_reconcile_blocked"


__all__ = [
    "ClaimResult", "ClaimStatus", "ConfigProvenance", "DesiredRuntimeSnapshot", "FencedClaim",
    "MaterializationReadSnapshot", "ModuleBindingEvidence", "ObservedRuntime", "ObservedRuntimePort",
    "PodPresence", "ProjectRuntimeReadPort", "ProjectRuntimeReconcileService", "ProviderBindingEvidence",
    "ReconcileAction", "ReconcileOutcome", "ReconcilePlan", "ReconcileRecord", "ReconcileResult",
    "ReconcileReplay", "ReconcileStorePort", "RuntimeBindingsReadSnapshot", "RuntimeExecutionReceipt", "RuntimeReconcilePort",
    "SourceVersionEvidence", "compare_runtime", "observed_runtime_digest",
]
