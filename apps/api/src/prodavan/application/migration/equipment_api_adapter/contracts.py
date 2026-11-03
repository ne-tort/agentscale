"""Typed project/chat-scoped API contracts for the equipment shadow adapter."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, TypeAlias

from prodavan.application.migration.equipment_domain.domain import DomainScope, HumanSelection
from prodavan.application.migration.equipment_domain.service import EquipmentDomainSnapshot
from prodavan.application.migration.equipment_ingestion.ingestion import CatalogSnapshot
from prodavan.application.migration.equipment_ingestion.search_projection import EquipmentSearchProjection


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class AdapterErrorCode(StrEnum):
    INVALID_SCOPE = "invalid_scope"
    INVALID_QUERY = "invalid_query"
    INVALID_CURSOR = "invalid_cursor"
    SNAPSHOT_UNAVAILABLE = "snapshot_unavailable"
    SNAPSHOT_SCOPE_MISMATCH = "snapshot_scope_mismatch"
    INCOMPLETE_CANONICAL_EVIDENCE = "incomplete_canonical_evidence"
    HUMAN_SELECTION_REQUIRED = "human_selection_required"
    WRITE_DISABLED = "write_disabled"
    CONFLICT = "conflict"
    LEGACY_ROW_INVALID = "legacy_row_invalid"


class EquipmentActorKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"


class AdapterError(ValueError):
    """Stable, API-serializable adapter failure; message carries no row payload."""

    def __init__(self, code: AdapterErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "retryable": self.retryable}


def _require_id(value: object, name: str) -> str:
    if (not isinstance(value, str) or value.strip() != value
            or not _ID_RE.fullmatch(value)):
        raise AdapterError(AdapterErrorCode.INVALID_QUERY, f"{name} is required")
    return value


def _optional_id(value: str | None, name: str) -> None:
    if value is not None:
        _require_id(value, name)


def _optional_digest(value: str | None, name: str) -> None:
    if value is not None and (not isinstance(value, str) or not _DIGEST_RE.fullmatch(value)):
        raise AdapterError(AdapterErrorCode.INVALID_QUERY, f"{name} must be a sha256 digest")


def _optional_revision(value: str | None, name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise AdapterError(AdapterErrorCode.INVALID_QUERY, f"{name} cannot be empty")


@dataclass(frozen=True, slots=True)
class EquipmentApiScope:
    """Authenticated route scope, including exact active chat and SoT owner."""

    tenant_id: str
    owner_id: str
    project_id: str
    chat_id: str
    actor_id: str
    actor_kind: EquipmentActorKind | None = None

    def __post_init__(self) -> None:
        for name in ("tenant_id", "owner_id", "project_id", "chat_id", "actor_id"):
            value = getattr(self, name)
            if (not isinstance(value, str) or value.strip() != value
                    or not _ID_RE.fullmatch(value)):
                raise AdapterError(AdapterErrorCode.INVALID_SCOPE, f"{name} is required")
        if self.actor_kind is not None and not isinstance(self.actor_kind, EquipmentActorKind):
            raise AdapterError(AdapterErrorCode.INVALID_SCOPE, "actor_kind is invalid")

    @property
    def domain_scope(self) -> DomainScope:
        return DomainScope(self.tenant_id, self.owner_id, self.project_id)


@dataclass(frozen=True, slots=True)
class ChatScopedEquipmentSnapshot:
    scope: DomainScope
    chat_id: str
    snapshot: EquipmentDomainSnapshot

    def __post_init__(self) -> None:
        _require_id(self.chat_id, "chat_id")
        if getattr(self.snapshot, "scope", None) != self.scope:
            raise AdapterError(AdapterErrorCode.SNAPSHOT_SCOPE_MISMATCH,
                               "snapshot scope does not match its chat partition")


@dataclass(frozen=True, slots=True)
class ChatScopedCatalogSnapshot:
    chat_id: str
    snapshot: CatalogSnapshot

    def __post_init__(self) -> None:
        _require_id(self.chat_id, "chat_id")


@dataclass(frozen=True, slots=True)
class ChatScopedHumanSelection:
    chat_id: str
    selection: HumanSelection

    def __post_init__(self) -> None:
        _require_id(self.chat_id, "chat_id")


@dataclass(frozen=True, slots=True)
class ChatScopedSearchProjection:
    chat_id: str
    projection: EquipmentSearchProjection

    def __post_init__(self) -> None:
        _require_id(self.chat_id, "chat_id")


@dataclass(frozen=True, slots=True)
class Page:
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 200:
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "limit must be between 1 and 200")
        if self.cursor is not None and (
            not isinstance(self.cursor, str) or len(self.cursor) > 2048
            or not re.fullmatch(r"[A-Za-z0-9_-]+", self.cursor)
        ):
            raise AdapterError(AdapterErrorCode.INVALID_CURSOR, "cursor is invalid")


@dataclass(frozen=True, slots=True)
class RequestQuery:
    scope: EquipmentApiScope
    page: Page = Page()
    request_id: str | None = None

    def __post_init__(self) -> None:
        _optional_id(self.request_id, "request_id")


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    scope: EquipmentApiScope
    page: Page = Page()
    source_id: str | None = None

    def __post_init__(self) -> None:
        _optional_id(self.source_id, "source_id")


@dataclass(frozen=True, slots=True)
class CatalogOfferQuery:
    scope: EquipmentApiScope
    page: Page = Page()
    source_id: str | None = None
    snapshot_id: str | None = None
    supplier_id: str | None = None

    def __post_init__(self) -> None:
        _optional_id(self.source_id, "source_id")
        _optional_id(self.snapshot_id, "snapshot_id")
        _optional_id(self.supplier_id, "supplier_id")


@dataclass(frozen=True, slots=True)
class CandidateQuery:
    scope: EquipmentApiScope
    page: Page = Page()
    line_id: str | None = None

    def __post_init__(self) -> None:
        _optional_id(self.line_id, "line_id")


@dataclass(frozen=True, slots=True)
class OfferQuery:
    scope: EquipmentApiScope
    page: Page = Page()
    line_id: str | None = None
    candidate_group_id: str | None = None
    supplier_id: str | None = None
    include_stale: bool = True

    def __post_init__(self) -> None:
        _optional_id(self.line_id, "line_id")
        _optional_id(self.candidate_group_id, "candidate_group_id")
        _optional_id(self.supplier_id, "supplier_id")
        if not isinstance(self.include_stale, bool):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "include_stale must be boolean")


@dataclass(frozen=True, slots=True)
class ProcurementQuery:
    scope: EquipmentApiScope
    page: Page = Page()
    supplier_id: str | None = None

    def __post_init__(self) -> None:
        _optional_id(self.supplier_id, "supplier_id")


EquipmentQuery: TypeAlias = (RequestQuery | CatalogQuery | CatalogOfferQuery | CandidateQuery
                             | OfferQuery | ProcurementQuery)


@dataclass(frozen=True, slots=True)
class ApiPage:
    items: tuple[Mapping[str, Any], ...]
    next_cursor: str | None
    source_revision: str
    snapshot_digest: str
    compatibility_rows: tuple["CompatibilityRow", ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or not isinstance(self.compatibility_rows, tuple):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "page items must be immutable tuples")
        if not isinstance(self.source_revision, str) or not self.source_revision.strip():
            raise AdapterError(AdapterErrorCode.INCOMPLETE_CANONICAL_EVIDENCE,
                               "source revision is required")
        if not isinstance(self.snapshot_digest, str) or not _DIGEST_RE.fullmatch(self.snapshot_digest):
            raise AdapterError(AdapterErrorCode.INCOMPLETE_CANONICAL_EVIDENCE,
                               "snapshot digest is invalid")


@dataclass(frozen=True, slots=True)
class CompatibilityRow:
    """Legacy-shaped preview; row_id is not a legacy row ID and is never persisted."""

    table_slug: str
    row_id: str
    body: Mapping[str, Any]
    session_id: str
    read_only: bool = True
    provenance: Mapping[str, str] | None = None
    exact_legacy_equivalent: bool = False
    omitted_fields: tuple[str, ...] = ()
    legacy_row_id: str | None = None
    legacy_id_status: str = "unknown"

    def __post_init__(self) -> None:
        if self.read_only is not True or self.legacy_row_id is not None:
            raise AdapterError(AdapterErrorCode.WRITE_DISABLED,
                               "shadow compatibility rows are read-only and have no legacy ID")
        if self.legacy_id_status != "unknown":
            raise AdapterError(AdapterErrorCode.LEGACY_ROW_INVALID,
                               "legacy row identity must remain explicitly unknown")
        if (not isinstance(self.table_slug, str) or not self.table_slug.strip()
                or not isinstance(self.row_id, str) or not self.row_id.startswith("shadow:")
                or not isinstance(self.session_id, str) or not self.session_id.strip()):
            raise AdapterError(AdapterErrorCode.LEGACY_ROW_INVALID,
                               "compatibility preview identity is invalid")
        if not isinstance(self.body, Mapping) or self.body.get("session_id") != self.session_id:
            raise AdapterError(AdapterErrorCode.LEGACY_ROW_INVALID,
                               "compatibility preview body scope is invalid")
        if not isinstance(self.provenance, Mapping) or not self.provenance:
            raise AdapterError(AdapterErrorCode.LEGACY_ROW_INVALID,
                               "compatibility preview provenance is required")
        if self.exact_legacy_equivalent:
            raise AdapterError(AdapterErrorCode.LEGACY_ROW_INVALID,
                               "legacy equivalence has not been proven")

class EquipmentCommandKind(StrEnum):
    CREATE_REQUEST = "create_request"
    CREATE_CANDIDATE_GROUP = "create_candidate_group"
    SELECT_OFFER = "select_offer"
    REFRESH_CATALOG = "refresh_catalog"
    REFRESH_OFFERS = "refresh_offers"
    EXPORT_PROCUREMENT = "export_procurement"


@dataclass(frozen=True, slots=True)
class CreateRequestCommand:
    scope: EquipmentApiScope
    command_id: str
    request_id: str
    title: str
    currency: str
    created_at: str
    expected_source_revision: str | None = None
    expected_snapshot_digest: str | None = None
    canonical_registry_digest: str | None = None

    def __post_init__(self) -> None:
        _require_id(self.command_id, "command_id")
        _require_id(self.request_id, "request_id")
        if (not isinstance(self.title, str) or not self.title.strip()
                or not isinstance(self.currency, str) or not re.fullmatch(r"[A-Z]{3}", self.currency)):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "request title and currency are required")
        if not isinstance(self.created_at, str) or not self.created_at.strip():
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "created_at is required")
        try:
            parsed = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "created_at must be ISO-8601") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "created_at must include a timezone")
        _optional_revision(self.expected_source_revision, "expected_source_revision")
        _optional_digest(self.expected_snapshot_digest, "expected_snapshot_digest")
        _optional_digest(self.canonical_registry_digest, "canonical_registry_digest")


@dataclass(frozen=True, slots=True)
class CreateCandidateGroupCommand:
    scope: EquipmentApiScope
    command_id: str
    group_id: str
    line_id: str
    match_kind: str
    part_number: str | None = None
    aliases: tuple[str, ...] = ()
    note: str | None = None
    expected_source_revision: str | None = None
    expected_snapshot_digest: str | None = None
    canonical_registry_digest: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("command_id", "group_id", "line_id"):
            _require_id(getattr(self, field_name), field_name)
        if self.match_kind not in {"exact", "analog", "doubt"}:
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "candidate match_kind is unsupported")
        if self.part_number is not None and (not isinstance(self.part_number, str) or not self.part_number.strip()):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "candidate part number cannot be empty")
        if (not isinstance(self.aliases, tuple)
                or any(not isinstance(alias, str) or not alias.strip() for alias in self.aliases)):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "candidate aliases must be nonempty strings")
        if self.note is not None and (not isinstance(self.note, str) or not self.note.strip()):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "candidate note must be absent or nonempty")
        if not (self.part_number or self.aliases):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "candidate requires a part number or alias")
        _optional_revision(self.expected_source_revision, "expected_source_revision")
        _optional_digest(self.expected_snapshot_digest, "expected_snapshot_digest")
        _optional_digest(self.canonical_registry_digest, "canonical_registry_digest")


@dataclass(frozen=True, slots=True)
class SelectOfferCommand:
    scope: EquipmentApiScope
    command_id: str
    selection_id: str
    line_id: str
    tender_id: str
    reason: str
    supersedes_selection_id: str | None = None
    human_approval_id: str | None = None
    expected_projection_digest: str | None = None
    expected_source_revision: str | None = None
    canonical_registry_digest: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("command_id", "selection_id", "line_id", "tender_id"):
            _require_id(getattr(self, field_name), field_name)
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "selection reason is required")
        if self.scope.actor_kind is not EquipmentActorKind.HUMAN:
            raise AdapterError(AdapterErrorCode.HUMAN_SELECTION_REQUIRED,
                               "offer selection intent requires an authenticated human actor")
        _require_id(self.human_approval_id, "human_approval_id")
        _optional_id(self.supersedes_selection_id, "supersedes_selection_id")
        if not isinstance(self.expected_projection_digest, str) or not _DIGEST_RE.fullmatch(
                self.expected_projection_digest):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "expected_projection_digest is required")
        if not isinstance(self.expected_source_revision, str) or not self.expected_source_revision.strip():
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "expected_source_revision is required")
        _optional_digest(self.canonical_registry_digest, "canonical_registry_digest")


@dataclass(frozen=True, slots=True)
class RefreshCatalogCommand:
    scope: EquipmentApiScope
    command_id: str
    source_id: str
    source_snapshot_id: str | None = None
    expected_source_revision: str | None = None
    expected_snapshot_digest: str | None = None
    canonical_registry_digest: str | None = None

    def __post_init__(self) -> None:
        _require_id(self.command_id, "command_id")
        _require_id(self.source_id, "source_id")
        _optional_id(self.source_snapshot_id, "source_snapshot_id")
        _optional_revision(self.expected_source_revision, "expected_source_revision")
        _optional_digest(self.expected_snapshot_digest, "expected_snapshot_digest")
        _optional_digest(self.canonical_registry_digest, "canonical_registry_digest")


@dataclass(frozen=True, slots=True)
class RefreshOffersCommand:
    scope: EquipmentApiScope
    command_id: str
    source_snapshot_id: str
    line_ids: tuple[str, ...] = ()
    expected_source_revision: str | None = None
    expected_snapshot_digest: str | None = None
    canonical_registry_digest: str | None = None

    def __post_init__(self) -> None:
        _require_id(self.command_id, "command_id")
        _require_id(self.source_snapshot_id, "source_snapshot_id")
        if (not isinstance(self.line_ids, tuple)
                or any(not isinstance(value, str) or not _ID_RE.fullmatch(value) for value in self.line_ids)
                or len(set(self.line_ids)) != len(self.line_ids)):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "line_ids must be unique nonempty IDs")
        _optional_revision(self.expected_source_revision, "expected_source_revision")
        _optional_digest(self.expected_snapshot_digest, "expected_snapshot_digest")
        _optional_digest(self.canonical_registry_digest, "canonical_registry_digest")


@dataclass(frozen=True, slots=True)
class ExportProcurementCommand:
    scope: EquipmentApiScope
    command_id: str
    export_id: str
    human_approval_id: str | None = None
    format: str = "xlsx"
    expected_source_revision: str | None = None
    expected_selection_digest: str | None = None
    canonical_registry_digest: str | None = None

    def __post_init__(self) -> None:
        _require_id(self.command_id, "command_id")
        _require_id(self.export_id, "export_id")
        if self.format not in {"xlsx", "pdf"}:
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "unsupported procurement export format")
        if self.scope.actor_kind is not EquipmentActorKind.HUMAN:
            raise AdapterError(AdapterErrorCode.HUMAN_SELECTION_REQUIRED,
                               "procurement export intent requires an authenticated human actor")
        _require_id(self.human_approval_id, "human_approval_id")
        if not isinstance(self.expected_source_revision, str) or not self.expected_source_revision.strip():
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "expected_source_revision is required")
        if not isinstance(self.expected_selection_digest, str) or not _DIGEST_RE.fullmatch(
                self.expected_selection_digest):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "expected_selection_digest is required")
        _optional_digest(self.canonical_registry_digest, "canonical_registry_digest")


EquipmentCommand: TypeAlias = (
    CreateRequestCommand | CreateCandidateGroupCommand | SelectOfferCommand
    | RefreshCatalogCommand | RefreshOffersCommand | ExportProcurementCommand
)


class EquipmentIntentState(StrEnum):
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class EquipmentCommandEnvelope:
    version: str
    command_kind: EquipmentCommandKind
    scope: EquipmentApiScope
    command_id: str
    idempotency_key: str
    payload_digest: str
    payload: Mapping[str, Any]
    actor_id: str
    actor_kind: EquipmentActorKind | None
    expected_source_revision: str | None = None
    expected_snapshot_digest: str | None = None
    expected_selection_digest: str | None = None
    canonical_registry_digest: str | None = None

    def __post_init__(self) -> None:
        if self.version != "1" or not isinstance(self.command_kind, EquipmentCommandKind):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "command envelope version or kind is invalid")
        _require_id(self.command_id, "command_id")
        if not isinstance(self.idempotency_key, str) or not _DIGEST_RE.fullmatch(self.idempotency_key):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "idempotency_key is invalid")
        if not isinstance(self.payload_digest, str) or not _DIGEST_RE.fullmatch(self.payload_digest):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "payload_digest is invalid")
        if not isinstance(self.payload, Mapping):
            raise AdapterError(AdapterErrorCode.INVALID_QUERY, "command payload must be an object")
        if self.actor_id != self.scope.actor_id or self.actor_kind is not self.scope.actor_kind:
            raise AdapterError(AdapterErrorCode.INVALID_SCOPE, "command actor does not match authenticated scope")
        _optional_revision(self.expected_source_revision, "expected_source_revision")
        _optional_digest(self.expected_snapshot_digest, "expected_snapshot_digest")
        _optional_digest(self.expected_selection_digest, "expected_selection_digest")
        _optional_digest(self.canonical_registry_digest, "canonical_registry_digest")


@dataclass(frozen=True, slots=True)
class EquipmentIntent:
    """API-ready job/refresh/export intent; blocked until audited gates are proven."""

    command_kind: EquipmentCommandKind
    scope: EquipmentApiScope
    command_id: str
    idempotency_key: str
    payload_digest: str
    payload: Mapping[str, Any]
    state: EquipmentIntentState
    write_enabled: bool
    required_gates: tuple[str, ...]
    envelope_version: str = "1"
    envelope: EquipmentCommandEnvelope | None = None


@dataclass(frozen=True, slots=True)
class PrepareEquipmentCommandResult:
    command_kind: EquipmentCommandKind
    command_id: str
    idempotency_key: str
    accepted: bool
    write_enabled: bool
    required_gates: tuple[str, ...]
    reason_code: str
    payload_digest: str
    intent: EquipmentIntent | None = None


@dataclass(frozen=True, slots=True)
class CursorPosition:
    snapshot_digest: str
    offset: int

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_digest, str) or not _DIGEST_RE.fullmatch(self.snapshot_digest):
            raise AdapterError(AdapterErrorCode.INVALID_CURSOR, "cursor snapshot digest is invalid")
        if isinstance(self.offset, bool) or not isinstance(self.offset, int) or self.offset < 0:
            raise AdapterError(AdapterErrorCode.INVALID_CURSOR, "cursor offset is invalid")


def encode_cursor(position: CursorPosition) -> str:
    raw = json.dumps(
        {"v": 1, "snapshot": position.snapshot_digest, "offset": position.offset},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str | None, *, snapshot_digest: str) -> int:
    if value is None:
        return 0
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        decoded = json.loads(raw.decode("ascii"))
        if not isinstance(decoded, dict) or set(decoded) != {"v", "snapshot", "offset"}:
            raise ValueError
        offset = decoded["offset"]
        version = decoded.get("v")
        if (isinstance(version, bool) or not isinstance(version, int) or version != 1
                or decoded.get("snapshot") != snapshot_digest
                or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0):
            raise ValueError
        return offset
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error,
            json.JSONDecodeError) as error:
        raise AdapterError(AdapterErrorCode.INVALID_CURSOR, "cursor does not match this source snapshot") from error


def page_items(items: tuple[Any, ...], page: Page, *, snapshot_digest: str) -> tuple[tuple[Any, ...], str | None]:
    offset = decode_cursor(page.cursor, snapshot_digest=snapshot_digest)
    if offset > len(items):
        raise AdapterError(AdapterErrorCode.INVALID_CURSOR, "cursor offset exceeds the current result set")
    selected = items[offset:offset + page.limit]
    next_offset = offset + len(selected)
    next_cursor = encode_cursor(CursorPosition(snapshot_digest, next_offset)) if next_offset < len(items) else None
    return selected, next_cursor


__all__ = [
    "AdapterError", "AdapterErrorCode", "ApiPage", "CandidateQuery", "CatalogOfferQuery",
    "CatalogQuery", "ChatScopedCatalogSnapshot", "ChatScopedEquipmentSnapshot",
    "ChatScopedHumanSelection", "ChatScopedSearchProjection", "CompatibilityRow",
    "CreateCandidateGroupCommand", "CreateRequestCommand",
    "CursorPosition", "EquipmentActorKind", "EquipmentApiScope", "EquipmentCommand",
    "EquipmentCommandEnvelope", "EquipmentCommandKind",
    "EquipmentIntent", "EquipmentIntentState", "ExportProcurementCommand", "OfferQuery",
    "Page", "PrepareEquipmentCommandResult",
    "ProcurementQuery", "RefreshCatalogCommand", "RefreshOffersCommand", "RequestQuery",
    "SelectOfferCommand", "decode_cursor", "encode_cursor", "page_items",
]
