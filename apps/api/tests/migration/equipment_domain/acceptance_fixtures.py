"""MIG-EQ-02 acceptance fixtures; intentionally not executed during freeze."""

from decimal import Decimal

from prodavan.application.migration.equipment_domain.domain import (
    CandidateGroup,
    DomainEvidence,
    DomainScope,
    EquipmentTender,
    OfferFreshness,
    RequestLine,
    TypedRequest,
)
from prodavan.application.migration.equipment_domain.service import (
    AssociationBatch,
    DecisionActor,
    DecisionActorKind,
    EquipmentDomainSnapshot,
)

SCOPE = DomainScope("tenant-a", "owner-a", "project-a")
OTHER_PROJECT = DomainScope("tenant-a", "owner-a", "project-b")
EVIDENCE = DomainEvidence("tenant-a", "owner-a", "source-a", "supplier-a", "v1", "project-a")
EVIDENCE_B = DomainEvidence("tenant-a", "owner-a", "source-b", "supplier-b", "v1", "project-a")
INCOMPLETE_EVIDENCE = DomainEvidence(None, None, None, "supplier-a", None, None)
REQUEST = TypedRequest("request-a", EVIDENCE, "Pump replacement", "RUB", "2026-11-01T14:00:00Z")
LINE = RequestLine("line-a", "request-a", EVIDENCE, 0, "Pump", Decimal("2"), "pcs", "PN-42")
GROUP = CandidateGroup("group-a", "line-a", EVIDENCE, 0, "exact", "PN-42")
TENDER_SAME_PN_A = EquipmentTender(
    "tender-a", "line-a", "group-a", EVIDENCE, 0, "supplier-a", "source-a",
    "record-a", "PN-42", "Pump A", "RUB", Decimal("100"), Decimal("4"),
    freshness=OfferFreshness.FRESH,
)
TENDER_SAME_PN_B = EquipmentTender(
    "tender-b", "line-a", "group-a", EVIDENCE_B,
    1, "supplier-b", "source-b", "record-a", "PN-42", "Pump B", "RUB", Decimal("90"), Decimal("5"),
    freshness=OfferFreshness.FRESH,
)
STALE_TENDER = EquipmentTender(
    "tender-stale", "line-a", "group-a", EVIDENCE, 2, "supplier-a", "source-a",
    "record-stale", "PN-42", "Stale pump", "RUB", Decimal("80"), None,
    OfferFreshness.STALE, False,
)
ON_ORDER_TENDER = EquipmentTender(
    "tender-order", "line-a", "group-a", EVIDENCE, 3, "supplier-a", "source-a",
    "record-order", "PN-42", "Already ordered pump", "RUB", Decimal("70"), None,
    OfferFreshness.FRESH, True,
)
SNAPSHOT = EquipmentDomainSnapshot(SCOPE, "revision-7", (REQUEST,), (LINE,), (GROUP,),
                                   (TENDER_SAME_PN_A, TENDER_SAME_PN_B), source_generation=1)
HUMAN = DecisionActor("employee-a", DecisionActorKind.HUMAN, "tenant-a", "owner-a", "project-a")
AGENT = DecisionActor("agent-a", DecisionActorKind.AGENT, "tenant-a", "owner-a", "project-a")
ASSOCIATION_BATCH = AssociationBatch(SCOPE, "revision-7", (
    {"id": "line-a", "tenant_id": "tenant-a", "owner_id": "owner-a", "project_id": "project-a",
     "candidate_group_ids": ["group-z", "group-a", "group-z"]},
))

CASES = (
    "typed request, line, candidate group, tender, and budget-facing projection preserve explicit scope",
    "incomplete owner or source evidence blocks positive projection, selection, and export",
    "JSONB relationship arrays backfill into ordinals preserving original relationship order",
    "JSONB scalar, object, empty IDs, or missing owner identity fail closed without inferred links",
    "duplicate IDs in legacy JSONB retain order and are not silently deduplicated",
    "line linked to unknown request or differing tenant/owner/project is blocked",
    "candidate group linked to unknown or cross-scope line is scoped out",
    "tender linked to absent group, another line, or another project is blocked",
    "same P/N from different supplier/source identities remains two distinct tenders",
    "duplicate supplier/source/source-record identity within one group is rejected as ambiguous",
    "projection ordering and digest are stable under equivalent input permutations",
    "blocked projection exposes no positive requests, lines, candidate groups, or tenders",
    "selection requires a human actor, reason, existing line and matching tender",
    "selection cannot cross line, tenant, owner, supplier, or source scope",
    "procurement export requires explicit human selection and actor consistency",
    "export contains only selected tender IDs and no implicit best-offer decision",
    "fixtures and acceptance declarations remain unexecuted until coordinator lifts freeze",
    "stale and on-order tenders remain visible but cannot be selected or exported",
    "service workflow requires an authenticated human actor for decisions",
    "procurement export contains line, supplier, source and amount details",
    "application ports fence association append by exact source revision and scope",
    "source generation and projection digest fence selection and procurement transactions",
    "identical selection/export retries replay while changed payloads conflict",
    "commit-after-write uncertainty requires idempotency reconciliation, never success",
    "manual override, selected line membership and quantity-price totals remain auditable",
    "duplicate vendor/source-record and cross-tenant rows expose no positive projection",
)
