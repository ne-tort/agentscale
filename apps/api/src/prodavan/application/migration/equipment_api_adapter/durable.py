"""Durable API adapter: read port and mutation gateway over ``equipment_v2_*``.

Two seams are provided.

:class:`DurableEquipmentApiReadPort`
    Implements :class:`~.adapter.EquipmentApiReadPort` for the existing shadow
    adapter, but reads durable rows through
    :class:`~.equipment_domain.durable.SqlAlchemyEquipmentRepository` instead of
    fixtures.  Catalog snapshot/offer reads are deliberately *not* implemented
    here: catalog ingestion persistence belongs to ``equipment_ingestion/**``
    (another agent's zone), so those reads fail closed with
    ``catalog_source_unavailable`` rather than pretending an empty catalog.

:class:`DurableEquipmentCommandGateway`
    Executes selection and prepared-export commands against the durable domain
    service.  Scope is taken from the **server-resolved principal** and only then
    compared with the request-carrying :class:`~.contracts.EquipmentApiScope`; a
    client-supplied tenant/project/actor kind is never authority.  Mutations are
    idempotent by ``command_id``: the same command ID derives the same durable
    selection/export identity, so a replay returns the original outcome and a
    changed payload is an ``idempotency_conflict``.

Stable error mapping
--------------------
Every durable ``reason_code`` maps to exactly one :class:`~.contracts.AdapterErrorCode`
through :data:`REASON_TO_ERROR_CODE`.  Raw reason tokens are kept as well, so
observability does not lose the fine-grained cause, but API clients branch on the
stable code only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from prodavan.application.migration.equipment_domain.domain import (
    DomainScope,
    HumanSelection,
    _stable_digest,
)
from prodavan.application.migration.equipment_domain.durable import (
    REASON_COMMIT_UNKNOWN,
    REASON_CONTEXT_MISMATCH,
    REASON_CONTEXT_MISSING,
    REASON_EXPORT_IDEMPOTENCY_CONFLICT,
    REASON_EXPORT_LINE_MISMATCH,
    REASON_EXPORT_SELECTION_SET_CHANGED,
    REASON_OWNER_MISMATCH,
    REASON_PRINCIPAL_MISMATCH,
    REASON_PROJECT_ANCHOR_MISSING,
    REASON_REQUEST_BINDING_MISSING,
    REASON_SELECTED_OFFER_DETACHED,
    REASON_SELECTION_CAS_CONFLICT,
    REASON_SELECTION_GROUP_AMBIGUOUS,
    REASON_SELECTION_IDEMPOTENCY_CONFLICT,
    REASON_SELECTION_LINE_MISSING,
    REASON_SELECTION_MEMBERSHIP_UNPROVEN,
    REASON_SELECTION_UNCHANGED,
    UNRESOLVED_OFFER_GROUP_AMBIGUOUS,
    UNRESOLVED_OFFER_SOURCE_CHAIN,
    UNRESOLVED_REQUEST_CURRENCY,
    UNRESOLVED_REQUEST_CURRENCY_AMBIGUOUS,
    UNRESOLVED_REQUEST_SOURCE,
    UNRESOLVED_SOURCE_SCHEMA,
    EquipmentDurableBlocked,
    SqlAlchemyEquipmentRepository,
)
from prodavan.application.migration.equipment_domain.service import EquipmentDomainService
from prodavan.application.migration.equipment_domain.transactions import (
    EquipmentTransactionResult,
    EquipmentTransactionStatus,
)
from .contracts import (
    AdapterError,
    AdapterErrorCode,
    ChatScopedCatalogSnapshot,
    ChatScopedEquipmentSnapshot,
    ChatScopedHumanSelection,
    ChatScopedSearchProjection,
    CreateRequestCommand,
    EquipmentActorKind,
    EquipmentApiScope,
    EquipmentCommand,
    EquipmentCommandKind,
    ExportProcurementCommand,
    PrepareEquipmentCommandResult,
    SelectOfferCommand,
)


#: Durable reason → stable API code.  Comprehensive by construction: a reason with
#: no entry falls back to ``DURABLE_UNAVAILABLE`` and is logged with its raw token.
REASON_TO_ERROR_CODE: Mapping[str, AdapterErrorCode] = {
    REASON_SELECTION_UNCHANGED: AdapterErrorCode.CONFLICT,
    REASON_SELECTION_CAS_CONFLICT: AdapterErrorCode.SELECTION_CONFLICT,
    REASON_SELECTION_LINE_MISSING: AdapterErrorCode.INVALID_QUERY,
    REASON_SELECTION_GROUP_AMBIGUOUS: AdapterErrorCode.SELECTION_MEMBERSHIP_INVALID,
    REASON_SELECTION_MEMBERSHIP_UNPROVEN: AdapterErrorCode.SELECTION_MEMBERSHIP_INVALID,
    REASON_SELECTION_IDEMPOTENCY_CONFLICT: AdapterErrorCode.IDEMPOTENCY_CONFLICT,
    REASON_SELECTED_OFFER_DETACHED: AdapterErrorCode.SELECTED_OFFER_DETACHED,
    REASON_REQUEST_BINDING_MISSING: AdapterErrorCode.REQUEST_NOT_FOUND,
    REASON_EXPORT_SELECTION_SET_CHANGED: AdapterErrorCode.SELECTION_CONFLICT,
    REASON_EXPORT_LINE_MISMATCH: AdapterErrorCode.STALE_PROJECTION,
    REASON_EXPORT_IDEMPOTENCY_CONFLICT: AdapterErrorCode.IDEMPOTENCY_CONFLICT,
    REASON_COMMIT_UNKNOWN: AdapterErrorCode.COMMIT_UNKNOWN,
    REASON_CONTEXT_MISSING: AdapterErrorCode.TRUSTED_CONTEXT_MISSING,
    REASON_CONTEXT_MISMATCH: AdapterErrorCode.TRUSTED_CONTEXT_MISSING,
    REASON_PROJECT_ANCHOR_MISSING: AdapterErrorCode.PROJECT_ANCHOR_MISSING,
    REASON_PRINCIPAL_MISMATCH: AdapterErrorCode.CROSS_TENANT,
    REASON_OWNER_MISMATCH: AdapterErrorCode.CROSS_TENANT,
    UNRESOLVED_REQUEST_SOURCE: AdapterErrorCode.INCOMPLETE_CANONICAL_EVIDENCE,
    UNRESOLVED_SOURCE_SCHEMA: AdapterErrorCode.INCOMPLETE_CANONICAL_EVIDENCE,
    UNRESOLVED_OFFER_SOURCE_CHAIN: AdapterErrorCode.INCOMPLETE_CANONICAL_EVIDENCE,
    UNRESOLVED_OFFER_GROUP_AMBIGUOUS: AdapterErrorCode.INCOMPLETE_CANONICAL_EVIDENCE,
    UNRESOLVED_REQUEST_CURRENCY: AdapterErrorCode.INCOMPLETE_CANONICAL_EVIDENCE,
    UNRESOLVED_REQUEST_CURRENCY_AMBIGUOUS: AdapterErrorCode.INCOMPLETE_CANONICAL_EVIDENCE,
}

#: Reason codes that mean "retry later" rather than "the request is wrong".
_RETRYABLE_REASONS = frozenset({
    REASON_COMMIT_UNKNOWN,
    AdapterErrorCode.DURABLE_UNAVAILABLE.value,
})

#: Catalog reads owned by ``equipment_ingestion/**`` in this wave.
CATALOG_UNAVAILABLE = "catalog_source_unavailable"


def error_for_reason(reason_code: str, *, retryable: bool | None = None) -> AdapterError:
    """Translate one durable reason token into a stable, payload-free error."""
    code = REASON_TO_ERROR_CODE.get(reason_code, AdapterErrorCode.DURABLE_UNAVAILABLE)
    if retryable is None:
        retryable = reason_code in _RETRYABLE_REASONS
    return AdapterError(code, message=f"durable equipment operation refused: {reason_code}",
                        retryable=retryable)


def outcome_for_result(result: EquipmentTransactionResult) -> dict[str, Any]:
    """Serialize a transaction receipt without leaking payloads."""
    return {
        "status": result.status.value,
        "durable": result.durable,
        "operation_id": result.operation_id,
        "reason_code": result.reason_code,
        "error_code": (REASON_TO_ERROR_CODE.get(result.reason_code).value
                       if result.reason_code in REASON_TO_ERROR_CODE else None),
    }


class _Principal(Protocol):
    tenant_id: str
    cabinet_id: str | None
    project_id: str | None
    subject_id: str
    role: str


def _actor_kind_for(principal: _Principal) -> EquipmentActorKind:
    """Derive the actor kind from the resolution path, never from the request."""
    source = str(getattr(principal, "resolution_source", ""))
    if source in {"session_cookie", "bearer_jwt_verified"}:
        return EquipmentActorKind.HUMAN
    if source == "internal_service_mtls":
        return EquipmentActorKind.SERVICE
    return EquipmentActorKind.AGENT


@dataclass(frozen=True, slots=True)
class _ResolvedScope:
    scope: DomainScope
    actor_id: str
    actor_kind: EquipmentActorKind


def resolve_scope(*, scope: EquipmentApiScope, principal: _Principal,
                  require_human: bool = True) -> _ResolvedScope:
    """Prove the request scope against the server-resolved principal.

    Tenant and project come from ``principal``; a differing request value is a
    ``cross_tenant`` denial, not a warning.  The owner comes from the durable
    config (a separate check inside the repository) and the actor kind from the
    resolution path.
    """
    if str(scope.tenant_id) != str(principal.tenant_id):
        raise AdapterError(AdapterErrorCode.CROSS_TENANT,
                           "request tenant is not the resolved tenant")
    if principal.project_id is None:
        raise AdapterError(AdapterErrorCode.TRUSTED_CONTEXT_MISSING,
                           "resolved principal carries no project scope")
    if str(scope.project_id) != str(principal.project_id):
        raise AdapterError(AdapterErrorCode.CROSS_TENANT,
                           "request project is not the resolved project")
    if str(scope.actor_id) != str(principal.subject_id):
        raise AdapterError(AdapterErrorCode.INVALID_SCOPE,
                           "command actor is not the resolved principal")
    derived = _actor_kind_for(principal)
    if scope.actor_kind is not None and scope.actor_kind is not derived:
        raise AdapterError(AdapterErrorCode.INVALID_SCOPE,
                           "request actor kind contradicts the resolution path")
    if require_human and derived is not EquipmentActorKind.HUMAN:
        raise AdapterError(AdapterErrorCode.HUMAN_SELECTION_REQUIRED,
                           "equipment decisions require an authenticated human principal")
    return _ResolvedScope(scope.domain_scope, str(principal.subject_id), derived)


class DurableEquipmentApiReadPort:
    """``EquipmentApiReadPort`` over durable equipment rows.

    ``repository`` must be constructed inside the caller's transaction with the
    trusted tenant context already applied; this class adds no scope widening.
    ``principal`` supplies the only actor identity this adapter will read
    selections for.
    """

    def __init__(self, *, repository: SqlAlchemyEquipmentRepository,
                 principal: _Principal) -> None:
        self._repository = repository
        self._principal = principal

    async def request_snapshot(self, *, scope: DomainScope, chat_id: str
                               ) -> ChatScopedEquipmentSnapshot | None:
        try:
            snapshot = await self._repository.load_snapshot(scope=scope)
        except EquipmentDurableBlocked as error:
            raise error_for_reason(error.reason_code) from error
        if snapshot is None:
            return None
        return ChatScopedEquipmentSnapshot(scope=scope, chat_id=chat_id, snapshot=snapshot)

    async def catalog_snapshots(self, *, scope: DomainScope, chat_id: str,
                                source_id: str | None = None
                                ) -> Sequence[ChatScopedCatalogSnapshot]:
        del scope, chat_id, source_id
        raise AdapterError(AdapterErrorCode.SNAPSHOT_UNAVAILABLE,
                           f"catalog persistence is owned elsewhere: {CATALOG_UNAVAILABLE}",
                           retryable=False)

    async def search_projection(self, *, scope: object,
                                chat_id: str) -> ChatScopedSearchProjection | None:
        del scope, chat_id
        # No durable search projection is written by this task; absence is
        # reported as absence, never synthesized from the domain projection.
        return None

    async def human_selections(self, *, scope: DomainScope, chat_id: str
                               ) -> Sequence[ChatScopedHumanSelection]:
        try:
            selections = await self._repository.load_selections(
                scope=scope, actor_id=str(self._principal.subject_id))
        except EquipmentDurableBlocked as error:
            raise error_for_reason(error.reason_code) from error
        return tuple(ChatScopedHumanSelection(chat_id=chat_id, selection=row)
                     for row in selections)


@dataclass(frozen=True, slots=True)
class DurableCommandOutcome:
    """Result of one durably executed command."""

    command_kind: EquipmentCommandKind
    command_id: str
    idempotency_key: str
    status: str
    durable: bool
    reason_code: str = ""
    error_code: str = ""
    selection: HumanSelection | None = None
    export_bundle: Any = None
    transaction: EquipmentTransactionResult | None = None

    @property
    def accepted(self) -> bool:
        return self.durable and self.status in {
            EquipmentTransactionStatus.COMMITTED.value,
            EquipmentTransactionStatus.REPLAYED.value,
        }


class DurableEquipmentCommandGateway:
    """Execute equipment decisions against durable ``equipment_v2_*`` state.

    Only selection, prepared export and request binding are executed here.
    Catalog refresh is owned by ``equipment_ingestion/**`` and returns
    ``write_disabled`` with an explicit reason instead of a silent no-op.
    """

    def __init__(self, *, service: EquipmentDomainService,
                 repository: SqlAlchemyEquipmentRepository,
                 principal: _Principal) -> None:
        self._service = service
        self._repository = repository
        self._principal = principal

    async def execute(self, command: EquipmentCommand) -> DurableCommandOutcome:
        if isinstance(command, SelectOfferCommand):
            return await self.select_offer(command)
        if isinstance(command, ExportProcurementCommand):
            return await self.export_procurement(command)
        if isinstance(command, CreateRequestCommand):
            return await self.bind_request(command)
        return self._write_disabled(command)

    # ------------------------------------------------------------------ select --

    async def select_offer(self, command: SelectOfferCommand) -> DurableCommandOutcome:
        resolved = resolve_scope(scope=command.scope, principal=self._principal)
        selection_id = _command_identity(prefix="sel", scope=resolved.scope,
                                         command_id=command.command_id)
        try:
            result = await self._service.select(
                scope=resolved.scope, actor_id=resolved.actor_id,
                selection_id=selection_id, line_id=command.line_id,
                tender_id=command.tender_id, reason=command.reason,
                supersedes_selection_id=command.supersedes_selection_id,
                manual_override=True,
            )
        except EquipmentDurableBlocked as error:
            raise error_for_reason(error.reason_code) from error
        except PermissionError as error:
            raise AdapterError(AdapterErrorCode.HUMAN_SELECTION_REQUIRED,
                               "selection requires an authenticated human principal") from error
        except ValueError as error:
            raise AdapterError(AdapterErrorCode.SELECTION_MEMBERSHIP_INVALID,
                               f"selection refused before write: {error}") from error
        return DurableCommandOutcome(
            command_kind=EquipmentCommandKind.SELECT_OFFER,
            command_id=command.command_id,
            idempotency_key=result.idempotency_key,
            status=result.status.value,
            durable=result.durable,
            reason_code=result.reason_code,
            error_code=(REASON_TO_ERROR_CODE.get(result.reason_code).value
                        if result.reason_code in REASON_TO_ERROR_CODE else ""),
            transaction=result,
        )

    # ------------------------------------------------------------------ export --

    async def export_procurement(
        self, command: ExportProcurementCommand,
    ) -> DurableCommandOutcome:
        resolved = resolve_scope(scope=command.scope, principal=self._principal)
        export_id = _command_identity(prefix="exp", scope=resolved.scope,
                                      command_id=command.command_id)
        try:
            bundle = await self._service.prepare_export_bundle(
                scope=resolved.scope, actor_id=resolved.actor_id, export_id=export_id)
        except EquipmentDurableBlocked as error:
            raise error_for_reason(error.reason_code) from error
        except PermissionError as error:
            raise AdapterError(AdapterErrorCode.HUMAN_SELECTION_REQUIRED,
                               "export requires an authenticated human principal") from error
        if not bundle.committable:
            # A partial/empty selection set is reported, never committed.
            raise AdapterError(
                AdapterErrorCode.EXPORT_NOT_READY,
                f"export is {bundle.status.value}: "
                f"{len(bundle.unselected_line_ids)} unselected line(s)",
            )
        if (command.expected_selection_digest is not None
                and command.expected_selection_digest != bundle.selection_digest):
            raise AdapterError(AdapterErrorCode.STALE_PROJECTION,
                               "expected selection digest does not match durable selections")
        if (command.expected_source_revision is not None
                and command.expected_source_revision != bundle.source_revision):
            raise AdapterError(AdapterErrorCode.STALE_PROJECTION,
                               "expected source revision does not match durable source")
        try:
            result = await self._service.commit_export(
                scope=resolved.scope, actor_id=resolved.actor_id, export_id=export_id)
        except EquipmentDurableBlocked as error:
            raise error_for_reason(error.reason_code) from error
        return DurableCommandOutcome(
            command_kind=EquipmentCommandKind.EXPORT_PROCUREMENT,
            command_id=command.command_id,
            idempotency_key=result.idempotency_key,
            status=result.status.value, durable=result.durable,
            reason_code=result.reason_code,
            error_code=(REASON_TO_ERROR_CODE.get(result.reason_code).value
                        if result.reason_code in REASON_TO_ERROR_CODE else ""),
            export_bundle=bundle, transaction=result,
        )

    # ------------------------------------------------------------------ binding --

    async def bind_request(self, command: CreateRequestCommand) -> DurableCommandOutcome:
        """Bind an existing durable request row to its project/source evidence.

        Request row creation itself belongs to the cabinet/module API; this stops
        at proving the row exists in ``equipment_v2_requests`` and recording the
        append-only binding, which is what a later selection needs.
        """
        resolved = resolve_scope(scope=command.scope, principal=self._principal,
                                require_human=False)
        try:
            bound = await self._repository.bind_request(
                scope=resolved.scope, request_id=command.request_id,
                source_id=command.source_id or "",
                source_schema_version=command.source_schema_version,
            )
        except EquipmentDurableBlocked as error:
            raise error_for_reason(error.reason_code) from error
        key = _stable_digest({"operation": "equipment.request.bind",
                              "scope": resolved.scope, "command_id": command.command_id})
        return DurableCommandOutcome(
            command_kind=EquipmentCommandKind.CREATE_REQUEST,
            command_id=command.command_id, idempotency_key=key,
            status=EquipmentTransactionStatus.COMMITTED.value if bound else "blocked",
            durable=bound,
        )

    # ------------------------------------------------------------------ helpers --

    def _write_disabled(self, command: EquipmentCommand
                        ) -> DurableCommandOutcome:
        """Refuse a command kind this task does not own, with a named reason."""
        reason = {
            EquipmentCommandKind.CREATE_CANDIDATE_GROUP: "candidate_group_write_owned_elsewhere",
            EquipmentCommandKind.REFRESH_CATALOG: "catalog_refresh_owned_by_ingestion",
            EquipmentCommandKind.REFRESH_OFFERS: "catalog_refresh_owned_by_ingestion",
        }.get(getattr(command, "command_kind", None), "equipment_command_not_owned")
        return DurableCommandOutcome(
            command_kind=_command_kind(command),
            command_id=str(getattr(command, "command_id", "")),
            idempotency_key=_stable_digest({"command": getattr(command, "command_id", ""),
                                            "reason": reason}),
            status=EquipmentTransactionStatus.BLOCKED.value,
            durable=False, reason_code=reason,
            error_code=AdapterErrorCode.WRITE_DISABLED.value,
        )


def _command_kind(command: EquipmentCommand) -> EquipmentCommandKind:
    return {
        CreateRequestCommand: EquipmentCommandKind.CREATE_REQUEST,
        SelectOfferCommand: EquipmentCommandKind.SELECT_OFFER,
        ExportProcurementCommand: EquipmentCommandKind.EXPORT_PROCUREMENT,
    }.get(type(command), EquipmentCommandKind.CREATE_CANDIDATE_GROUP)


def _command_identity(*, prefix: str, scope: DomainScope, command_id: str) -> str:
    """Deterministic durable identity for one scoped command.

    The same ``command_id`` always yields the same selection/export identity, so
    the repository's idempotency key is stable across retries; a changed payload
    then conflicts instead of creating a second decision.
    """
    digest = _stable_digest({"operation": f"equipment.{prefix}", "scope": scope,
                             "command_id": command_id})[7:27]
    return f"{prefix}_{digest}"


__all__ = [
    "CATALOG_UNAVAILABLE",
    "DurableCommandOutcome",
    "DurableEquipmentApiReadPort",
    "DurableEquipmentCommandGateway",
    "REASON_TO_ERROR_CODE",
    "error_for_reason",
    "outcome_for_result",
    "resolve_scope",
]
