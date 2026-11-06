"""Deterministic tenant/definition/version/artifact parity for the M04E backfill.

The parity report is derived *only* from accepted, proof-bearing evidence rows for
one partition. It never reads the legacy tables and never re-derives identity from
a caller-supplied version ID: every version is reached through the accepted
definition record of the same partition.

The report is the comparison input for M05 import: two reports of the same
partition and source snapshot must be byte-identical, so a replayed page, a
resumed run and a repaired cursor all produce the same digest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Sequence

from .backfill import BackfillRecord, EvidenceStatus

_ALLOWED_LIFECYCLES = frozenset({"draft", "published"})

# Stable, redacted parity reason codes. They name a structural defect in the
# accepted evidence set; no row values, payload fragments or digests leak here.
PARITY_DEFINITION_MISSING = "parity_definition_missing"
PARITY_DEFINITION_DUPLICATE = "parity_definition_duplicate"
PARITY_VERSION_WITHOUT_DEFINITION = "parity_version_without_definition"
PARITY_MANIFEST_VERSION_DUPLICATE = "parity_manifest_version_duplicate"
PARITY_ARTIFACT_DIGEST_DUPLICATE = "parity_artifact_digest_duplicate"
PARITY_CURRENT_VERSION_MISSING = "parity_current_version_missing"
PARITY_CURRENT_VERSION_NOT_PUBLISHED = "parity_current_version_not_published"
PARITY_VERSION_PUBLISHED_NOT_CURRENT = "parity_version_published_not_current"
PARITY_LIFECYCLE_UNPROVEN = "parity_lifecycle_unproven"
PARITY_ARTIFACT_BYTES_UNVERIFIED = "parity_artifact_bytes_unverified"
PARITY_DEFINITION_IDENTITY_MISMATCH = "parity_definition_identity_mismatch"
PARITY_VERSION_FOREIGN_DEFINITION = "parity_version_foreign_definition"
PARITY_SOURCE_SNAPSHOT_UNBOUND = "parity_source_snapshot_unbound"

PARITY_REASON_CODES = (
    PARITY_DEFINITION_MISSING,
    PARITY_DEFINITION_DUPLICATE,
    PARITY_VERSION_WITHOUT_DEFINITION,
    PARITY_MANIFEST_VERSION_DUPLICATE,
    PARITY_ARTIFACT_DIGEST_DUPLICATE,
    PARITY_CURRENT_VERSION_MISSING,
    PARITY_CURRENT_VERSION_NOT_PUBLISHED,
    PARITY_VERSION_PUBLISHED_NOT_CURRENT,
    PARITY_LIFECYCLE_UNPROVEN,
    PARITY_ARTIFACT_BYTES_UNVERIFIED,
    PARITY_DEFINITION_IDENTITY_MISMATCH,
    PARITY_VERSION_FOREIGN_DEFINITION,
    PARITY_SOURCE_SNAPSHOT_UNBOUND,
)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class VersionParity:
    """One accepted version, keyed by its canonical manifest identity."""

    source_id: str
    manifest_version: str
    artifact_digest: str
    verified_artifact_digest: str | None
    lifecycle: str | None
    is_current: bool
    digest: str

    @property
    def artifact_bytes_verified(self) -> bool:
        return (
            self.verified_artifact_digest is not None
            and self.verified_artifact_digest == self.artifact_digest
        )

    def material(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "manifest_version": self.manifest_version,
            "artifact_digest": self.artifact_digest,
            "verified_artifact_digest": self.verified_artifact_digest,
            "lifecycle": self.lifecycle,
            "is_current": self.is_current,
        }


@dataclass(frozen=True, slots=True)
class DefinitionParity:
    """Definition identity plus every accepted version of that definition."""

    tenant_id: str
    definition_id: str
    app_id: str | None
    lifecycle: str | None
    current_version_id: str | None
    versions: tuple[VersionParity, ...]
    violations: tuple[str, ...]
    digest: str

    @property
    def version_count(self) -> int:
        return len(self.versions)

    @property
    def manifest_versions(self) -> tuple[str, ...]:
        return tuple(sorted(version.manifest_version for version in self.versions))

    @property
    def artifact_digests(self) -> tuple[str, ...]:
        return tuple(sorted(version.artifact_digest for version in self.versions))

    def material(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "definition_id": self.definition_id,
            "app_id": self.app_id,
            "lifecycle": self.lifecycle,
            "current_version_id": self.current_version_id,
            "versions": [version.material() for version in self.versions],
        }


@dataclass(frozen=True, slots=True)
class PartitionParityReport:
    """Comparable, deterministic view of one partition's accepted evidence."""

    tenant_id: str
    app_definition_id: str
    source_snapshot_digest: str
    definition: DefinitionParity | None
    violations: tuple[str, ...]
    digest: str

    @property
    def definition_count(self) -> int:
        return 0 if self.definition is None else 1

    @property
    def version_count(self) -> int:
        return 0 if self.definition is None else self.definition.version_count

    @property
    def artifact_count(self) -> int:
        return 0 if self.definition is None else len(set(self.definition.artifact_digests))

    @property
    def consistent(self) -> bool:
        return not self.violations

    def matches(self, other: PartitionParityReport) -> bool:
        return (
            isinstance(other, PartitionParityReport)
            and other.tenant_id == self.tenant_id
            and other.app_definition_id == self.app_definition_id
            and other.source_snapshot_digest == self.source_snapshot_digest
            and other.digest == self.digest
            and other.violations == self.violations
        )

    def material(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "app_definition_id": self.app_definition_id,
            "source_snapshot_digest": self.source_snapshot_digest,
            "definition": self.definition.material() if self.definition is not None else None,
            "violations": list(self.violations),
        }


