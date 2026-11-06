"""Read-only shadow adapter from normalized equipment to legacy API row shapes.

No database, legacy row CRUD, route handler, OpenSearch writer, action dispatcher,
or intent enqueue is reachable from this module. All source reads go through an
explicit tenant/project/chat-bounded port.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence

from prodavan.application.migration.equipment_domain.domain import (
    CandidateGroup,
    DomainScope,
    EquipmentDomainStatus,
    EquipmentTender,
    HumanSelection,
    OfferFreshness,
    TypedRequest,
    active_human_selections,
    build_projection,
)
from prodavan.application.migration.equipment_domain.service import EquipmentDomainSnapshot
from prodavan.application.migration.equipment_ingestion.ingestion import CatalogSnapshot
from prodavan.application.migration.equipment_ingestion.search_projection import (
    EquipmentSearchProjection,
    ProjectionScope,
)
from .contracts import (
    AdapterError,
    AdapterErrorCode,
    ApiPage,
    CandidateQuery,
    ChatScopedCatalogSnapshot,
    ChatScopedEquipmentSnapshot,
    ChatScopedHumanSelection,
    ChatScopedSearchProjection,
    CatalogOfferQuery,
    CatalogQuery,
    CompatibilityRow,
    CreateCandidateGroupCommand,
    CreateRequestCommand,
    EquipmentApiScope,
    EquipmentCommandEnvelope,
    EquipmentCommand,
    EquipmentCommandKind,
    EquipmentActorKind,
    EquipmentIntent,
    EquipmentIntentState,
    ExportProcurementCommand,
    OfferQuery,
    Page,
    PrepareEquipmentCommandResult,
    ProcurementQuery,
    RefreshCatalogCommand,
    RefreshOffersCommand,
    RequestQuery,
    SelectOfferCommand,
    page_items,
)


class EquipmentApiReadPort(Protocol):
    """Loads a consistent, authorized partition; implementations must not widen scope."""

    async def request_snapshot(self, *, scope: DomainScope, chat_id: str
                               ) -> ChatScopedEquipmentSnapshot | None: ...
    async def catalog_snapshots(self, *, scope: DomainScope, chat_id: str,
                                source_id: str | None = None
                                ) -> Sequence[ChatScopedCatalogSnapshot]: ...
    async def search_projection(self, *, scope: ProjectionScope,
                                chat_id: str) -> ChatScopedSearchProjection | None: ...
    async def human_selections(self, *, scope: DomainScope, chat_id: str
                               ) -> Sequence[ChatScopedHumanSelection]: ...


class EquipmentApiIntentPort(Protocol):
    """Future job/decision/export boundary; intentionally not called by this shadow adapter."""

    async def enqueue(self, *, command: EquipmentCommand,
                      idempotency_key: str) -> PrepareEquipmentCommandResult: ...


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"unsupported equipment value {type(value).__name__}")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, default=_json_default, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _legacy_row(table_slug: str, row_id: str, body: Mapping[str, Any],
                scope: EquipmentApiScope, provenance: Mapping[str, str], *,
                omitted_fields: tuple[str, ...] = ()) -> CompatibilityRow:
    stamped = dict(body)
    stamped["session_id"] = scope.chat_id
    return CompatibilityRow(table_slug, row_id, stamped, scope.chat_id, True,
                            dict(provenance), False, omitted_fields, legacy_row_id=None)


def _preview_id(table_slug: str, domain_id: str, scope: EquipmentApiScope) -> str:
    return "shadow:" + _digest((scope.tenant_id, scope.owner_id, scope.project_id,
                                 scope.chat_id, table_slug, domain_id))[7:31]


def _snapshot_digest(snapshot: EquipmentDomainSnapshot) -> str:
    return build_projection(
        requests=snapshot.requests,
        lines=snapshot.lines,
        candidate_groups=snapshot.candidate_groups,
        tenders=snapshot.tenders,
        expected_scope=snapshot.scope,
        source_revision=snapshot.source_revision,
        source_generation=snapshot.source_generation,
    ).contract.input_digest


def _query_digest(scope: EquipmentApiScope, source_digest: str,
                  filters: Mapping[str, Any]) -> str:
    return _digest({"tenant_id": scope.tenant_id, "owner_id": scope.owner_id,
                    "project_id": scope.project_id, "chat_id": scope.chat_id,
                    "actor_id": scope.actor_id,
                    "actor_kind": scope.actor_kind.value if scope.actor_kind else None,
                    "source_digest": source_digest, "filters": filters})


class EquipmentApiAdapter:
    """Typed read API and disabled write-intent facade for equipment migration."""

    def __init__(self, *, source: EquipmentApiReadPort) -> None:
        self._source = source

    async def requests(self, query: RequestQuery) -> ApiPage:
        snapshot = await self._domain_snapshot(query.scope)
        rows = tuple(sorted((row for row in snapshot.requests
                             if query.request_id is None or row.request_id == query.request_id),
                            key=lambda row: row.request_id))
        digest = _query_digest(query.scope, _snapshot_digest(snapshot),
                               {"request_id": query.request_id})
        selected, cursor = page_items(rows, query.page, snapshot_digest=digest)
        compat = tuple(_legacy_row(
            "request_lines",
            _preview_id("request_lines", line.line_id, query.scope),
            {"title": line.description, "qty": str(line.quantity), "unit": line.unit,
             "part_number": line.part_number,
             "migration_domain_request_id": line.request_id,
             "migration_domain_line_id": line.line_id},
            query.scope,
            {"source_revision": snapshot.source_revision, "snapshot_digest": digest,
             "domain_record_id": line.line_id},
            omitted_fields=("legacy row id", "legacy request id", "legacy lines may have independent pagination"),
        ) for row in selected for line in sorted(snapshot.lines,
                                                 key=lambda item: (item.request_id, item.ordinal, item.line_id))
            if line.request_id == row.request_id)
        items = tuple(self._request_dto(
            row, lines=tuple(sorted((line for line in snapshot.lines if line.request_id == row.request_id),
                                    key=lambda item: (item.ordinal, item.line_id)))
        ) for row in selected)
        return ApiPage(items, cursor, snapshot.source_revision, digest, compat)

    async def candidates(self, query: CandidateQuery) -> ApiPage:
        snapshot = await self._domain_snapshot(query.scope)
        rows = tuple(sorted((row for row in snapshot.candidate_groups
                             if query.line_id is None or row.line_id == query.line_id),
                            key=lambda row: (row.line_id, row.ordinal, row.group_id)))
        digest = _query_digest(query.scope, _snapshot_digest(snapshot),
                               {"line_id": query.line_id})
        selected, cursor = page_items(rows, query.page, snapshot_digest=digest)
        compat = tuple(_legacy_row("found_groups", _preview_id("found_groups", row.group_id, query.scope), self._candidate_body(row),
            query.scope, {"source_revision": snapshot.source_revision,
                          "snapshot_digest": digest, "domain_record_id": row.group_id},
            omitted_fields=("aliases_hash", "source_hash", "legacy row id", "legacy line id"))
            for row in selected)
        return ApiPage(tuple(self._candidate_dto(row) for row in selected), cursor,
                       snapshot.source_revision, digest, compat)

    async def offers(self, query: OfferQuery) -> ApiPage:
        snapshot = await self._domain_snapshot(query.scope)
        rows = tuple(sorted((row for row in snapshot.tenders
                             if (query.line_id is None or row.line_id == query.line_id)
                             and (query.candidate_group_id is None or row.group_id == query.candidate_group_id)
                             and (query.supplier_id is None or row.supplier_id == query.supplier_id)
                             and (query.include_stale or row.freshness is OfferFreshness.FRESH)),
                            key=lambda row: (row.line_id, row.ordinal, row.supplier_id,
                                             row.source_id, row.source_record_id, row.tender_id)))
        digest = _query_digest(query.scope, _snapshot_digest(snapshot),
                               {"line_id": query.line_id,
                                "candidate_group_id": query.candidate_group_id,
                                "supplier_id": query.supplier_id,
                                "include_stale": query.include_stale})
        selected, cursor = page_items(rows, query.page, snapshot_digest=digest)
        compat = tuple(_legacy_row("found_offers", _preview_id("found_offers", row.tender_id, query.scope), self._offer_body(row),
            query.scope, {"source_revision": snapshot.source_revision,
                          "snapshot_digest": digest, "domain_record_id": row.tender_id,
                          "source_id": row.source_id, "source_record_id": row.source_record_id},
            omitted_fields=("legacy offer row id", "legacy line/group/seller IDs",
                            "available quantity for procurement eligibility",
                            "price in converted RUB", "seller display name"))
            for row in selected)
        return ApiPage(tuple(self._tender_dto(row) for row in selected), cursor,
                       snapshot.source_revision, digest, compat)

    async def procurement(self, query: ProcurementQuery) -> ApiPage:
        if query.scope.actor_kind is not EquipmentActorKind.HUMAN:
            raise AdapterError(AdapterErrorCode.HUMAN_SELECTION_REQUIRED,
                               "procurement projection requires an authenticated human actor")
        snapshot = await self._domain_snapshot(query.scope)
        selections = await self._active_selections(query.scope)
        lines = {row.line_id: row for row in snapshot.lines}
        tenders = {row.tender_id: row for row in snapshot.tenders}
        projection_digest = _snapshot_digest(snapshot)
        for selection in selections:
            line = lines.get(selection.line_id)
            tender = tenders.get(selection.tender_id)
            if (selection.actor_id != query.scope.actor_id or line is None or tender is None
                    or tender.line_id != selection.line_id
                    or selection.projection_digest != projection_digest
                    or tender.freshness is not OfferFreshness.FRESH or tender.on_order
                    or tender.available_quantity is None
                    or tender.available_quantity < line.quantity
                    or (selection.supplier_id, selection.source_id, selection.source_record_id,
                        selection.source_schema_version) != (
                            tender.supplier_id, tender.source_id, tender.source_record_id,
                            tender.evidence.source_schema_version)):
                raise AdapterError(AdapterErrorCode.HUMAN_SELECTION_REQUIRED,
                                   "procurement requires a current human selection of an available offer with matching provenance")
        selected_by_line = {row.line_id: row for row in selections}
        tenders = {row.tender_id: row for row in snapshot.tenders}
        by_supplier_currency: dict[tuple[str, str], dict[str, Any]] = {}
        for line in sorted(snapshot.lines, key=lambda row: (row.request_id, row.ordinal, row.line_id)):
            selection = selected_by_line.get(line.line_id)
            tender = tenders.get(selection.tender_id) if selection else None
            if tender is None or (query.supplier_id and tender.supplier_id != query.supplier_id):
                continue
            supplier_key = (tender.supplier_id, tender.currency)
            body = by_supplier_currency.setdefault(supplier_key, {
                "supplier_id": tender.supplier_id, "currency": tender.currency,
                "selected_count": 0, "quantity": Decimal("0"),
                "total": Decimal("0"), "selection_ids": [], "tender_ids": [],
            })
            body["selected_count"] += 1
            body["quantity"] += line.quantity
            body["total"] += line.quantity * tender.unit_price
            assert selection is not None
            body["selection_ids"].append(selection.selection_id)
            body["tender_ids"].append(tender.tender_id)
        rows = tuple({**by_supplier_currency[key],
                      "quantity": str(by_supplier_currency[key]["quantity"]),
                      "total": str(by_supplier_currency[key]["total"]),
                      "selection_ids": tuple(by_supplier_currency[key]["selection_ids"]),
                      "tender_ids": tuple(by_supplier_currency[key]["tender_ids"])}
                     for key in sorted(by_supplier_currency))
        digest = _query_digest(query.scope, _snapshot_digest(snapshot),
                               {"selection_evidence_digest": _digest(selections),
                                "supplier_id": query.supplier_id})
        selected, cursor = page_items(rows, query.page, snapshot_digest=digest)
        compat = tuple(_legacy_row(
            "procurement",
            _preview_id("procurement", _digest((query.scope.chat_id, row["supplier_id"],
                                                   row["currency"])), query.scope),
            self._procurement_body(row), query.scope,
            {"source_revision": snapshot.source_revision,
             "snapshot_digest": digest,
             "selection_ids_digest": _digest(row["selection_ids"])},
            omitted_fields=("offers_count", "margin_pct", "delivery", "margin totals",
                            "project-wide cross-chat aggregation", "legacy row id"),
        ) for row in selected)
        items = tuple({"supplier_id": row["supplier_id"],
                       "selected_count": row["selected_count"],
                       "quantity": row["quantity"], "selected_total": row["total"],
                       "currency": row["currency"], "selection_ids": row["selection_ids"],
                       "tender_ids": row["tender_ids"]} for row in selected)
        return ApiPage(items, cursor, snapshot.source_revision, digest, compat)

    async def catalog(self, query: CatalogQuery) -> ApiPage:
        scoped_rows = tuple(await self._source.catalog_snapshots(
            scope=query.scope.domain_scope, chat_id=query.scope.chat_id,
            source_id=query.source_id))
        if any(row.chat_id != query.scope.chat_id for row in scoped_rows):
            raise AdapterError(AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH,
                               "catalog snapshot belongs to another chat partition")
        all_rows = tuple(item.snapshot for item in scoped_rows)
        expected = (query.scope.tenant_id, query.scope.owner_id, query.scope.project_id)
        if any((row.tenant_id, row.owner_id, row.project_id) != expected for row in all_rows):
            raise AdapterError(AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH,
                               "catalog source returned another tenant/owner/project")
        rows = tuple(sorted((row for row in all_rows
                             if query.source_id is None or row.source_id == query.source_id),
                            key=lambda row: (row.source_id, row.snapshot_id)))
        digest = _query_digest(query.scope,
            _digest(tuple((row.snapshot_id, row.tenant_id, row.owner_id, row.project_id,
                           row.source_id, row.supplier_id, row.source_revision,
                           row.schema_version, row.observed_at.isoformat(), row.freshness.value,
                           row.content_digest, row.artifact_id, row.accepted) for row in rows)),
            {"source_id": query.source_id})
        selected, cursor = page_items(rows, query.page, snapshot_digest=digest)
        source_revision = _digest(tuple((row.snapshot_id, row.source_revision)
                                        for row in rows))
        return ApiPage(tuple(self._catalog_dto(row) for row in selected), cursor,
                       source_revision, digest)

    async def catalog_offers(self, query: CatalogOfferQuery) -> ApiPage:
        scoped_rows = tuple(await self._source.catalog_snapshots(
            scope=query.scope.domain_scope, chat_id=query.scope.chat_id,
            source_id=query.source_id))
        if any(row.chat_id != query.scope.chat_id for row in scoped_rows):
            raise AdapterError(AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH,
                               "catalog offer snapshot belongs to another chat partition")
        all_snapshots = tuple(item.snapshot for item in scoped_rows)
        expected = (query.scope.tenant_id, query.scope.owner_id, query.scope.project_id)
        if any((row.tenant_id, row.owner_id, row.project_id) != expected for row in all_snapshots):
            raise AdapterError(AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH,
                               "catalog offer source returned another tenant/owner/project")
        snapshots = tuple(row for row in all_snapshots
                          if query.source_id is None or row.source_id == query.source_id)
        chosen = tuple(row for row in snapshots
                       if row.accepted and (query.snapshot_id is None or row.snapshot_id == query.snapshot_id))
        if query.snapshot_id is not None and not chosen:
            raise AdapterError(AdapterErrorCode.SNAPSHOT_UNAVAILABLE,
                               "accepted catalog snapshot is unavailable")
        evidence_digest = _query_digest(query.scope,
            _digest(tuple((row.snapshot_id, row.source_id, row.supplier_id, row.source_revision,
                           row.observed_at.isoformat(), row.freshness.value,
                           row.content_digest, row.artifact_id)
                          for row in sorted(chosen, key=lambda item: item.snapshot_id))),
            {"snapshot_id": query.snapshot_id, "source_id": query.source_id,
             "supplier_id": query.supplier_id})
        offer_rows = tuple(sorted(
            ((row, offer) for row in chosen for offer in row.offers
             if query.supplier_id is None or offer.supplier_id == query.supplier_id),
            key=lambda pair: (pair[0].snapshot_id, pair[1].supplier_id,
                              pair[1].source_id, pair[1].source_record_key, pair[1].offer_id)))
        selected, cursor = page_items(offer_rows, query.page, snapshot_digest=evidence_digest)
        items = tuple({"offer_id": row.offer_id, "snapshot_id": row.snapshot_id,
                       "tenant_id": snapshot.tenant_id, "owner_id": snapshot.owner_id,
                       "project_id": snapshot.project_id,
                       "supplier_id": row.supplier_id, "source_id": row.source_id,
                       "source_record_key": row.source_record_key,
                       "observed_at": row.observed_at.isoformat(), "currency": row.currency,
                       "price": row.price, "availability": row.availability,
                       "freshness": snapshot.freshness.value,
                       "source_revision": snapshot.source_revision,
                       "schema_version": snapshot.schema_version,
                       "snapshot_digest": snapshot.content_digest,
                       "attributes": dict(row.attributes), "artifact_id": row.artifact_id,
                       "provenance_digest": row.provenance_digest}
                      for snapshot, row in selected)
        source_revision = _digest(tuple((row.snapshot_id, row.source_revision)
                                        for row in sorted(chosen, key=lambda item: item.snapshot_id)))
        return ApiPage(items, cursor, source_revision, evidence_digest)

    async def search_projection(self, scope: EquipmentApiScope) -> EquipmentSearchProjection | None:
        scoped = await self._source.search_projection(
            scope=ProjectionScope(scope.tenant_id, scope.owner_id, scope.project_id),
            chat_id=scope.chat_id)
        if scoped is None:
            return None
        if scoped.chat_id != scope.chat_id:
            raise AdapterError(AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH,
                               "search projection belongs to another chat partition")
        projection = scoped.projection
        if projection is not None and (projection.scope.tenant_id, projection.scope.owner_id,
                projection.scope.project_id) != (scope.tenant_id, scope.owner_id, scope.project_id):
            raise AdapterError(AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH,
                               "search projection is outside the requested tenant/project scope")
        return projection

    def prepare_command(self, command: EquipmentCommand) -> PrepareEquipmentCommandResult:
        command_id = getattr(command, "command_id", "")
        if not isinstance(command, (CreateRequestCommand, CreateCandidateGroupCommand,
                                    SelectOfferCommand, RefreshCatalogCommand,
                                    RefreshOffersCommand, ExportProcurementCommand)):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "unsupported equipment command")
        if not isinstance(command_id, str) or not command_id.strip():
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "command_id is required for idempotency")
        payload_digest = _digest(command)
        scope_key = (command.scope.tenant_id, command.scope.owner_id,
                     command.scope.project_id, command.scope.chat_id,
                     command.scope.actor_id, command.scope.actor_kind.value
                     if command.scope.actor_kind is not None else None)
        # Keep retry identity stable per scoped command ID. A durable sink must
        # compare payload_digest and reject reuse of that ID with changed intent.
        idem = _digest({"scope": scope_key, "command_id": command_id.strip()})
        gates = self._required_gates(command)
        command_kind = self._command_kind(command)
        payload = self._command_payload(command)
        envelope = EquipmentCommandEnvelope(
            version="1", command_kind=command_kind, scope=command.scope,
            command_id=command_id.strip(), idempotency_key=idem,
            payload_digest=payload_digest, payload=payload,
            actor_id=command.scope.actor_id, actor_kind=command.scope.actor_kind,
            expected_source_revision=getattr(command, "expected_source_revision", None),
            expected_snapshot_digest=getattr(command, "expected_snapshot_digest",
                                             getattr(command, "expected_projection_digest", None)),
            expected_selection_digest=getattr(command, "expected_selection_digest", None),
            canonical_registry_digest=getattr(command, "canonical_registry_digest", None),
        )
        intent = EquipmentIntent(
            command_kind=command_kind, scope=command.scope,
            command_id=command_id.strip(), idempotency_key=idem,
            payload_digest=payload_digest, payload={"command": payload},
            state=EquipmentIntentState.BLOCKED, write_enabled=False,
            required_gates=gates, envelope_version="1", envelope=envelope,
        )
        return PrepareEquipmentCommandResult(
            command_kind=self._command_kind(command), command_id=command_id.strip(),
            idempotency_key=idem, accepted=False, write_enabled=False,
            required_gates=gates, reason_code="equipment_write_gate_unproven",
            payload_digest=payload_digest, intent=intent)

    async def execute_command(self, command: EquipmentCommand) -> PrepareEquipmentCommandResult:
        """Prepare a disabled intent only; legacy rows and job ports remain untouched."""
        return self.prepare_command(command)

    async def compatibility_rows(self, query: RequestQuery | CatalogQuery | CatalogOfferQuery
                                 | CandidateQuery | OfferQuery | ProcurementQuery
                                 ) -> tuple[CompatibilityRow, ...]:
        handlers = {RequestQuery: self.requests, CatalogQuery: self.catalog,
                    CatalogOfferQuery: self.catalog_offers,
                    CandidateQuery: self.candidates, OfferQuery: self.offers,
                    ProcurementQuery: self.procurement}
        return (await handlers[type(query)](query)).compatibility_rows

    async def _domain_snapshot(self, scope: EquipmentApiScope) -> EquipmentDomainSnapshot:
        scoped = await self._source.request_snapshot(scope=scope.domain_scope, chat_id=scope.chat_id)
        if scoped is None:
            raise AdapterError(AdapterErrorCode.SNAPSHOT_UNAVAILABLE,
                               "equipment domain snapshot is unavailable", retryable=True)
        if scoped.chat_id != scope.chat_id or scoped.scope != scope.domain_scope:
            raise AdapterError(AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH,
                               "equipment snapshot is outside requested chat/project scope")
        snapshot = scoped.snapshot
        if snapshot.scope != scope.domain_scope:
            raise AdapterError(AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH,
                               "equipment snapshot is outside requested tenant/owner/project")
        projection = build_projection(requests=snapshot.requests, lines=snapshot.lines,
            candidate_groups=snapshot.candidate_groups, tenders=snapshot.tenders,
            expected_scope=scope.domain_scope, source_revision=snapshot.source_revision,
            source_generation=snapshot.source_generation)
        if projection.status is not EquipmentDomainStatus.READY:
            raise AdapterError(AdapterErrorCode.INCOMPLETE_CANONICAL_EVIDENCE,
                               "equipment snapshot lacks canonical scope or source evidence")
        return EquipmentDomainSnapshot(
            scope=snapshot.scope,
            source_revision=snapshot.source_revision,
            requests=tuple(sorted(snapshot.requests, key=lambda row: row.request_id)),
            lines=tuple(sorted(snapshot.lines, key=lambda row: (row.request_id, row.ordinal, row.line_id))),
            candidate_groups=tuple(sorted(snapshot.candidate_groups,
                                          key=lambda row: (row.line_id, row.ordinal, row.group_id))),
            tenders=tuple(sorted(snapshot.tenders,
                                 key=lambda row: (row.line_id, row.ordinal, row.supplier_id,
                                                  row.source_id, row.source_record_id, row.tender_id))),
            source_generation=snapshot.source_generation,
        )

    async def _active_selections(self, scope: EquipmentApiScope) -> tuple[HumanSelection, ...]:
        scoped_selections = tuple(await self._source.human_selections(
            scope=scope.domain_scope, chat_id=scope.chat_id))
        if any(row.chat_id != scope.chat_id for row in scoped_selections):
            raise AdapterError(AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH,
                               "human selection belongs to another chat partition")
        selections = tuple(row.selection for row in scoped_selections)
        expected = (scope.tenant_id, scope.owner_id, scope.project_id)
        if any((item.tenant_id, item.owner_id, item.project_id) != expected for item in selections):
            raise AdapterError(AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH,
                               "selection history is outside requested tenant/owner/project")
        try:
            return active_human_selections(selections)
        except ValueError as error:
            raise AdapterError(AdapterErrorCode.LEGACY_ROW_INVALID,
                               "human selection history is invalid") from error

    @staticmethod
    def _request_dto(row: TypedRequest, *, lines: Sequence[Any] = ()) -> Mapping[str, Any]:
        return {"request_id": row.request_id, "title": row.title,
                "currency": row.currency, "created_at": row.created_at,
                "lines": tuple({"line_id": line.line_id, "ordinal": line.ordinal,
                                "description": line.description, "quantity": str(line.quantity),
                                "unit": line.unit, "part_number": line.part_number}
                               for line in lines),
                "scope": {"tenant_id": row.evidence.tenant_id, "owner_id": row.evidence.owner_id,
                          "project_id": row.evidence.project_id},
                "source": {"source_id": row.evidence.source_id,
                           "supplier_id": row.evidence.supplier_id,
                           "schema_version": row.evidence.source_schema_version}}

    @staticmethod
    def _candidate_dto(row: CandidateGroup) -> Mapping[str, Any]:
        return {"candidate_group_id": row.group_id, "line_id": row.line_id,
                "ordinal": row.ordinal, "match_kind": row.match_kind,
                "scope": {"tenant_id": row.evidence.tenant_id,
                          "owner_id": row.evidence.owner_id,
                          "project_id": row.evidence.project_id},
                "source": {"source_id": row.evidence.source_id,
                           "supplier_id": row.evidence.supplier_id,
                           "schema_version": row.evidence.source_schema_version},
                "part_number": row.part_number, "aliases": row.aliases, "note": row.note}

    @staticmethod
    def _candidate_body(row: CandidateGroup) -> Mapping[str, Any]:
        return {"migration_domain_line_id": row.line_id,
                "migration_domain_group_id": row.group_id,
                "migration_domain_supplier_id": row.evidence.supplier_id,
                "source_id": row.evidence.source_id,
                "source_schema_version": row.evidence.source_schema_version,
                "part_number": row.part_number,
                "match_kind": row.match_kind,
                "note": row.note}

    @staticmethod
    def _tender_dto(row: EquipmentTender) -> Mapping[str, Any]:
        return {"tender_id": row.tender_id, "line_id": row.line_id,
                "candidate_group_id": row.group_id, "supplier_id": row.supplier_id,
                "source_id": row.source_id, "source_record_id": row.source_record_id,
                "scope": {"tenant_id": row.evidence.tenant_id,
                          "owner_id": row.evidence.owner_id,
                          "project_id": row.evidence.project_id},
                "part_number": row.part_number, "description": row.description,
                "currency": row.currency, "unit_price": str(row.unit_price),
                "available_quantity": (str(row.available_quantity)
                                        if row.available_quantity is not None else None),
                "freshness": row.freshness.value, "on_order": row.on_order,
                "source_schema_version": row.evidence.source_schema_version}

    @staticmethod
    def _offer_body(row: EquipmentTender) -> Mapping[str, Any]:
        return {"migration_domain_line_id": row.line_id,
                "migration_domain_group_id": row.group_id,
                "migration_domain_tenant_id": row.evidence.tenant_id,
                "migration_domain_owner_id": row.evidence.owner_id,
                "migration_domain_project_id": row.evidence.project_id,
                "migration_domain_supplier_id": row.supplier_id,
                "source_id": row.source_id,
                "source_record_id": row.source_record_id, "part_number": row.part_number,
                "title": row.description, "price_orig": str(row.unit_price),
                "currency": row.currency,
                "is_stale": (True if row.freshness is OfferFreshness.STALE
                             else False if row.freshness is OfferFreshness.FRESH else None),
                "freshness": row.freshness.value,
                "on_order": row.on_order,
                "source_schema_version": row.evidence.source_schema_version,
                "is_best": False,
                "migration_domain_tender_id": row.tender_id}

    @staticmethod
    def _catalog_dto(row: CatalogSnapshot) -> Mapping[str, Any]:
        return {"snapshot_id": row.snapshot_id, "source_id": row.source_id,
                "supplier_id": row.supplier_id, "source_revision": row.source_revision,
                "tenant_id": row.tenant_id, "owner_id": row.owner_id,
                "project_id": row.project_id,
                "schema_version": row.schema_version, "observed_at": row.observed_at.isoformat(),
                "freshness": row.freshness.value, "accepted": row.accepted,
                "content_digest": row.content_digest, "artifact_id": row.artifact_id,
                "offer_count": len(row.offers), "quarantined_count": len(row.quarantined_rows)}

    @staticmethod
    def _procurement_body(row: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"migration_domain_supplier_id": row["supplier_id"], "offers_count": 0,
                "selected_count": row["selected_count"], "qty_total": row["quantity"],
                "currency": row["currency"],
                "include_delivery": False, "delivery_rub": "0",
                "sum_margin_rub": None, "sum_with_margin_rub": None,
                "selection_ids": row["selection_ids"], "tender_ids": row["tender_ids"],
                "migration_shadow": True}

    @staticmethod
    def _command_kind(command: EquipmentCommand) -> EquipmentCommandKind:
        return {CreateRequestCommand: EquipmentCommandKind.CREATE_REQUEST,
                CreateCandidateGroupCommand: EquipmentCommandKind.CREATE_CANDIDATE_GROUP,
                SelectOfferCommand: EquipmentCommandKind.SELECT_OFFER,
                RefreshCatalogCommand: EquipmentCommandKind.REFRESH_CATALOG,
                RefreshOffersCommand: EquipmentCommandKind.REFRESH_OFFERS,
                ExportProcurementCommand: EquipmentCommandKind.EXPORT_PROCUREMENT}[type(command)]

    @staticmethod
    def _command_payload(command: EquipmentCommand) -> Mapping[str, Any]:
        return json.loads(json.dumps(command, default=_json_default,
                                     sort_keys=True, separators=(",", ":")))

    @staticmethod
    def _required_gates(command: EquipmentCommand) -> tuple[str, ...]:
        gates = ["canonical_equipment_registry_evidence", "canonical_tenant_and_owner_resolution",
                 "accepted_source_snapshot_binding", "compare_and_swap_write_adapter",
                 "durable_idempotency_store"]
        if not getattr(command, "canonical_registry_digest", None):
            gates.append("canonical_equipment_registry_digest_missing")
        if not getattr(command, "expected_source_revision", None):
            gates.append("expected_source_revision_missing")
        if isinstance(command, ExportProcurementCommand):
            if not command.expected_selection_digest:
                gates.append("expected_selection_digest_missing")
        elif not (getattr(command, "expected_snapshot_digest", None)
                  or getattr(command, "expected_projection_digest", None)):
            gates.append("expected_snapshot_digest_missing")
        if isinstance(command, SelectOfferCommand):
            gates.extend(("authenticated_human_selection", "human_approval_evidence",
                          "selection_audit_and_supersession_cas"))
        if isinstance(command, ExportProcurementCommand):
            gates.extend(("current_human_selection_set", "human_export_approval",
                          "export_artifact_store_boundary"))
        if isinstance(command, (RefreshCatalogCommand, RefreshOffersCommand)):
            gates.extend(("idempotent_durable_job_queue", "source_adapter_authorization"))
        gates.extend(("legacy_owner_write_compatibility_proof", "migration_cutover_approval"))
        return tuple(gates)


__all__ = ["EquipmentApiAdapter", "EquipmentApiIntentPort", "EquipmentApiReadPort"]
