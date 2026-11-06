"""Pure read-only owner/type inventory analyzer."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from types import MappingProxyType
from typing import Any

from .contracts import (
    CabinetReference,
    FindingCode,
    InventoryReport,
    InventorySummary,
    OwnerFinding,
    OwnerReference,
    OwnerRow,
    ProjectReference,
    ReferencePort,
    Evidence,
    OwnerRowPort,
)

TARGET_MAX_LENGTH = 40
_SOURCE_MAX_LENGTH = 80


def analyze_owner_inventory(rows: OwnerRowPort, references: ReferencePort) -> InventoryReport:
    """Classify rows without connecting to storage or mutating supplied objects.

    A candidate is emitted only for a well-formed, live, same-tenant owner ID
    with compatible optional cabinet/project references. No value is truncated
    or cast; the original object is retained in every finding.
    """
    owner_refs = tuple(references.iter_owners())
    cabinet_refs = tuple(references.iter_cabinets())
    project_refs = tuple(references.iter_projects())
    owner_by_id = _index(owner_refs, lambda item: item.owner_id)
    cabinet_by_id = _index(cabinet_refs, lambda item: item.cabinet_id)
    project_by_id = _index(project_refs, lambda item: item.project_id)

    findings: list[OwnerFinding] = []
    counts: Counter[FindingCode] = Counter()
    for row in rows.iter_owner_rows():
        evidence = _inspect_row(row, owner_by_id, cabinet_by_id, project_by_id)
        quarantine = bool(evidence)
        candidate = None if quarantine else _candidate(row.owner_id)
        if candidate is not None:
            evidence = (Evidence(FindingCode.SAFE_CANDIDATE, "ID is safe for explicit review", "owner_id"),)
        for item in evidence:
            counts[item.code] += 1
        findings.append(OwnerFinding(row.row_key, row.tenant_id, row.owner_id, candidate, tuple(evidence), quarantine))

    summary = InventorySummary(
        rows=len(findings),
        candidates=sum(item.candidate is not None for item in findings),
        quarantined=sum(item.quarantine for item in findings),
        by_code=MappingProxyType(dict(counts)),
    )
    return InventoryReport(tuple(findings), summary)


def _index(items: Iterable[Any], key: Any) -> dict[object, list[Any]]:
    result: dict[object, list[Any]] = {}
    for item in items:
        value = key(item)
        try:
            result.setdefault(value, []).append(item)
        except TypeError:
            continue
    return result


def _lookup(index: dict[object, list[Any]], value: object) -> list[Any]:
    try:
        return index.get(value, [])
    except TypeError:
        return []


def _inspect_row(row: OwnerRow, owners: dict[object, list[OwnerReference]], cabinets: dict[object, list[CabinetReference]], projects: dict[object, list[ProjectReference]]) -> list[Evidence]:
    evidence: list[Evidence] = []
    owner_id = row.owner_id
    if not _is_valid_id(owner_id):
        evidence.append(Evidence(FindingCode.MALFORMED_ID, "owner_id must be a non-empty string", "owner_id"))
    elif len(owner_id) > _SOURCE_MAX_LENGTH:
        evidence.append(Evidence(FindingCode.OVERLENGTH_ID, "owner_id exceeds String(80)", "owner_id"))
    elif len(owner_id) > TARGET_MAX_LENGTH:
        evidence.append(Evidence(FindingCode.OVERLENGTH_ID, "owner_id cannot fit String(40) without truncation", "owner_id"))

    owner_matches = _lookup(owners, owner_id)
    if not owner_matches:
        evidence.append(Evidence(FindingCode.ORPHAN_OWNER, "owner reference is absent", "owner_id", _display(owner_id)))
    else:
        if len(owner_matches) > 1:
            evidence.append(Evidence(FindingCode.AMBIGUOUS_OWNER, "owner reference is not unique", "owner_id", _display(owner_id)))
        _check_tenant_and_deleted(evidence, owner_matches, row.tenant_id, FindingCode.CROSS_TENANT_OWNER, FindingCode.DELETED_OWNER, "owner_id")

    if row.cabinet_id is not None:
        cabinet_matches = _lookup(cabinets, row.cabinet_id)
        if not cabinet_matches:
            evidence.append(Evidence(FindingCode.ORPHAN_CABINET, "cabinet reference is absent", "cabinet_id", _display(row.cabinet_id)))
        else:
            if len(cabinet_matches) > 1:
                evidence.append(Evidence(FindingCode.AMBIGUOUS_CABINET, "cabinet reference is not unique", "cabinet_id", _display(row.cabinet_id)))
            _check_tenant_and_deleted(evidence, cabinet_matches, row.tenant_id, FindingCode.CROSS_TENANT_CABINET, FindingCode.DELETED_CABINET, "cabinet_id")

    if row.project_id is not None:
        project_matches = _lookup(projects, row.project_id)
        if not project_matches:
            evidence.append(Evidence(FindingCode.ORPHAN_PROJECT, "project reference is absent", "project_id", _display(row.project_id)))
        else:
            if len(project_matches) > 1:
                evidence.append(Evidence(FindingCode.AMBIGUOUS_PROJECT, "project reference is not unique", "project_id", _display(row.project_id)))
            _check_tenant_and_deleted(evidence, project_matches, row.tenant_id, FindingCode.CROSS_TENANT_PROJECT, FindingCode.DELETED_PROJECT, "project_id")
            if row.cabinet_id is not None and not any(item.cabinet_id == row.cabinet_id for item in project_matches):
                evidence.append(Evidence(FindingCode.CABINET_PROJECT_MISMATCH, "project is not assigned to row cabinet", "project_id", _display(row.project_id)))

    if evidence:
        evidence.append(Evidence(FindingCode.QUARANTINE_REQUIRED, "row requires manual quarantine review"))
    return evidence


def _check_tenant_and_deleted(evidence: list[Evidence], matches: Iterable[Any], tenant_id: str, cross_code: FindingCode, deleted_code: FindingCode, field: str) -> None:
    matches = tuple(matches)
    if not any(item.tenant_id == tenant_id for item in matches):
        evidence.append(Evidence(cross_code, "reference belongs to another tenant", field))
    if any(item.deleted for item in matches):
        evidence.append(Evidence(deleted_code, "reference is deleted", field))


def _is_valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _candidate(value: object) -> str | None:
    return value if _is_valid_id(value) and len(value) <= TARGET_MAX_LENGTH else None


def _display(value: object) -> str:
    return value if isinstance(value, str) else repr(value)