def _accepted(records: Iterable[BackfillRecord], *, tenant_id: str, app_definition_id: str) -> tuple[BackfillRecord, ...]:
    return tuple(
        record for record in records
        if record.status is EvidenceStatus.ACCEPTED
        and record.tenant_id == tenant_id
        and record.app_definition_id == app_definition_id
    )


def build_partition_parity(
    *,
    tenant_id: str,
    app_definition_id: str,
    records: Sequence[BackfillRecord],
    source_snapshot_digest: str,
    strict: bool = True,
) -> PartitionParityReport:
    """Derive the partition parity report from accepted evidence only.

    ``strict`` additionally requires the lifecycle of every accepted row to be
    recorded and each accepted version's artifact bytes to be re-verified. A
    non-strict report is diagnostics only and must not gate the M05 handoff.
    """
    if not tenant_id.strip() or not app_definition_id.strip():
        raise ValueError("parity requires a trusted tenant and definition identity")
    if not source_snapshot_digest.startswith("sha256:") or len(source_snapshot_digest) != 71:
        raise ValueError("parity requires a sha256 source snapshot digest")

    accepted = _accepted(records, tenant_id=tenant_id, app_definition_id=app_definition_id)
    definitions = tuple(record for record in accepted if record.phase == "definition")
    versions = tuple(record for record in accepted if record.phase == "version")

    violations: list[str] = []
    for record in accepted:
        if record.source_snapshot_digest != source_snapshot_digest:
            violations.append(PARITY_SOURCE_SNAPSHOT_UNBOUND)
            break

    if not definitions:
        violations.append(PARITY_DEFINITION_MISSING)
    elif len(definitions) > 1:
        violations.append(PARITY_DEFINITION_DUPLICATE)
    if versions and not definitions:
        violations.append(PARITY_VERSION_WITHOUT_DEFINITION)

    if not definitions:
        material = {
            "tenant_id": tenant_id,
            "app_definition_id": app_definition_id,
            "source_snapshot_digest": source_snapshot_digest,
            "definition": None,
        }
        return PartitionParityReport(
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            source_snapshot_digest=source_snapshot_digest,
            definition=None,
            violations=tuple(dict.fromkeys(violations)),
            digest=_digest(material),
        )

    definition_record = definitions[0]
    identity = definition_record.canonical_identity
    app_id = identity[1] if len(identity) == 2 else None
    if len(identity) != 2 or identity[0] != tenant_id or not identity[1].strip():
        violations.append(PARITY_DEFINITION_IDENTITY_MISMATCH)

    lifecycle = definition_record.canonical_lifecycle
    current_version_id = definition_record.canonical_current_version_id
    if strict and lifecycle is None:
        violations.append(PARITY_LIFECYCLE_UNPROVEN)
    elif lifecycle is not None and lifecycle not in _ALLOWED_LIFECYCLES:
        violations.append(PARITY_LIFECYCLE_UNPROVEN)

    version_rows: list[VersionParity] = []
    seen_manifest: set[str] = set()
    seen_artifacts: set[str] = set()
    for record in sorted(versions, key=lambda row: (row.source_id, row.source_id)):
        version_identity = record.canonical_identity
        if len(version_identity) != 3 or version_identity[1] != app_definition_id:
            violations.append(PARITY_VERSION_FOREIGN_DEFINITION)
            continue
        manifest_version = version_identity[2]
        if not manifest_version.strip():
            violations.append(PARITY_VERSION_FOREIGN_DEFINITION)
            continue
        if manifest_version in seen_manifest:
            violations.append(PARITY_MANIFEST_VERSION_DUPLICATE)
        seen_manifest.add(manifest_version)
        artifact_digest = record.artifact_digest or ""
        if artifact_digest in seen_artifacts:
            violations.append(PARITY_ARTIFACT_DIGEST_DUPLICATE)
        seen_artifacts.add(artifact_digest)
        version_lifecycle = record.canonical_lifecycle
        if strict and version_lifecycle is None:
            violations.append(PARITY_LIFECYCLE_UNPROVEN)
        elif version_lifecycle is not None and version_lifecycle not in _ALLOWED_LIFECYCLES:
            violations.append(PARITY_LIFECYCLE_UNPROVEN)
        is_current = current_version_id is not None and current_version_id == record.source_id
        material = {
            "source_id": record.source_id,
            "manifest_version": manifest_version,
            "artifact_digest": artifact_digest,
            "verified_artifact_digest": record.verified_artifact_digest,
            "lifecycle": version_lifecycle,
            "is_current": is_current,
        }
        version_rows.append(VersionParity(
            source_id=record.source_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            verified_artifact_digest=record.verified_artifact_digest,
            lifecycle=version_lifecycle,
            is_current=is_current,
            digest=_digest(material),
        ))
        if strict and record.verified_artifact_digest != artifact_digest:
            violations.append(PARITY_ARTIFACT_BYTES_UNVERIFIED)

    ordered_versions = tuple(sorted(version_rows, key=lambda row: (row.manifest_version, row.source_id)))
    if current_version_id is not None:
        current = next((row for row in ordered_versions if row.is_current), None)
        if current is None:
            violations.append(PARITY_CURRENT_VERSION_MISSING)
        elif current.lifecycle != "published":
            violations.append(PARITY_CURRENT_VERSION_NOT_PUBLISHED)
    elif lifecycle == "published":
        violations.append(PARITY_CURRENT_VERSION_MISSING)
    for row in ordered_versions:
        if row.lifecycle == "published" and current_version_id is not None and not row.is_current:
            violations.append(PARITY_VERSION_PUBLISHED_NOT_CURRENT)

    definition = DefinitionParity(
        tenant_id=tenant_id,
        definition_id=app_definition_id,
        app_id=app_id,
        lifecycle=lifecycle,
        current_version_id=current_version_id,
        versions=ordered_versions,
        violations=(),
        digest="",
    )
    unique_violations = tuple(dict.fromkeys(violations))
    material = {
        "tenant_id": tenant_id,
        "app_definition_id": app_definition_id,
        "source_snapshot_digest": source_snapshot_digest,
        "definition": definition.material(),
        "violations": list(unique_violations),
    }
    definition_digest = _digest(definition.material())
    return PartitionParityReport(
        tenant_id=tenant_id,
        app_definition_id=app_definition_id,
        source_snapshot_digest=source_snapshot_digest,
        definition=DefinitionParity(
            tenant_id=definition.tenant_id,
            definition_id=definition.definition_id,
            app_id=definition.app_id,
            lifecycle=definition.lifecycle,
            current_version_id=definition.current_version_id,
            versions=definition.versions,
            violations=unique_violations,
            digest=definition_digest,
        ),
        violations=unique_violations,
        digest=_digest(material),
    )


__all__ = [
    "DefinitionParity",
    "PartitionParityReport",
    "VersionParity",
    "PARITY_REASON_CODES",
    "build_partition_parity",
]
