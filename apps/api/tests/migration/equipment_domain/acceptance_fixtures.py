"""MIG-EQ-02 acceptance fixtures; intentionally not executed during freeze."""

from decimal import Decimal

from prodavan.application.migration.equipment_domain.domain import (
    CandidateGroup,
    DomainEvidence,
    EquipmentTender,
    RequestLine,
    TypedRequest,
)

EVIDENCE = DomainEvidence("tenant-a", "owner-a", "source-a", "supplier-a", "v1")
INCOMPLETE_EVIDENCE = DomainEvidence(None, None, None, "supplier-a", None)
REQUEST = TypedRequest("request-a", EVIDENCE, "Pump replacement", "RUB", "2026-11-01T14:00:00Z")
LINE = RequestLine("line-a", "request-a", EVIDENCE, 0, "Pump", Decimal("2"), "pcs", "PN-42")
GROUP = CandidateGroup("group-a", "line-a", EVIDENCE, 0, "exact", "PN-42")
TENDER_SAME_PN_A = EquipmentTender(
    "tender-a", "line-a", "group-a", EVIDENCE, 0, "supplier-a", "source-a",
    "record-a", "PN-42", "Pump A", "RUB", Decimal("100"), Decimal("4"),
)
TENDER_SAME_PN_B = EquipmentTender(
    "tender-b", "line-a", DomainEvidence("tenant-a", "owner-a", "source-b", "supplier-b", "v1"),
    1, "supplier-b", "source-b", "record-a", "PN-42", "Pump B", "RUB", Decimal("90"), Decimal("5"),
)

CASES = (
    "typed request, line, candidate group, tender, and budget-facing projection preserve explicit scope",
    "incomplete owner or source evidence blocks positive projection, selection, and export",
    "JSONB relationship arrays backfill into ordinals preserving original relationship order",
    "JSONB scalar, object, empty IDs, or missing owner identity fail closed without inferred links",
    "duplicate IDs in legacy JSONB retain order and are not silently deduplicated",
    "line linked to unknown request or differing tenant/owner/source is scoped out",
    "candidate group linked to unknown or cross-scope line is scoped out",
    "tender linked to absent group, another line, or another owner is scoped out",
    "same P/N from different supplier/source identities remains two distinct tenders",
    "duplicate supplier/source/source-record identity is rejected as ambiguous",
    "projection ordering and digest are stable under equivalent input permutations",
    "blocked projection exposes no positive requests, lines, candidate groups, or tenders",
    "selection requires a human actor, reason, existing line and matching tender",
    "selection cannot cross line, tenant, owner, supplier, or source scope",
    "procurement export requires explicit human selection and actor consistency",
    "export contains only selected tender IDs and no implicit best-offer decision",
    "fixtures and acceptance declarations remain unexecuted until coordinator lifts freeze",
)
