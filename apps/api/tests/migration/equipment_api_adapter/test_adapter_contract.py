"""MIG-EQ-API-02 acceptance tests; intentionally not executed."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from prodavan.application.migration.equipment_api_adapter.adapter import EquipmentApiAdapter
from prodavan.application.migration.equipment_api_adapter.contracts import (
    AdapterError,
    AdapterErrorCode,
    CandidateQuery,
    CatalogOfferQuery,
    CatalogQuery,
    ChatScopedCatalogSnapshot,
    ChatScopedEquipmentSnapshot,
    ChatScopedHumanSelection,
    CreateCandidateGroupCommand,
    CreateRequestCommand,
    EquipmentActorKind,
    EquipmentApiScope,
    ExportProcurementCommand,
    OfferQuery,
    Page,
    ProcurementQuery,
    RefreshCatalogCommand,
    RequestQuery,
    SelectOfferCommand,
)
from prodavan.application.migration.equipment_domain.domain import HumanSelection
from prodavan.application.migration.equipment_domain.domain import build_projection
from prodavan.application.migration.equipment_domain.service import EquipmentDomainSnapshot
from prodavan.application.migration.equipment_domain.transactions import (
    EquipmentTransactionResult,
    EquipmentTransactionStatus,
    validate_transaction_result,
)
from prodavan.application.migration.equipment_ingestion.search_projection import ProjectionScope
from prodavan.application.migration.equipment_ingestion.ingestion import (
    CatalogOffer,
    CatalogSnapshot,
    FreshnessState,
)
from prodavan.application.migration.equipment_domain.domain import (
    CandidateGroup,
    DomainEvidence,
    DomainScope,
    EquipmentTender,
    OfferFreshness,
    RequestLine,
    TypedRequest,
)

SCOPE = DomainScope("tenant-a", "owner-a", "project-a")
API_SCOPE = EquipmentApiScope("tenant-a", "owner-a", "project-a", "chat-a", "employee-a",
                              EquipmentActorKind.HUMAN)
EVIDENCE = DomainEvidence("tenant-a", "owner-a", "source-a", "supplier-a", "v1", "project-a")
REQUEST = TypedRequest("request-a", EVIDENCE, "Pump request", "RUB", "2026-11-01T12:00:00Z")
LINE_A = RequestLine("line-a", "request-a", EVIDENCE, 0, "Pump A", Decimal("2"), "pcs", "PN-A")
LINE_B = RequestLine("line-b", "request-a", EVIDENCE, 1, "Pump B", Decimal("1"), "pcs", "PN-B")
GROUP_A = CandidateGroup("group-a", "line-a", EVIDENCE, 0, "exact", "PN-A", ("ALT-A",))
GROUP_B = CandidateGroup("group-b", "line-b", EVIDENCE, 0, "analog", "PN-B")
TENDER_A = EquipmentTender("tender-a", "line-a", "group-a", EVIDENCE, 0, "supplier-a", "source-a",
    "record-a", "PN-A", "Pump A offer", "RUB", Decimal("10"), Decimal("10"), OfferFreshness.FRESH)
TENDER_B = EquipmentTender("tender-b", "line-b", "group-b", EVIDENCE, 0, "supplier-a", "source-a",
    "record-b", "PN-B", "Pump B offer", "RUB", Decimal("5"), Decimal("10"), OfferFreshness.FRESH)
SNAPSHOT = EquipmentDomainSnapshot(SCOPE, "revision-9", (REQUEST,), (LINE_A, LINE_B),
                                   (GROUP_A, GROUP_B), (TENDER_A, TENDER_B))
PROJECTION_DIGEST = build_projection(requests=SNAPSHOT.requests, lines=SNAPSHOT.lines,
    candidate_groups=SNAPSHOT.candidate_groups, tenders=SNAPSHOT.tenders,
    expected_scope=SCOPE, source_revision=SNAPSHOT.source_revision).contract.input_digest


class ReadPort:
    def __init__(self):
        self.scope_calls = []
        self.selections: tuple[ChatScopedHumanSelection, ...] = ()
        self.snapshot = ChatScopedEquipmentSnapshot(SCOPE, "chat-a", SNAPSHOT)
        self.catalog: tuple[ChatScopedCatalogSnapshot, ...] = ()
        self.projection = None

    async def request_snapshot(self, *, scope, chat_id):
        self.scope_calls.append((scope, chat_id))
        return self.snapshot

    async def catalog_snapshots(self, *, scope, chat_id, source_id=None):
        self.scope_calls.append((scope, chat_id))
        return tuple(row for row in self.catalog
                     if source_id is None or row.snapshot.source_id == source_id)

    async def search_projection(self, *, scope, chat_id):
        self.scope_calls.append((scope, chat_id))
        return self.projection

    async def human_selections(self, *, scope, chat_id):
        self.scope_calls.append((scope, chat_id))
        return self.selections


@pytest.mark.asyncio
async def test_read_queries_are_project_and_chat_scoped_and_return_typed_dtos():
    port = ReadPort()
    adapter = EquipmentApiAdapter(source=port)
    result = await adapter.candidates(CandidateQuery(API_SCOPE, Page(limit=1)))

    assert result.items[0]["candidate_group_id"] == "group-a"
    assert result.source_revision == "revision-9"
    assert result.snapshot_digest.startswith("sha256:")
    assert port.scope_calls == [(SCOPE, "chat-a")]


@pytest.mark.asyncio
async def test_cursor_is_snapshot_filter_and_scope_bound():
    port = ReadPort()
    adapter = EquipmentApiAdapter(source=port)
    first = await adapter.offers(OfferQuery(API_SCOPE, Page(limit=1)))
    assert first.next_cursor is not None
    second = await adapter.offers(OfferQuery(API_SCOPE, Page(limit=1, cursor=first.next_cursor)))
    assert second.items[0]["tender_id"] == "tender-b"
    with pytest.raises(AdapterError) as error:
        await adapter.offers(OfferQuery(API_SCOPE, Page(limit=1, cursor=first.next_cursor), line_id="line-a"))
    assert error.value.code is AdapterErrorCode.INVALID_CURSOR
    with pytest.raises(AdapterError) as error:
        await adapter.offers(OfferQuery(API_SCOPE, Page(limit=1, cursor="not-a-cursor")))
    assert error.value.code is AdapterErrorCode.INVALID_CURSOR

    other_chat = EquipmentApiScope("tenant-a", "owner-a", "project-a", "chat-b", "employee-a")
    changed = ReadPort()
    changed.snapshot = ChatScopedEquipmentSnapshot(SCOPE, "chat-b", SNAPSHOT)
    with pytest.raises(AdapterError) as error:
        await EquipmentApiAdapter(source=changed).offers(
            OfferQuery(other_chat, Page(limit=1, cursor=first.next_cursor)))
    assert error.value.code is AdapterErrorCode.INVALID_CURSOR


@pytest.mark.asyncio
async def test_legacy_rows_are_read_only_previews_with_stable_provenance_and_loss_map():
    result = await EquipmentApiAdapter(source=ReadPort()).candidates(CandidateQuery(API_SCOPE))
    shadow = result.compatibility_rows[0]
    assert shadow.table_slug == "found_groups"
    assert shadow.session_id == "chat-a" and shadow.body["session_id"] == "chat-a"
    assert shadow.read_only is True
    assert shadow.exact_legacy_equivalent is False
    assert shadow.legacy_row_id is None
    assert shadow.legacy_id_status == "unknown"
    assert shadow.row_id.startswith("shadow:")
    assert "aliases_hash" in shadow.omitted_fields
    assert shadow.provenance["snapshot_digest"] == result.snapshot_digest


@pytest.mark.asyncio
async def test_offer_shadow_preserves_source_supplier_and_freshness_without_best_selection():
    result = await EquipmentApiAdapter(source=ReadPort()).offers(OfferQuery(API_SCOPE))
    row = result.compatibility_rows[0]
    assert row.body["source_id"] == "source-a"
    assert row.body["source_record_id"] == "record-a"
    assert row.body["migration_domain_supplier_id"] == "supplier-a"
    assert row.body["is_best"] is False
    assert row.body["is_stale"] is False
    assert "price in converted RUB" in row.omitted_fields

    unknown = replace(TENDER_A, freshness=OfferFreshness.UNKNOWN)
    port = ReadPort()
    port.snapshot = ChatScopedEquipmentSnapshot(SCOPE, "chat-a",
        replace(SNAPSHOT, tenders=(unknown, TENDER_B)))
    unknown_row = (await EquipmentApiAdapter(source=port).offers(OfferQuery(API_SCOPE))).compatibility_rows[0]
    assert unknown_row.body["freshness"] == "unknown"
    assert unknown_row.body["is_stale"] is None


@pytest.mark.asyncio
async def test_procurement_query_uses_only_explicit_current_human_selections():
    port = ReadPort()
    adapter = EquipmentApiAdapter(source=port)
    assert (await adapter.procurement(ProcurementQuery(API_SCOPE))).items == ()

    selection = HumanSelection(
        actor_id="employee-a", line_id="line-a", tender_id="tender-a", reason="checked",
        selection_id="selection-a", projection_digest=PROJECTION_DIGEST,
        tenant_id="tenant-a", owner_id="owner-a", project_id="project-a",
        supplier_id="supplier-a", source_id="source-a", source_record_id="record-a",
        source_schema_version="v1")
    port.selections = (ChatScopedHumanSelection("chat-a", selection),)
    result = await adapter.procurement(ProcurementQuery(API_SCOPE))
    assert result.items[0]["selected_count"] == 1
    assert result.items[0]["selected_total"] == "20"
    assert result.compatibility_rows[0].read_only is True
    assert result.compatibility_rows[0].body["migration_shadow"] is True
    assert "project-wide cross-chat aggregation" in result.compatibility_rows[0].omitted_fields

    port.selections = (ChatScopedHumanSelection("chat-a", replace(selection, actor_id="agent-a")),)
    with pytest.raises(AdapterError) as error:
        await adapter.procurement(ProcurementQuery(API_SCOPE))
    assert error.value.code is AdapterErrorCode.HUMAN_SELECTION_REQUIRED


@pytest.mark.asyncio
async def test_cross_scope_source_rows_fail_closed():
    port = ReadPort()
    with pytest.raises(AdapterError) as error:
        ChatScopedEquipmentSnapshot(SCOPE, "chat-a",
            replace(SNAPSHOT, scope=DomainScope("tenant-other", "owner-a", "project-a")))
    assert error.value.code is AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH

    port = ReadPort()
    port.snapshot = ChatScopedEquipmentSnapshot(SCOPE, "chat-other", SNAPSHOT)
    with pytest.raises(AdapterError) as error:
        await EquipmentApiAdapter(source=port).candidates(CandidateQuery(API_SCOPE))
    assert error.value.code is AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH


@pytest.mark.asyncio
async def test_catalog_snapshot_and_offer_reads_preserve_immutable_provenance():
    port = ReadPort()
    observed = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    offer = CatalogOffer("offer-a", "snapshot-a", "supplier-a", "source-a", "row-a", observed,
        "RUB", "10", "in_stock", {"title": "Pump"}, "sha256:" + "b" * 64, "artifact-a")
    snapshot = CatalogSnapshot("snapshot-a", "tenant-a", "owner-a", "source-a", "supplier-a",
        "run-a", "artifact-a", "v1", "revision-1", observed, FreshnessState.FRESH,
        (offer,), "sha256:" + "c" * 64, True, project_id="project-a")
    port.catalog = (ChatScopedCatalogSnapshot("chat-a", snapshot),)

    metadata = await EquipmentApiAdapter(source=port).catalog(CatalogQuery(API_SCOPE))
    assert metadata.items[0]["artifact_id"] == "artifact-a"
    assert (metadata.items[0]["tenant_id"], metadata.items[0]["owner_id"],
            metadata.items[0]["project_id"]) == ("tenant-a", "owner-a", "project-a")
    rows = await EquipmentApiAdapter(source=port).catalog_offers(CatalogOfferQuery(API_SCOPE))
    assert rows.items[0]["artifact_id"] == "artifact-a"
    assert rows.items[0]["schema_version"] == "v1"
    assert (rows.items[0]["tenant_id"], rows.items[0]["owner_id"],
            rows.items[0]["project_id"]) == ("tenant-a", "owner-a", "project-a")
    assert rows.items[0]["provenance_digest"] == "sha256:" + "b" * 64

    port.catalog = (ChatScopedCatalogSnapshot("chat-a", replace(snapshot, tenant_id="tenant-other")),)
    with pytest.raises(AdapterError) as error:
        await EquipmentApiAdapter(source=port).catalog(CatalogQuery(API_SCOPE))
    assert error.value.code is AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH


@pytest.mark.asyncio
async def test_catalog_offer_requires_accepted_snapshot_and_exact_chat():
    port = ReadPort()
    with pytest.raises(AdapterError) as error:
        await EquipmentApiAdapter(source=port).catalog_offers(
            CatalogOfferQuery(API_SCOPE, snapshot_id="missing"))
    assert error.value.code is AdapterErrorCode.SNAPSHOT_UNAVAILABLE

    rejected = CatalogSnapshot("snapshot-a", "tenant-a", "owner-a", "source-a", "supplier-a",
        "run-a", "artifact-a", "v1", "revision-1", datetime(2026, 11, 1, tzinfo=UTC),
        FreshnessState.FRESH, (), "sha256:" + "c" * 64, False, project_id="project-a")
    port.catalog = (ChatScopedCatalogSnapshot("chat-b", rejected),)
    with pytest.raises(AdapterError) as error:
        await EquipmentApiAdapter(source=port).catalog_offers(CatalogOfferQuery(API_SCOPE))
    assert error.value.code is AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH


def test_command_intents_are_stable_but_hard_disabled_and_list_missing_gates():
    command = RefreshCatalogCommand(API_SCOPE, "refresh-1", "source-a", "snapshot-a")
    adapter = EquipmentApiAdapter(source=ReadPort())
    first = adapter.prepare_command(command)
    replay = adapter.prepare_command(command)
    assert first.intent is not None and first.intent.envelope is not None
    assert first.idempotency_key == replay.idempotency_key
    assert first.payload_digest == replay.payload_digest
    assert first.accepted is False and first.write_enabled is False
    assert first.intent.state.value == "blocked"
    assert "canonical_tenant_and_owner_resolution" in first.required_gates
    assert "accepted_source_snapshot_binding" in first.required_gates
    assert "idempotent_durable_job_queue" in first.required_gates
    assert "canonical_equipment_registry_digest_missing" in first.required_gates
    assert "expected_source_revision_missing" in first.required_gates
    assert "expected_snapshot_digest_missing" in first.required_gates
    assert "canonical_equipment_registry_evidence" in first.required_gates
    assert "compare_and_swap_write_adapter" in first.required_gates
    assert first.intent.envelope.version == "1"
    assert first.intent.envelope.actor_id == "employee-a"
    assert first.intent.envelope.payload_digest == first.payload_digest
    assert first.intent.envelope.expected_source_revision is None
    assert first.intent.envelope.canonical_registry_digest is None
    changed_payload = adapter.prepare_command(
        RefreshCatalogCommand(API_SCOPE, "refresh-1", "source-a", "snapshot-b"))
    assert changed_payload.idempotency_key == first.idempotency_key
    assert changed_payload.payload_digest != first.payload_digest


def test_selection_and_export_intents_require_human_and_snapshot_gates():
    adapter = EquipmentApiAdapter(source=ReadPort())
    selection = adapter.prepare_command(SelectOfferCommand(
        API_SCOPE, "select-1", "selection-1", "line-a", "tender-a", "reviewed",
        human_approval_id="approval-1", expected_projection_digest=PROJECTION_DIGEST,
        expected_source_revision="revision-9"))
    export = adapter.prepare_command(ExportProcurementCommand(
        API_SCOPE, "export-1", "export-1", human_approval_id="approval-export-1",
        expected_source_revision="revision-9", expected_selection_digest=PROJECTION_DIGEST))
    assert selection.write_enabled is export.write_enabled is False
    assert selection.intent is not None and selection.intent.envelope is not None
    assert export.intent is not None and export.intent.envelope is not None
    assert "authenticated_human_selection" in selection.required_gates
    assert "human_approval_evidence" in selection.required_gates
    assert selection.intent.envelope.payload["human_approval_id"] == "approval-1"
    assert "canonical_equipment_registry_digest_missing" in selection.required_gates
    assert "expected_snapshot_digest_missing" not in selection.required_gates
    assert "human_export_approval" in export.required_gates
    assert "current_human_selection_set" in export.required_gates
    assert "expected_selection_digest_missing" not in export.required_gates


@pytest.mark.asyncio
async def test_execute_command_never_calls_legacy_mutation_or_job_intent_port():
    adapter = EquipmentApiAdapter(source=ReadPort())
    result = await adapter.execute_command(CreateCandidateGroupCommand(
        API_SCOPE, "group-command", "group-new", "line-a", "exact", "PN-A"))
    assert result.accepted is False and result.write_enabled is False
    assert result.reason_code == "equipment_write_gate_unproven"


@pytest.mark.asyncio
async def test_domain_queries_are_stable_for_permuted_source_rows():
    shuffled = replace(SNAPSHOT, requests=(REQUEST,), lines=(LINE_B, LINE_A),
                       candidate_groups=(GROUP_B, GROUP_A), tenders=(TENDER_B, TENDER_A))
    first = await EquipmentApiAdapter(source=ReadPort()).offers(OfferQuery(API_SCOPE, Page(limit=1)))
    port = ReadPort()
    port.snapshot = ChatScopedEquipmentSnapshot(SCOPE, "chat-a", shuffled)
    second = await EquipmentApiAdapter(source=port).offers(OfferQuery(API_SCOPE, Page(limit=1)))
    assert first.items == second.items
    assert first.next_cursor == second.next_cursor
    assert first.snapshot_digest == second.snapshot_digest
    first_page = await EquipmentApiAdapter(source=ReadPort()).requests(
        RequestQuery(API_SCOPE))
    assert first_page.items[0]["lines"][0]["line_id"] == "line-a"


def test_selection_intent_requires_human_actor_approval_and_cas_evidence():
    non_human = EquipmentApiScope("tenant-a", "owner-a", "project-a", "chat-a", "agent-a",
                                  EquipmentActorKind.AGENT)
    with pytest.raises(AdapterError) as error:
        SelectOfferCommand(non_human, "select-1", "selection-1", "line-a", "tender-a", "reviewed",
                           human_approval_id="approval-1", expected_projection_digest=PROJECTION_DIGEST,
                           expected_source_revision="revision-9")
    assert error.value.code is AdapterErrorCode.HUMAN_SELECTION_REQUIRED

    with pytest.raises(AdapterError):
        SelectOfferCommand(API_SCOPE, "select-1", "selection-1", "line-a", "tender-a", "reviewed",
                           human_approval_id=None, expected_projection_digest=PROJECTION_DIGEST,
                           expected_source_revision="revision-9")


def test_malformed_cursors_and_queries_fail_closed():
    with pytest.raises(AdapterError) as error:
        Page(limit=True)
    assert error.value.code is AdapterErrorCode.INVALID_QUERY
    with pytest.raises(AdapterError) as error:
        Page(cursor="%%%")
    assert error.value.code is AdapterErrorCode.INVALID_CURSOR


@pytest.mark.asyncio
async def test_catalog_query_order_filter_revision_and_offer_freshness_are_stable():
    observed = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    def make_snapshot(snapshot_id, source_id, freshness):
        artifact_id = "artifact-" + snapshot_id
        offer = CatalogOffer("offer-" + snapshot_id, snapshot_id, "supplier-a", source_id,
            "row-" + snapshot_id, observed, "RUB", "10", "in_stock", {"title": "Pump"},
            "sha256:" + "b" * 64, artifact_id)
        return CatalogSnapshot(snapshot_id, "tenant-a", "owner-a", source_id, "supplier-a",
            "run-" + snapshot_id, artifact_id, "v1", "revision-" + snapshot_id, observed,
            freshness, (offer,), "sha256:" + "c" * 64, True, project_id="project-a")

    snapshot_a = make_snapshot("snapshot-a", "source-a", FreshnessState.FRESH)
    snapshot_z = make_snapshot("snapshot-z", "source-z", FreshnessState.STALE)
    port = ReadPort()
    port.catalog = (ChatScopedCatalogSnapshot("chat-a", snapshot_z),
                    ChatScopedCatalogSnapshot("chat-a", snapshot_a))
    adapter = EquipmentApiAdapter(source=port)
    metadata = await adapter.catalog(CatalogQuery(API_SCOPE, Page(limit=1)))
    assert metadata.items[0]["snapshot_id"] == "snapshot-a"
    filtered = await adapter.catalog(CatalogQuery(API_SCOPE, source_id="source-z"))
    assert [row["snapshot_id"] for row in filtered.items] == ["snapshot-z"]
    offers = await adapter.catalog_offers(CatalogOfferQuery(API_SCOPE))
    by_snapshot = {row["snapshot_id"]: row["freshness"] for row in offers.items}
    assert by_snapshot == {"snapshot-a": "fresh", "snapshot-z": "stale"}


@pytest.mark.asyncio
async def test_non_human_actor_cannot_read_procurement_selection_projection():
    service_scope = EquipmentApiScope("tenant-a", "owner-a", "project-a", "chat-a", "svc-a",
                                     EquipmentActorKind.SERVICE)
    with pytest.raises(AdapterError) as error:
        await EquipmentApiAdapter(source=ReadPort()).procurement(ProcurementQuery(service_scope))
    assert error.value.code is AdapterErrorCode.HUMAN_SELECTION_REQUIRED


@pytest.mark.asyncio
async def test_request_and_candidate_reads_keep_request_line_and_group_membership():
    result = await EquipmentApiAdapter(source=ReadPort()).requests(RequestQuery(API_SCOPE))
    assert result.items[0]["request_id"] == "request-a"
    assert [line["line_id"] for line in result.items[0]["lines"]] == ["line-a", "line-b"]
    assert result.items[0]["source"]["supplier_id"] == "supplier-a"

    candidates = await EquipmentApiAdapter(source=ReadPort()).candidates(CandidateQuery(API_SCOPE))
    assert [(row["candidate_group_id"], row["line_id"])
            for row in candidates.items] == [("group-a", "line-a"), ("group-b", "line-b")]
    assert candidates.items[0]["source"] == {
        "source_id": "source-a", "supplier_id": "supplier-a", "schema_version": "v1"}


@pytest.mark.asyncio
async def test_owner_mismatch_from_scoped_read_fails_closed():
    port = ReadPort()
    foreign_scope = DomainScope("tenant-a", "owner-other", "project-a")
    foreign_snapshot = replace(SNAPSHOT, scope=foreign_scope)
    port.snapshot = ChatScopedEquipmentSnapshot(foreign_scope, "chat-a", foreign_snapshot)
    with pytest.raises(AdapterError) as error:
        await EquipmentApiAdapter(source=port).requests(RequestQuery(API_SCOPE))
    assert error.value.code is AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH


@pytest.mark.asyncio
async def test_same_part_number_from_distinct_suppliers_and_sources_stays_separate():
    second_evidence = replace(EVIDENCE, supplier_id="supplier-b", source_id="source-b")
    second = EquipmentTender(
        "tender-other-supplier", "line-a", "group-a", second_evidence, 1,
        "supplier-b", "source-b", "record-a", "PN-A", "Other pump offer", "RUB",
        Decimal("9"), Decimal("10"), OfferFreshness.FRESH)
    port = ReadPort()
    port.snapshot = ChatScopedEquipmentSnapshot(
        SCOPE, "chat-a", replace(SNAPSHOT, tenders=(TENDER_A, second, TENDER_B)))

    result = await EquipmentApiAdapter(source=port).offers(OfferQuery(API_SCOPE))
    same_pn = [row for row in result.items if row["part_number"] == "PN-A"]
    assert {(row["supplier_id"], row["source_id"], row["source_record_id"])
            for row in same_pn} == {
                ("supplier-a", "source-a", "record-a"),
                ("supplier-b", "source-b", "record-a"),
            }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "freshness,available_quantity",
    [(OfferFreshness.STALE, Decimal("10")),
     (OfferFreshness.FRESH, None),
     (OfferFreshness.FRESH, Decimal("1"))],
)
async def test_procurement_rejects_stale_unavailable_or_insufficient_offer(
    freshness, available_quantity
):
    port = ReadPort()
    tender = replace(TENDER_A, freshness=freshness, available_quantity=available_quantity)
    port.snapshot = ChatScopedEquipmentSnapshot(SCOPE, "chat-a", replace(SNAPSHOT, tenders=(tender,)))
    current_digest = build_projection(
        requests=SNAPSHOT.requests,
        lines=SNAPSHOT.lines,
        candidate_groups=SNAPSHOT.candidate_groups,
        tenders=(tender,),
        expected_scope=SCOPE,
        source_revision=SNAPSHOT.source_revision,
    ).contract.input_digest
    selection = HumanSelection(
        actor_id="employee-a", line_id="line-a", tender_id="tender-a", reason="checked",
        selection_id="selection-a", projection_digest=current_digest,
        tenant_id="tenant-a", owner_id="owner-a", project_id="project-a",
        supplier_id="supplier-a", source_id="source-a", source_record_id="record-a",
        source_schema_version="v1")
    port.selections = (ChatScopedHumanSelection("chat-a", selection),)

    with pytest.raises(AdapterError) as error:
        await EquipmentApiAdapter(source=port).procurement(ProcurementQuery(API_SCOPE))
    assert error.value.code is AdapterErrorCode.HUMAN_SELECTION_REQUIRED


@pytest.mark.asyncio
async def test_selection_scope_and_offer_provenance_must_match_current_offer():
    port = ReadPort()
    foreign = HumanSelection(
        actor_id="employee-a", line_id="line-a", tender_id="tender-a", reason="checked",
        selection_id="selection-a", projection_digest=PROJECTION_DIGEST,
        tenant_id="tenant-a", owner_id="owner-other", project_id="project-a",
        supplier_id="supplier-a", source_id="source-a", source_record_id="record-a",
        source_schema_version="v1")
    port.selections = (ChatScopedHumanSelection("chat-a", foreign),)
    with pytest.raises(AdapterError) as error:
        await EquipmentApiAdapter(source=port).procurement(ProcurementQuery(API_SCOPE))
    assert error.value.code is AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH

    mismatch = replace(foreign, owner_id="owner-a", supplier_id="supplier-b")
    port.selections = (ChatScopedHumanSelection("chat-a", mismatch),)
    with pytest.raises(AdapterError) as error:
        await EquipmentApiAdapter(source=port).procurement(ProcurementQuery(API_SCOPE))
    assert error.value.code is AdapterErrorCode.HUMAN_SELECTION_REQUIRED


@pytest.mark.asyncio
async def test_offer_outside_selected_line_candidate_group_is_not_projected():
    port = ReadPort()
    misplaced = replace(TENDER_A, group_id="group-b")
    port.snapshot = ChatScopedEquipmentSnapshot(
        SCOPE, "chat-a", replace(SNAPSHOT, tenders=(misplaced, TENDER_B)))

    with pytest.raises(AdapterError) as error:
        await EquipmentApiAdapter(source=port).offers(OfferQuery(API_SCOPE))
    assert error.value.code is AdapterErrorCode.INCOMPLETE_CANONICAL_EVIDENCE


@pytest.mark.asyncio
async def test_request_group_write_and_export_commands_stay_blocked_without_adapter():
    adapter = EquipmentApiAdapter(source=ReadPort())
    commands = (
        CreateRequestCommand(API_SCOPE, "request-create", "request-new", "New request",
                             "RUB", "2026-11-01T12:00:00Z"),
        CreateCandidateGroupCommand(API_SCOPE, "group-create", "group-new", "line-a",
                                    "exact", "PN-A"),
        SelectOfferCommand(
            API_SCOPE, "select-blocked", "selection-blocked", "line-a", "tender-a", "reviewed",
            human_approval_id="approval-select", expected_projection_digest=PROJECTION_DIGEST,
            expected_source_revision="revision-9"),
        ExportProcurementCommand(
            API_SCOPE, "export-command", "export-a", human_approval_id="approval-a",
            expected_source_revision="revision-9", expected_selection_digest=PROJECTION_DIGEST),
    )
    for command in commands:
        result = await adapter.execute_command(command)
        assert result.accepted is False
        assert result.write_enabled is False
        assert result.intent is not None and result.intent.state.value == "blocked"
        assert not hasattr(result, "file_ref")


@pytest.mark.asyncio
async def test_unknown_transaction_commit_is_never_reported_as_success():
    key = "sha256:" + "a" * 64
    payload = "sha256:" + "b" * 64
    result = EquipmentTransactionResult(
        status=EquipmentTransactionStatus.COMMIT_UNKNOWN,
        operation_id="export-unknown",
        idempotency_key=key,
        payload_digest=payload,
        scope=SCOPE,
        source_revision="revision-9",
        source_generation=1,
        reason_code="reconcile_required",
        durable=False,
    )
    with pytest.raises(RuntimeError, match="unknown"):
        validate_transaction_result(
            result=result,
            scope=SCOPE,
            idempotency_key=key,
            payload_digest=payload,
            source_revision="revision-9",
            source_generation=1,
        )


@pytest.mark.asyncio
async def test_unwired_export_adapter_returns_no_file_ref_or_success():
    adapter = EquipmentApiAdapter(source=ReadPort())
    command = ExportProcurementCommand(
        API_SCOPE, "export-unknown", "export-unknown", human_approval_id="approval-a",
        expected_source_revision="revision-9", expected_selection_digest=PROJECTION_DIGEST)
    result = await adapter.execute_command(command)

    assert result.accepted is False
    assert result.write_enabled is False
    assert result.reason_code == "equipment_write_gate_unproven"
    assert result.intent is not None and result.intent.state.value == "blocked"
    assert not hasattr(result, "file_ref")
