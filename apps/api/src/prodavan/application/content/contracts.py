"""Explicit Content API contract — stable codes, principal kinds, pure predicates.

This module holds data and *pure* decisions only: no I/O, no session, no ORM
access. Keeping the decisions here lets the migration acceptance suite assert
them without a database (see ``tests/migration/content_api``).

The HTTP surface, request bodies and response schemas live in
``prodavan.api.v1.content``; the authoritative prose contract lives in
``application/content/CONTRACT.md``.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence

from prodavan.domain.content.types import AclPermission, ContentVisibility
from prodavan.domain.identity.types import (
    ROLE_COMPANY,
    ROLE_COMPANY_ADMIN,
    ROLE_EMPLOYEE,
    ROLE_PLATFORM_ADMIN,
)

# --- limits -----------------------------------------------------------------

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_IDEMPOTENCY_KEY_LEN = 128
#: ``content_acl_entries.principal_id`` is ``String(40)``; rejecting longer ids
#: here keeps a bad payload a 422 instead of a database truncation error.
MAX_PRINCIPAL_ID_LEN = 40
MAX_LINK_KIND_LEN = 64
#: ``content_asset_links.link_id`` is ``String(40)`` (see the same note above).
MAX_LINK_ID_LEN = 40
MAX_MIME_LEN = 128
MAX_TITLE_LEN = 260
#: ``content_aliases.label`` is ``String(200)`` — narrower than an asset title.
MAX_ALIAS_LABEL_LEN = 200
MAX_TAGS = 64
MAX_TAG_LEN = 64
VERSION_PAGE_DEFAULT = 50
VERSION_PAGE_MAX = 100
VERSION_TTL_DEFAULT = 900
VERSION_TTL_MIN = 60
VERSION_TTL_MAX = 3600

# --- blockers ---------------------------------------------------------------

#: Traceable blocker slug carried in the problem ``extra`` of the archive reply.
ARCHIVE_BLOCKER_SLUG = "content_assets_archive_columns_missing"
ARCHIVE_BLOCKER = (
    "CONTENT_ARCHIVE_UNAVAILABLE (content_assets_archive_columns_missing): "
    "content_assets has no status, archived_at or deleted_at column, so a soft "
    "archive cannot be persisted and an archived asset cannot be listed, "
    "restored or excluded from read paths. A reviewed revision adding nullable "
    "archived_at (plus an index used by read/list filters) is required before "
    "DELETE /content/assets/{id} can succeed. This slice authors no DDL and no "
    "migration revision."
)
IDEMPOTENCY_BLOCKER = (
    "content_idempotency_receipts_table_missing: there is no content-scoped "
    "idempotency receipt table, so creation idempotency is derived from a "
    "deterministic resource id plus an equality check of the persisted request "
    "projection. That covers repeated creates with the same key and rejects a "
    "different body, but it cannot record the original response body verbatim. "
    "A receipt table is only required for byte-identical response replay."
)
STORAGE_KEY_EXPOSURE = (
    "content_version_storage_key_exposed: begin/upload version responses still "
    "return storage_key for compatibility with tests/integration/test_content.py "
    "and the module upload adapters. Removing it requires migrating those callers."
)

# --- principal kinds --------------------------------------------------------


class ContentPrincipalKind(StrEnum):
    """ACL principal kinds accepted by the Content Service.

    ``employee``/``company``/``platform`` are the historical kinds persisted by
    earlier revisions. ``cabinet``/``project``/``role`` are the tenant scoped
    kinds added by this contract; the storage column is a plain string so no DDL
    is needed.
    """

    EMPLOYEE = "employee"
    COMPANY = "company"
    PLATFORM = "platform"
    CABINET = "cabinet"
    PROJECT = "project"
    ROLE = "role"


SUPPORTED_PRINCIPAL_KINDS: frozenset[str] = frozenset(kind.value for kind in ContentPrincipalKind)
PERMITTED_PERMISSIONS: frozenset[str] = frozenset(
    permission.value for permission in AclPermission
)
KNOWN_ROLES: frozenset[str] = frozenset(
    {ROLE_PLATFORM_ADMIN, ROLE_COMPANY, ROLE_EMPLOYEE, ROLE_COMPANY_ADMIN, "member"}
)
PLATFORM_PRINCIPAL_ID = "platform"
PLATFORM_KIND_REQUIRED = (ContentPrincipalKind.PLATFORM, PLATFORM_PRINCIPAL_ID)


# --- link kinds -------------------------------------------------------------


class ContentLinkKind(StrEnum):
    """``content_asset_links.link_kind`` values with a defined owner."""

    PROJECT_ATTACHMENT = "project_attachment"
    PROJECT = "project"
    MODULE = "module"
    MODULE_CATALOG = "module_catalog"
    DOCUMENTS = "documents"


#: Kinds a caller may create through the public asset-link route.
PUBLIC_LINK_KINDS: frozenset[str] = frozenset(
    {
        ContentLinkKind.PROJECT_ATTACHMENT.value,
        ContentLinkKind.PROJECT.value,
        ContentLinkKind.MODULE.value,
        ContentLinkKind.MODULE_CATALOG.value,
    }
)
#: Kinds written only by owning domain services (never by a caller).
INTERNAL_LINK_KINDS: frozenset[str] = frozenset({ContentLinkKind.DOCUMENTS.value})

#: Cabinet/project scoped link kinds whose target row carries ``company_id``.
TENANT_SCOPED_LINK_KINDS: frozenset[str] = frozenset(
    {
        ContentLinkKind.PROJECT_ATTACHMENT.value,
        ContentLinkKind.PROJECT.value,
    }
)


# --- stable error codes -----------------------------------------------------


class ContentErrorCode(StrEnum):
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CONFLICT = "CONFLICT"
    NOT_READY = "NOT_READY"
    IDEMPOTENCY_REPLAY = "IDEMPOTENCY_REPLAY"
    CONTENT_ARCHIVE_UNAVAILABLE = "CONTENT_ARCHIVE_UNAVAILABLE"
    CONTENT_DELETE_HAS_DEPENDENTS = "CONTENT_DELETE_HAS_DEPENDENTS"
    CONTENT_VERSION_SEALED = "CONTENT_VERSION_SEALED"
    CONTENT_VERSION_NOT_SEALED = "CONTENT_VERSION_NOT_SEALED"
    CONTENT_VERSION_CONFLICT = "CONTENT_VERSION_CONFLICT"
    CONTENT_DIGEST_MISMATCH = "CONTENT_DIGEST_MISMATCH"
    CONTENT_UPLOAD_TOO_LARGE = "CONTENT_UPLOAD_TOO_LARGE"
    CONTENT_ACL_PRINCIPAL_UNSUPPORTED = "CONTENT_ACL_PRINCIPAL_UNSUPPORTED"
    CONTENT_ACL_PERMISSION_UNSUPPORTED = "CONTENT_ACL_PERMISSION_UNSUPPORTED"
    CONTENT_ACL_CROSS_TENANT = "CONTENT_ACL_CROSS_TENANT"
    CONTENT_ACL_DUPLICATE = "CONTENT_ACL_DUPLICATE"
    CONTENT_ACL_PLATFORM_GRANT_FORBIDDEN = "CONTENT_ACL_PLATFORM_GRANT_FORBIDDEN"
    CONTENT_LINK_KIND_UNSUPPORTED = "CONTENT_LINK_KIND_UNSUPPORTED"
    CONTENT_LINK_TARGET_CONFLICT = "CONTENT_LINK_TARGET_CONFLICT"
    CONTENT_ALIAS_VISIBILITY_UNSUPPORTED = "CONTENT_ALIAS_VISIBILITY_UNSUPPORTED"


#: Stable code returned by ``DELETE /content/assets/{id}`` without ``purge``.
#: Derived from the catalogue so the wire code and the enum cannot drift.
ARCHIVE_BLOCKER_CODE = ContentErrorCode.CONTENT_ARCHIVE_UNAVAILABLE.value


# --- canonical request projections -----------------------------------------


def _normalize_tags(tags: Iterable[str] | None) -> list[str]:
    seen: list[str] = []
    for tag in tags or ():
        text = str(tag).strip()
        if text and text not in seen:
            seen.append(text)
    return sorted(seen)


def normalize_tags(tags: Iterable[str] | None) -> list[str]:
    """Public form of the tag projection: stripped, de-duplicated, sorted.

    Persisted rows and idempotency projections must use the same function or a
    replay with ``[" b", "a "]`` would not match the stored ``["a", "b"]``.
    """
    return _normalize_tags(tags)


def canonical_asset_request(
    *,
    owner_company_id: str | None,
    visibility: str,
    mime: str | None,
    title: str | None,
    tags: Sequence[str] | None,
) -> dict[str, Any]:
    """Projection of ``POST /content/assets`` that identifies the request.

    Replay compares this projection with the persisted asset row, so an
    ``Idempotency-Key`` reused for a different body is detectable without a
    receipt table.
    """
    return {
        "owner_company_id": owner_company_id,
        "owner_scope": "company" if owner_company_id else "platform",
        "visibility": visibility,
        "mime": mime,
        "title": title,
        "tags": _normalize_tags(tags),
    }


def canonical_asset_row(row: Any) -> dict[str, Any]:
    """Same projection derived from a persisted ``ContentAssetRow``."""
    return canonical_asset_request(
        owner_company_id=getattr(row, "owner_company_id", None),
        visibility=getattr(row, "visibility", ""),
        mime=getattr(row, "mime", None),
        title=getattr(row, "title", None),
        tags=list(getattr(row, "tags", None) or []),
    )


def canonical_alias_request(
    *,
    slug: str,
    owner_company_id: str | None,
    visibility: str,
    label: str | None,
    description: str | None,
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "slug": slug,
        "owner_company_id": owner_company_id,
        "owner_scope": "company" if owner_company_id else "platform",
        "visibility": visibility,
        "label": label,
        "description": description,
        "metadata": _stable_json(metadata or {}),
    }


def canonical_alias_row(row: Any) -> dict[str, Any]:
    return canonical_alias_request(
        slug=getattr(row, "slug", ""),
        owner_company_id=getattr(row, "owner_company_id", None),
        visibility=getattr(row, "visibility", ""),
        label=getattr(row, "label", None),
        description=getattr(row, "description", None),
        metadata=getattr(row, "metadata_", None) or {},
    )


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def projections_match(stored: Mapping[str, Any], requested: Mapping[str, Any]) -> bool:
    """True when a replayed request projects onto the persisted resource."""
    if set(stored) != set(requested):
        return False
    return all(stored[key] == requested[key] for key in requested)


#: Projection keys that describe *current blob state*, not the create request.
#:
#: ``assets.mime`` is rewritten by every version upload and finalize
#: (``asset.mime = mime`` / ``head.content_type``), so after a single upload it no
#: longer says what the create body said — it usually says nothing there, because
#: ``mime`` is optional at create time. Keeping it in the replay identity meant a
#: perfectly legitimate retry of ``POST /content/assets`` was answered with
#: ``409 IDEMPOTENCY_REPLAY`` merely because a version had been uploaded in
#: between. Replay therefore compares the fields that only the create request can
#: set: tenant, owner scope, visibility, title and tags.
_ASSET_REPLAY_IGNORED_KEYS = frozenset({"mime"})


def asset_projections_match(stored: Mapping[str, Any], requested: Mapping[str, Any]) -> bool:
    """Replay comparison for ``POST /content/assets`` (see the note above)."""
    comparable = {
        key: value for key, value in requested.items() if key not in _ASSET_REPLAY_IGNORED_KEYS
    }
    if set(stored) != set(requested):
        return False
    return all(stored[key] == value for key, value in comparable.items())


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- pure policy predicates -------------------------------------------------


def asset_visible_to_tenants(
    *,
    owner_scope: str,
    owner_company_id: str | None,
    tenant_ids: Iterable[str],
    is_platform_admin: bool,
) -> bool:
    """Tenant boundary for a content asset.

    Platform owned assets are published content: the tenant boundary does not
    apply and the ACL decides. A company owned asset is invisible (``404``, not
    ``403``) outside the caller's tenants so existence never leaks.
    """
    if is_platform_admin:
        return True
    if owner_scope == ContentVisibility.COMPANY or owner_company_id is not None:
        return bool(owner_company_id) and owner_company_id in set(tenant_ids)
    return True


def alias_visible_to_tenants(
    *,
    owner_company_id: str | None,
    tenant_ids: Iterable[str],
    is_platform_admin: bool,
) -> bool:
    if is_platform_admin:
        return True
    if owner_company_id is None:
        return True
    return owner_company_id in set(tenant_ids)


def acl_grant_shallow_problem(
    *,
    principal_kind: str,
    principal_id: str,
    permission: str,
    resource_tenant_id: str | None,
    actor_is_platform_admin: bool,
) -> str | None:
    """Structural validation of one ACL entry; returns an error code or None.

    Row-scoped checks (does the cabinet/project/employee exist, does it belong to
    the same tenant) stay in ``AccessPolicyService`` because they need a session.
    """
    if principal_kind not in SUPPORTED_PRINCIPAL_KINDS:
        return ContentErrorCode.CONTENT_ACL_PRINCIPAL_UNSUPPORTED.value
    if permission not in PERMITTED_PERMISSIONS:
        return ContentErrorCode.CONTENT_ACL_PERMISSION_UNSUPPORTED.value
    grantee = (principal_id or "").strip()
    if not grantee or len(grantee) > MAX_PRINCIPAL_ID_LEN:
        return ContentErrorCode.VALIDATION_ERROR.value
    if principal_kind == ContentPrincipalKind.PLATFORM.value:
        if grantee != PLATFORM_PRINCIPAL_ID:
            return ContentErrorCode.VALIDATION_ERROR.value
        if not actor_is_platform_admin:
            return ContentErrorCode.CONTENT_ACL_PLATFORM_GRANT_FORBIDDEN.value
    if principal_kind == ContentPrincipalKind.ROLE.value:
        if grantee not in KNOWN_ROLES:
            return ContentErrorCode.CONTENT_ACL_PRINCIPAL_UNSUPPORTED.value
        if grantee == ROLE_PLATFORM_ADMIN and not actor_is_platform_admin:
            return ContentErrorCode.CONTENT_ACL_PLATFORM_GRANT_FORBIDDEN.value
    if (
        principal_kind == ContentPrincipalKind.COMPANY.value
        and resource_tenant_id
        and grantee != resource_tenant_id
        and not actor_is_platform_admin
    ):
        # A tenant admin can only grant inside its own tenant. Cross-tenant
        # sharing is a platform decision and needs platform.admin.
        return ContentErrorCode.CONTENT_ACL_CROSS_TENANT.value
    if (
        principal_kind in (ContentPrincipalKind.CABINET.value, ContentPrincipalKind.PROJECT.value)
        and not resource_tenant_id
        and not actor_is_platform_admin
    ):
        return ContentErrorCode.CONTENT_ACL_CROSS_TENANT.value
    return None


def acl_duplicate_keys(entries: Iterable[Mapping[str, Any]]) -> set[tuple[str, str, str]]:
    """Return duplicated ``(kind, id, permission)`` triples in one payload."""
    seen: set[tuple[str, str, str]] = set()
    duplicates: set[tuple[str, str, str]] = set()
    for entry in entries:
        key = (
            str(entry.get("principal_kind", "")),
            str(entry.get("principal_id", "")),
            str(entry.get("permission", "")),
        )
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    return duplicates


def link_kind_is_caller_assignable(link_kind: str) -> bool:
    return link_kind in PUBLIC_LINK_KINDS


def normalize_link_kind(link_kind: str) -> str:
    return (link_kind or "").strip().lower()


__all__ = [
    "ARCHIVE_BLOCKER",
    "ARCHIVE_BLOCKER_CODE",
    "ARCHIVE_BLOCKER_SLUG",
    "IDEMPOTENCY_BLOCKER",
    "STORAGE_KEY_EXPOSURE",
    "MAX_UPLOAD_BYTES",
    "MAX_IDEMPOTENCY_KEY_LEN",
    "MAX_ALIAS_LABEL_LEN",
    "VERSION_PAGE_DEFAULT",
    "VERSION_PAGE_MAX",
    "VERSION_TTL_DEFAULT",
    "VERSION_TTL_MAX",
    "VERSION_TTL_MIN",
    "ContentPrincipalKind",
    "ContentLinkKind",
    "ContentErrorCode",
    "SUPPORTED_PRINCIPAL_KINDS",
    "PERMITTED_PERMISSIONS",
    "KNOWN_ROLES",
    "PUBLIC_LINK_KINDS",
    "INTERNAL_LINK_KINDS",
    "TENANT_SCOPED_LINK_KINDS",
    "canonical_asset_request",
    "canonical_asset_row",
    "canonical_alias_request",
    "canonical_alias_row",
    "asset_projections_match",
    "projections_match",
    "digest_of",
    "asset_visible_to_tenants",
    "alias_visible_to_tenants",
    "acl_grant_shallow_problem",
    "acl_duplicate_keys",
    "link_kind_is_caller_assignable",
    "normalize_link_kind",
    "normalize_tags",
]
