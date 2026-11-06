"""Source tests for the exclusive equipment visibility policy zone.

Tests are intentionally not executed during the migration verification freeze.
"""

from datetime import datetime, timedelta, timezone

import pytest

from prodavan.application.equipment_visibility.contracts import (
    CatalogSnapshotVisibility,
    SelectedOfferVisibility,
    SourceProvenance,
    VisibilityContext,
    VisibleOffer,
)
from prodavan.application.equipment_visibility.service import EquipmentVisibilityPolicy


NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)


def provenance(*, supplier="supplier-a", tenant="tenant-1", project="project-1", chain=("source-1",)):
    return SourceProvenance(
        tenant_id=tenant,
        project_id=project,
        supplier_key=supplier,
        source_chain=chain,
        run_id="run-1",
        artifact_id="artifact-1",
    )


def offer(*, offer_id="offer-1", supplier="supplier-a", tenant="tenant-1", project="project-1"):
    return VisibleOffer(
        id=offer_id,
        candidate_group_id="group-1",
        part_number="PN-1",
        supplier_key=supplier,
        snapshot_id="snapshot-1",
        provenance=provenance(supplier=supplier, tenant=tenant, project=project),
    )


def context(*, suppliers=frozenset({"supplier-a", "supplier-b"}), max_age=3600):
    return VisibilityContext(
        tenant_id="tenant-1",
        project_id="project-1",
        supplier_keys=suppliers,
        now=NOW,
        max_snapshot_age_seconds=max_age,
    )


class SnapshotPort:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def get_snapshot(self, snapshot_id):
        return self.snapshot if self.snapshot and self.snapshot.id == snapshot_id else None


class OfferPort:
    def __init__(self, value, member=True):
        self.value = value
        self.member = member

    async def get_offer(self, offer_id):
        return self.value if self.value and self.value.id == offer_id else None

    async def is_offer_in_candidate_group(self, *, candidate_group_id, offer_id):
        return self.member


class ProvenancePort:
    def __init__(self, value):
        self.value = value

    async def get_provenance(self, offer_id):
        return self.value


def policy(*, snapshot=None, value=None, member=True, provenance_value=None):
    return EquipmentVisibilityPolicy(
        snapshots=SnapshotPort(snapshot),
        offers=OfferPort(value, member),
        provenance=ProvenancePort(provenance_value),
    )


def snapshot(*, tenant="tenant-1", project="project-1", captured_at=NOW - timedelta(minutes=5), chain=("source-1",)):
    return CatalogSnapshotVisibility(
        id="snapshot-1",
        tenant_id=tenant,
        project_id=project,
        captured_at=captured_at,
        source_chain=chain,
    )


@pytest.mark.asyncio
async def test_snapshot_requires_same_tenant_project_and_freshness():
    with pytest.raises(ValueError, match="another tenant"):
        await policy(snapshot=snapshot(tenant="tenant-2")).authorize_snapshot(
            context=context(), snapshot_id="snapshot-1"
        )
    with pytest.raises(ValueError, match="stale"):
        await policy(snapshot=snapshot(captured_at=NOW - timedelta(hours=2))).authorize_snapshot(
            context=context(max_age=60), snapshot_id="snapshot-1"
        )
    with pytest.raises(ValueError, match="future"):
        await policy(snapshot=snapshot(captured_at=NOW + timedelta(minutes=1))).authorize_snapshot(
            context=context(), snapshot_id="snapshot-1"
        )


@pytest.mark.asyncio
async def test_materialization_checks_supplier_scope_and_source_chain():
    with pytest.raises(ValueError, match="supplier scope"):
        await policy(snapshot=snapshot(),).authorize_materialization(
            context=context(suppliers=frozenset({"supplier-a"})),
            request_id="request-1",
            snapshot_id="snapshot-1",
            offers=(offer(supplier="supplier-b"),),
        )

    with pytest.raises(ValueError, match="source provenance"):
        await policy(snapshot=snapshot(chain=("source-2",))).authorize_materialization(
            context=context(),
            request_id="request-1",
            snapshot_id="snapshot-1",
            offers=(offer(),),
        )


@pytest.mark.asyncio
async def test_selected_offer_requires_membership_and_matching_provenance():
    selected = SelectedOfferVisibility(
        candidate_group_id="group-1", offer_id="offer-1", approval_id="approval-1"
    )
    with pytest.raises(ValueError, match="membership"):
        await policy(snapshot=snapshot(), value=offer(), member=False, provenance_value=provenance()).authorize_selected_offer(
            context=context(), selection=selected
        )
    with pytest.raises(ValueError, match="provenance mismatch"):
        await policy(snapshot=snapshot(), value=offer(), provenance_value=provenance(supplier="supplier-b")).authorize_selected_offer(
            context=context(), selection=selected
        )
    with pytest.raises(ValueError, match="does not match catalog snapshot"):
        await policy(
            snapshot=snapshot(chain=("source-2",)),
            value=offer(),
            provenance_value=provenance(),
        ).authorize_selected_offer(context=context(), selection=selected)


@pytest.mark.asyncio
async def test_selected_offer_rejects_cross_project_offer():
    selected = SelectedOfferVisibility(
        candidate_group_id="group-1", offer_id="offer-1", approval_id="approval-1"
    )
    with pytest.raises(ValueError, match="another project"):
        await policy(
            snapshot=snapshot(),
            value=offer(project="project-2"),
            provenance_value=provenance(project="project-2"),
        ).authorize_selected_offer(context=context(), selection=selected)


def test_same_part_number_is_separated_by_supplier_but_duplicate_supplier_is_rejected():
    from prodavan.application.equipment_visibility.contracts import MaterializationVisibility

    MaterializationVisibility(
        request_id="request-1",
        catalog_snapshot_id="snapshot-1",
        offers=(offer(supplier="supplier-a"), offer(offer_id="offer-2", supplier="supplier-b")),
    )
    with pytest.raises(ValueError, match="single separated offer"):
        MaterializationVisibility(
            request_id="request-1",
            catalog_snapshot_id="snapshot-1",
            offers=(offer(), offer(offer_id="offer-2")),
        )
