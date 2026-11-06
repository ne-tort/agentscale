"""Policy adapter that gates equipment matching on visibility and provenance."""

from __future__ import annotations

from datetime import timedelta

from .contracts import (
    CatalogSnapshotVisibility,
    MaterializationVisibility,
    SelectedOfferVisibility,
    VisibilityContext,
    VisibleOffer,
)
from .ports import CatalogSnapshotVisibilityPort, OfferVisibilityPort, SourceProvenancePort


class EquipmentVisibilityPolicy:
    """Validate every source boundary before equipment matching is called."""

    def __init__(
        self,
        *,
        snapshots: CatalogSnapshotVisibilityPort,
        offers: OfferVisibilityPort,
        provenance: SourceProvenancePort,
    ) -> None:
        self._snapshots = snapshots
        self._offers = offers
        self._provenance = provenance

    async def authorize_snapshot(
        self, *, context: VisibilityContext, snapshot_id: str
    ) -> CatalogSnapshotVisibility:
        snapshot = await self._snapshots.get_snapshot(snapshot_id)
        if snapshot is None:
            raise ValueError("catalog snapshot is not visible")
        self._assert_tenant_project(context, snapshot.tenant_id, snapshot.project_id)
        if context.now - snapshot.captured_at > timedelta(seconds=context.max_snapshot_age_seconds):
            raise ValueError("catalog snapshot is stale")
        if context.now < snapshot.captured_at:
            raise ValueError("catalog snapshot timestamp is in the future")
        if not snapshot.source_chain:
            raise ValueError("catalog snapshot has no source provenance")
        return snapshot

    async def authorize_materialization(
        self,
        *,
        context: VisibilityContext,
        request_id: str,
        snapshot_id: str,
        offers: tuple[VisibleOffer, ...],
    ) -> MaterializationVisibility:
        snapshot = await self.authorize_snapshot(context=context, snapshot_id=snapshot_id)
        result = MaterializationVisibility(
            request_id=request_id,
            catalog_snapshot_id=snapshot.id,
            offers=offers,
        )
        for offer in result.offers:
            self._assert_tenant_project(
                context, offer.provenance.tenant_id, offer.provenance.project_id
            )
            if context.supplier_keys and offer.supplier_key not in context.supplier_keys:
                raise ValueError("offer supplier is outside the project supplier scope")
            if offer.provenance.source_chain != snapshot.source_chain:
                raise ValueError("offer source provenance does not match catalog snapshot")
            if offer.snapshot_id != snapshot.id:
                raise ValueError("offer is not from the authorized catalog snapshot")
        return result

    async def authorize_selected_offer(
        self, *, context: VisibilityContext, selection: SelectedOfferVisibility
    ) -> VisibleOffer:
        offer = await self._offers.get_offer(selection.offer_id)
        if offer is None:
            raise ValueError("selected offer is not visible")
        snapshot = await self.authorize_snapshot(context=context, snapshot_id=offer.snapshot_id)
        self._assert_tenant_project(
            context, offer.provenance.tenant_id, offer.provenance.project_id
        )
        if context.supplier_keys and offer.supplier_key not in context.supplier_keys:
            raise ValueError("selected offer supplier is outside the project supplier scope")
        if offer.candidate_group_id != selection.candidate_group_id:
            raise ValueError("selected offer does not belong to candidate group")
        if not await self._offers.is_offer_in_candidate_group(
            candidate_group_id=selection.candidate_group_id, offer_id=selection.offer_id
        ):
            raise ValueError("selected offer membership is not proven")
        provenance = await self._provenance.get_provenance(selection.offer_id)
        if provenance is None:
            raise ValueError("selected offer source provenance is missing")
        if provenance != offer.provenance:
            raise ValueError("selected offer source provenance mismatch")
        if provenance.source_chain != snapshot.source_chain:
            raise ValueError("selected offer source provenance does not match catalog snapshot")
        if not provenance.source_chain:
            raise ValueError("selected offer source chain is empty")
        return offer

    @staticmethod
    def _assert_tenant_project(context: VisibilityContext, tenant_id: str, project_id: str) -> None:
        if tenant_id != context.tenant_id:
            raise ValueError("equipment source belongs to another tenant")
        if project_id != context.project_id:
            raise ValueError("equipment source belongs to another project")


__all__ = ["EquipmentVisibilityPolicy"]
