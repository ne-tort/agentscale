"""MIG-EQ-SEARCH-01 acceptance tests; intentionally not run during freeze."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.migration.equipment_ingestion.ingestion import (
    CatalogOffer,
    CatalogSnapshot,
    FreshnessState,
    RawArtifact,
    evidence_digest,
    offer_identity,
    offer_provenance_digest,
)
from prodavan.application.migration.equipment_ingestion.search_projection import (
    LastGoodProjectionPointer,
    ProjectionScope,
    PublicationDecision,
    SearchDocumentFields,
    SnapshotEvidence,
    build_search_projection,
    build_search_projection_from_port,
    plan_projection_publication,
)

NOW = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
SCOPE = ProjectionScope("tenant-a", "owner-a", "project-a")


class FieldAdapter:
    """Test adapter explicitly declares source attributes as searchable fields."""

    def fields_for(self, offer, *, snapshot):
        attrs = offer.attributes
        return SearchDocumentFields(
            title=attrs.get("title", ""),
            part_number=attrs.get("part_number"),
            brand=attrs.get("brand"),
            supplier=attrs.get("supplier"),
            lead_time=attrs.get("lead_time"),
        )


def accepted_evidence(
    *,
    snapshot_id: str = "snapshot-a",
    source_id: str = "source-a",
    supplier_id: str = "supplier-a",
    source_record_key: str = "row-a",
    observed_at: datetime = NOW,
    price: str | None = "125.00",
    currency: str = "USD",
    availability: str = "in_stock",
    freshness: FreshnessState = FreshnessState.FRESH,
    tenant_id: str = "tenant-a",
    owner_id: str = "owner-a",
    project_id: str | None = "project-a",
    title: str = "Power module",
    part_number: str = "PN-42",
    artifact_id: str | None = None,
) -> SnapshotEvidence:
    artifact_id = artifact_id or f"artifact-{snapshot_id}"
    offer = CatalogOffer(
        offer_id=offer_identity(source_id, supplier_id, source_record_key),
        snapshot_id=snapshot_id,
        supplier_id=supplier_id,
        source_id=source_id,
        source_record_key=source_record_key,
        observed_at=observed_at,
        currency=currency,
        price=price,
        availability=availability,
        attributes={
            "title": title,
            "part_number": part_number,
            "brand": "Acme",
            "supplier": "Acme Distribution",
            "lead_time": "2 days",
        },
        provenance_digest=offer_provenance_digest(
            source_id=source_id,
            supplier_id=supplier_id,
            source_record_key=source_record_key,
            artifact_id=artifact_id,
            observed_at=observed_at,
        ),
        artifact_id=artifact_id,
    )
    artifact = RawArtifact(
        artifact_id=artifact_id,
        source_id=source_id,
        run_id=f"run-{snapshot_id}",
        content_digest=evidence_digest({"raw": artifact_id}),
        byte_length=8,
        media_type="application/octet-stream",
        immutable=True,
        retained_reference=f"immutable://equipment-artifacts/{artifact_id}",
    )
    snapshot = CatalogSnapshot(
        snapshot_id=snapshot_id,
        tenant_id=tenant_id,
        owner_id=owner_id,
        source_id=source_id,
        supplier_id=supplier_id,
        run_id=artifact.run_id,
        artifact_id=artifact_id,
        schema_version="v1",
        source_revision="revision-1",
        observed_at=observed_at,
        freshness=freshness,
        offers=(offer,),
        content_digest=evidence_digest({"snapshot": snapshot_id, "offer": offer.offer_id}),
        accepted=True,
        project_id=project_id,
    )
    return SnapshotEvidence(snapshot=snapshot, artifact=artifact)


def build(evidence, *, expected_generation=0, rebuilt_at=NOW):
    return build_search_projection(
        evidence=evidence,
        scope=SCOPE,
        field_adapter=FieldAdapter(),
        projection_name="equipment-search",
        projection_version="1",
        expected_generation=expected_generation,
        rebuilt_at=rebuilt_at,
    )


@pytest.mark.asyncio
async def test_narrow_read_port_supplies_accepted_snapshot_evidence():
    evidence = (accepted_evidence(),)

    class EvidencePort:
        requested_scope = None

        async def accepted_snapshot_evidence(self, *, scope):
            self.requested_scope = scope
            return evidence

    port = EvidencePort()
    projection = await build_search_projection_from_port(
        source=port,
        scope=SCOPE,
        field_adapter=FieldAdapter(),
        projection_name="equipment-search",
        projection_version="1",
        expected_generation=0,
        rebuilt_at=NOW,
    )

    assert port.requested_scope == SCOPE
    assert projection.document_count == 1
    assert projection.documents[0].document_id == evidence[0].snapshot.offers[0].offer_id


def test_same_part_number_from_different_supplier_or_source_remains_separate():
    first = accepted_evidence(
        snapshot_id="snapshot-a",
        source_id="source-a",
        supplier_id="supplier-a",
        source_record_key="record-a",
    )
    different_supplier = accepted_evidence(
        snapshot_id="snapshot-b",
        source_id="source-a",
        supplier_id="supplier-b",
        source_record_key="record-b",
        artifact_id="artifact-b",
    )
    different_source = accepted_evidence(
        snapshot_id="snapshot-c",
        source_id="source-b",
        supplier_id="supplier-a",
        source_record_key="record-c",
        artifact_id="artifact-c",
    )

    projection = build((different_source, different_supplier, first))

    assert projection.document_count == 3
    assert len({item.document_id for item in projection.documents}) == 3
    assert {item.source["part_number"] for item in projection.documents} == {"PN-42"}
    assert {
        (item.source["source_id"], item.source["supplier_id"])
        for item in projection.documents
    } == {
        ("source-a", "supplier-a"),
        ("source-a", "supplier-b"),
        ("source-b", "supplier-a"),
    }


def test_projection_preserves_price_currency_availability_observation_and_provenance():
    evidence = accepted_evidence(
        price="000125.4500",
        currency="EUR",
        availability="backorder",
        freshness=FreshnessState.FRESH,
    )

    stale_projection = build(
        (replace(evidence, freshness=FreshnessState.STALE),)
    )
    document = stale_projection.documents[0].source

    assert document["price"] == "000125.4500"
    assert document["price_original"] == "000125.4500"
    assert document["currency"] == "EUR"
    assert document["price_num"] == 125.45
    assert document["availability"] == "backorder"
    assert document["in_stock"] is None
    assert document["observed_at"] == "2026-11-02T09:30:00Z"
    assert document["freshness"] == "stale"
    assert stale_projection.source_digest != build((evidence,)).source_digest
    assert document["tenant_id"] == "tenant-a"
    assert document["owner_id"] == "owner-a"
    assert document["project_id"] == "project-a"
    assert document["run_id"] == evidence.snapshot.run_id
    assert document["snapshot_id"] == evidence.snapshot.snapshot_id
    assert document["artifact_id"] == evidence.artifact.artifact_id
    assert document["raw_ref"] == evidence.artifact.retained_reference
    assert document["source_record_key"] == "row-a"
    assert document["provenance_digest"] == evidence.snapshot.offers[0].provenance_digest


def test_canonical_order_digest_and_idempotency_ignore_input_order_and_rebuild_time():
    first = accepted_evidence(snapshot_id="snapshot-a", source_record_key="row-a")
    second = accepted_evidence(
        snapshot_id="snapshot-b",
        source_record_key="row-b",
        observed_at=NOW + timedelta(minutes=1),
    )

    forward = build((first, second))
    replay = build((second, first), rebuilt_at=NOW + timedelta(days=1))

    assert forward.source_snapshot_ids == ("snapshot-a", "snapshot-b")
    assert forward.source_digest == replay.source_digest
    assert forward.content_digest == replay.content_digest
    assert forward.idempotency_key == replay.idempotency_key
    assert forward.generation_id == replay.generation_id
    assert tuple(item.document_id for item in forward.documents) == tuple(
        sorted(item.document_id for item in forward.documents)
    )


def test_new_generation_is_exactly_expected_plus_one_and_has_stable_identity():
    first_evidence = accepted_evidence(snapshot_id="snapshot-a")
    second_evidence = accepted_evidence(
        snapshot_id="snapshot-b",
        observed_at=NOW + timedelta(minutes=1),
        artifact_id="artifact-b",
    )

    first = build((first_evidence,), expected_generation=7)
    replay = build((first_evidence,), expected_generation=7)
    next_generation = build((first_evidence,), expected_generation=8)
    latest = build((first_evidence, second_evidence), expected_generation=7)

    assert first.generation == 8
    assert first.generation_id == replay.generation_id
    assert first.idempotency_key == replay.idempotency_key
    assert next_generation.generation == 9
    assert next_generation.idempotency_key == first.idempotency_key
    assert next_generation.generation_id != first.generation_id
    assert latest.document_count == 1
    assert latest.documents[0].source["snapshot_id"] == "snapshot-b"
    assert latest.documents[0].source["observed_at"] == "2026-11-02T09:31:00Z"


def test_cross_tenant_snapshot_is_quarantined_without_leaking_a_document():
    scoped = accepted_evidence(snapshot_id="snapshot-good")
    foreign = accepted_evidence(
        snapshot_id="snapshot-foreign",
        source_id="source-foreign",
        supplier_id="supplier-foreign",
        source_record_key="foreign-row",
        tenant_id="tenant-b",
        owner_id="owner-b",
        project_id="project-b",
    )

    projection = build((foreign, scoped))

    assert projection.source_snapshot_ids == ("snapshot-good",)
    assert projection.document_count == 1
    assert {item.source["tenant_id"] for item in projection.documents} == {"tenant-a"}
    assert len(projection.quarantined) == 1
    assert projection.quarantined[0].reason_code == "tenant_scope_mismatch"
    assert projection.quarantined[0].snapshot_id == "snapshot-foreign"
    assert "foreign-row" not in projection.quarantined[0].record_digest


def test_cross_tenant_only_input_is_quarantined_and_cannot_replace_last_good():
    foreign = accepted_evidence(
        snapshot_id="snapshot-foreign",
        tenant_id="tenant-b",
        owner_id="owner-b",
        project_id="project-b",
    )

    projection = build((foreign,))
    plan = plan_projection_publication(
        projection=projection,
        current_pointer=None,
        expected_generation=0,
    )

    assert projection.documents == ()
    assert projection.source_snapshot_ids == ()
    assert projection.quarantined[0].reason_code == "tenant_scope_mismatch"
    assert plan.decision is PublicationDecision.NO_SEARCHABLE_DOCUMENTS
    assert plan.candidate_pointer is None


def test_incomplete_snapshot_scope_is_quarantined_and_cannot_publish():
    incomplete = accepted_evidence(snapshot_id="snapshot-incomplete")
    object.__setattr__(incomplete.snapshot, "tenant_id", None)

    projection = build((incomplete,))
    plan = plan_projection_publication(
        projection=projection,
        current_pointer=None,
        expected_generation=0,
    )

    assert projection.documents == ()
    assert projection.quarantined[0].reason_code == "snapshot_or_artifact_identity_incomplete"
    assert plan.decision is PublicationDecision.NO_SEARCHABLE_DOCUMENTS
    assert plan.candidate_pointer is None


def test_artifact_run_provenance_mismatch_is_quarantined():
    evidence = accepted_evidence(snapshot_id="snapshot-mismatch")
    wrong_artifact = replace(evidence.artifact, run_id="another-run")
    malformed = SnapshotEvidence(snapshot=evidence.snapshot, artifact=wrong_artifact)

    projection = build((malformed,))

    assert projection.documents == ()
    assert projection.quarantined[0].reason_code == "artifact_provenance_mismatch"


def test_missing_searchable_title_is_quarantined_not_published():
    evidence = (accepted_evidence(title=" "),)

    projection = build(evidence)

    assert projection.document_count == 0
    assert projection.documents == ()
    assert projection.quarantined[0].reason_code == "offer_or_search_fields_invalid"
    assert projection.quarantined[0].offer_id == evidence[0].snapshot.offers[0].offer_id
    plan = plan_projection_publication(
        projection=projection,
        current_pointer=None,
        expected_generation=0,
    )
    assert plan.decision is PublicationDecision.NO_SEARCHABLE_DOCUMENTS
    assert plan.candidate_pointer is None


def test_unaccepted_snapshot_is_rejected_and_not_projected():
    accepted = accepted_evidence()
    unaccepted_snapshot = replace(accepted.snapshot, accepted=False, offers=())
    unaccepted = SnapshotEvidence(snapshot=unaccepted_snapshot, artifact=accepted.artifact)

    with pytest.raises(ValueError, match="unaccepted snapshot"):
        build((unaccepted,))


def test_equal_observation_with_conflicting_offer_data_is_rejected():
    first = accepted_evidence(snapshot_id="snapshot-a", price="10.00")
    second = accepted_evidence(snapshot_id="snapshot-b", price="11.00")
    second_offer = replace(
        second.snapshot.offers[0],
        offer_id=first.snapshot.offers[0].offer_id,
        source_id=first.snapshot.offers[0].source_id,
        supplier_id=first.snapshot.offers[0].supplier_id,
        source_record_key=first.snapshot.offers[0].source_record_key,
        artifact_id=second.artifact.artifact_id,
        provenance_digest=offer_provenance_digest(
            source_id=first.snapshot.offers[0].source_id,
            supplier_id=first.snapshot.offers[0].supplier_id,
            source_record_key=first.snapshot.offers[0].source_record_key,
            artifact_id=second.artifact.artifact_id,
            observed_at=NOW,
        ),
    )
    second = SnapshotEvidence(
        snapshot=replace(second.snapshot, offers=(second_offer,)),
        artifact=second.artifact,
    )

    with pytest.raises(ValueError, match="same observed time"):
        build((first, second))


def test_last_good_pointer_advances_only_as_a_candidate_and_replays_exactly():
    evidence = (accepted_evidence(),)
    first = build(evidence, expected_generation=0)
    initial = plan_projection_publication(
        projection=first,
        current_pointer=None,
        expected_generation=0,
    )

    assert initial.decision is PublicationDecision.PUBLISH
    assert initial.current_pointer is None
    assert initial.candidate_pointer is not None
    assert initial.candidate_pointer.generation == 1
    assert first.generation == 1

    committed_pointer = initial.candidate_pointer
    replay = plan_projection_publication(
        projection=first,
        current_pointer=committed_pointer,
        expected_generation=0,
    )
    assert replay.decision is PublicationDecision.REPLAY
    assert replay.candidate_pointer is None
    assert replay.current_pointer == committed_pointer

    changed = build(
        (accepted_evidence(price="126.00"),),
        expected_generation=1,
    )
    next_plan = plan_projection_publication(
        projection=changed,
        current_pointer=committed_pointer,
        expected_generation=1,
    )
    assert next_plan.decision is PublicationDecision.PUBLISH
    assert next_plan.candidate_pointer is not None
    assert next_plan.candidate_pointer.generation == 2
    assert next_plan.current_pointer == committed_pointer
    assert committed_pointer.generation == 1


def test_stale_generation_cannot_replace_current_last_good_pointer():
    projection = build((accepted_evidence(),), expected_generation=1)
    current = LastGoodProjectionPointer(
        scope=SCOPE,
        projection_name="equipment-search",
        projection_version="1",
        generation=2,
        generation_id="generation-current",
        source_digest=evidence_digest("current-source"),
        content_digest=evidence_digest("current-content"),
        idempotency_key=evidence_digest("current-idempotency"),
    )

    decision = plan_projection_publication(
        projection=projection,
        current_pointer=current,
        expected_generation=1,
    )

    assert decision.decision is PublicationDecision.GENERATION_CONFLICT
    assert decision.candidate_pointer is None
    assert decision.current_pointer == current
