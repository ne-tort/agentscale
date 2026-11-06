from __future__ import annotations

from dataclasses import dataclass

from .analyzer import analyze_owner_inventory
from .contracts import (
    CabinetReference,
    EmptyOwnerRows,
    EmptyReferences,
    FindingCode,
    OwnerReference,
    OwnerRow,
    ProjectReference,
)


def _codes(report, row_key="r1"):
    finding = next(item for item in report.findings if item.row_key == row_key)
    return {item.code for item in finding.evidence}


def test_clean_row_is_candidate_without_rewriting_id():
    row = OwnerRow("r1", "t1", "owner-1", "cab-1", "proj-1")
    refs = EmptyReferences(
        owners=[OwnerReference("owner-1", "t1")],
        cabinets=[CabinetReference("cab-1", "t1")],
        projects=[ProjectReference("proj-1", "t1", "cab-1")],
    )

    report = analyze_owner_inventory(EmptyOwnerRows([row]), refs)

    assert report.findings[0].candidate == "owner-1"
    assert report.findings[0].quarantine is False
    assert FindingCode.SAFE_CANDIDATE in _codes(report)


def test_malformed_and_overlength_ids_are_quarantined_without_cast():
    values = [None, " owner", "x" * 41, "x" * 81]
    rows = [OwnerRow(f"r{i}", "t1", value) for i, value in enumerate(values)]
    refs = EmptyReferences(owners=[OwnerReference(value, "t1") for value in values if value is not None])

    report = analyze_owner_inventory(EmptyOwnerRows(rows), refs)

    assert FindingCode.MALFORMED_ID in _codes(report, "r0")
    assert FindingCode.MALFORMED_ID in _codes(report, "r1")
    assert FindingCode.OVERLENGTH_ID in _codes(report, "r2")
    assert FindingCode.OVERLENGTH_ID in _codes(report, "r3")
    assert all(item.candidate is None for item in report.findings)
    assert report.findings[2].owner_id == values[2]


def test_exact_string_40_is_candidate_without_cast_or_truncation():
    owner_id = "x" * 40
    row = OwnerRow("r1", "t1", owner_id)
    report = analyze_owner_inventory(
        EmptyOwnerRows([row]), EmptyReferences(owners=[OwnerReference(owner_id, "t1")])
    )

    assert report.findings[0].candidate == owner_id
    assert len(report.findings[0].candidate) == 40
    assert FindingCode.OVERLENGTH_ID not in _codes(report)


def test_orphan_deleted_and_cross_tenant_references_are_evidence():
    rows = [
        OwnerRow("orphan", "t1", "missing"),
        OwnerRow("deleted", "t1", "gone"),
        OwnerRow("foreign", "t1", "other"),
    ]
    refs = EmptyReferences(
        owners=[OwnerReference("gone", "t1", deleted=True), OwnerReference("other", "t2")]
    )

    report = analyze_owner_inventory(EmptyOwnerRows(rows), refs)

    assert FindingCode.ORPHAN_OWNER in _codes(report, "orphan")
    assert FindingCode.DELETED_OWNER in _codes(report, "deleted")
    assert FindingCode.CROSS_TENANT_OWNER in _codes(report, "foreign")
    assert all(item.quarantine for item in report.findings)


def test_cabinet_project_compatibility_and_tenant_checks():
    row = OwnerRow("r1", "t1", "owner", "cab-1", "proj-1")
    refs = EmptyReferences(
        owners=[OwnerReference("owner", "t1")],
        cabinets=[CabinetReference("cab-1", "t2")],
        projects=[ProjectReference("proj-1", "t1", "cab-2")],
    )

    report = analyze_owner_inventory(EmptyOwnerRows([row]), refs)
    codes = _codes(report)

    assert FindingCode.CROSS_TENANT_CABINET in codes
    assert FindingCode.CABINET_PROJECT_MISMATCH in codes
    assert FindingCode.QUARANTINE_REQUIRED in codes


@dataclass
class ReadOnlyRows:
    rows: tuple[OwnerRow, ...]
    writes: int = 0

    def iter_owner_rows(self):
        return iter(self.rows)

    def write(self, *_args, **_kwargs):
        self.writes += 1
        raise AssertionError("analyzer must not write")


def test_ports_are_read_only_and_no_positive_write_is_attempted():
    source = ReadOnlyRows((OwnerRow("r1", "t1", "owner"),))
    report = analyze_owner_inventory(source, EmptyReferences(owners=[OwnerReference("owner", "t1")]))

    assert report.summary.candidates == 1
    assert source.writes == 0


def test_duplicate_registry_references_are_ambiguous_and_quarantined():
    row = OwnerRow("r1", "t1", "owner", "cab", "project")
    refs = EmptyReferences(
        owners=[OwnerReference("owner", "t1"), OwnerReference("owner", "t1")],
        cabinets=[CabinetReference("cab", "t1"), CabinetReference("cab", "t1")],
        projects=[ProjectReference("project", "t1", "cab"), ProjectReference("project", "t1", "cab")],
    )

    report = analyze_owner_inventory(EmptyOwnerRows([row]), refs)

    codes = _codes(report)
    assert FindingCode.AMBIGUOUS_OWNER in codes
    assert FindingCode.AMBIGUOUS_CABINET in codes
    assert FindingCode.AMBIGUOUS_PROJECT in codes
    assert report.findings[0].candidate is None
    assert report.findings[0].quarantine is True


def test_report_summary_by_code_is_immutable():
    row = OwnerRow("r1", "t1", "owner")
    report = analyze_owner_inventory(
        EmptyOwnerRows([row]), EmptyReferences(owners=[OwnerReference("owner", "t1")])
    )

    try:
        report.summary.by_code[FindingCode.SAFE_CANDIDATE] = 99
    except TypeError:
        pass
    else:
        raise AssertionError("summary counts must be immutable")

    assert report.summary.by_code[FindingCode.SAFE_CANDIDATE] == 1
