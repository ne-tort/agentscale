"""Pydantic request/response schemas for the Content API (wire contract).

Wire shape is unchanged where a client already depends on it (the web
``content-ops`` feature and the module/document upload adapters read flat
objects, not a ``data``/``meta`` envelope). New operations follow
``docs/02-architecture/api-style.md``: plural nouns, kebab paths, ``PATCH`` for
partial updates, cursor pagination with ``next_cursor``/``has_more``, and stable
RFC 7807 error codes raised as ``domain.errors.AppError``.

Kept in the application package so the migration acceptance suite can validate
the schema without importing the HTTP router.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from prodavan.application.content.contracts import (
    MAX_ALIAS_LABEL_LEN,
    MAX_IDEMPOTENCY_KEY_LEN,
    MAX_MIME_LEN,
    MAX_PRINCIPAL_ID_LEN,
    MAX_TAGS,
    MAX_TAG_LEN,
    MAX_TITLE_LEN,
    VERSION_PAGE_MAX,
    VERSION_TTL_DEFAULT,
    VERSION_TTL_MAX,
    VERSION_TTL_MIN,
    ContentPrincipalKind,
)

Strict = ConfigDict(extra="forbid")


# --- assets -----------------------------------------------------------------


class CreateAssetBody(BaseModel):
    model_config = Strict

    owner_company_id: str | None = None
    visibility: Literal["private", "company"] = "company"
    mime: str | None = Field(default=None, max_length=MAX_MIME_LEN)
    title: str | None = Field(default=None, max_length=MAX_TITLE_LEN)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)

    @field_validator("tags")
    @classmethod
    def _tag_lengths(cls, value: list[str]) -> list[str]:
        for tag in value:
            if len(tag) > MAX_TAG_LEN:
                raise ValueError(f"tag exceeds {MAX_TAG_LEN} characters")
        return value


class PatchAssetBody(BaseModel):
    model_config = Strict

    title: str | None = Field(default=None, max_length=MAX_TITLE_LEN)
    tags: list[str] | None = Field(default=None, max_length=MAX_TAGS)
    visibility: Literal["private", "company"] | None = None

    @field_validator("tags")
    @classmethod
    def _tag_lengths(cls, value: list[str] | None) -> list[str] | None:
        for tag in value or ():
            if len(tag) > MAX_TAG_LEN:
                raise ValueError(f"tag exceeds {MAX_TAG_LEN} characters")
        return value


class AssetResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    owner_scope: str
    owner_company_id: str | None = None
    visibility: str
    mime: str | None = None
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_by: str | None = None
    latest_version: int | None = None
    latest_version_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AssetList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AssetResource] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False


class AssetDependents(BaseModel):
    """What a destructive ``DELETE /content/assets/{id}`` would have to remove.

    This is the retained evidence for the blocked archive path: the caller can
    see the live alias bindings, the entity links and the version count before
    deciding on a platform-admin purge.
    """

    model_config = ConfigDict(extra="forbid")

    asset_id: str
    version_count: int = 0
    active_alias_bindings: list[str] = Field(default_factory=list)
    binding_ids: list[str] = Field(default_factory=list)
    binding_count: int = 0
    asset_links: list[str] = Field(default_factory=list)
    safe_to_purge: bool = False
    archive_available: bool = False
    archive_blocker: str
    evidence_policy: str


# --- versions ---------------------------------------------------------------


class BeginVersionBody(BaseModel):
    model_config = Strict

    mime: str | None = Field(default=None, max_length=MAX_MIME_LEN)
    ttl_seconds: int = Field(
        default=VERSION_TTL_DEFAULT, ge=VERSION_TTL_MIN, le=VERSION_TTL_MAX
    )
    sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        description="optional digest the uploaded bytes must match",
    )


class VersionRead(BaseModel):
    """A version as returned by *read* endpoints (detail and history).

    Deliberately has no ``storage_key``: the internal object-store key is only
    handed out where a client has to write the bytes itself — the
    ``begin version`` presigned-upload response (see :class:`VersionResource`).
    Read endpoints used to reuse that model, so every detail/page carried
    ``storage_key: null``; a null-only field is still a published wire contract
    and it invites clients to depend on a key the API never intends to serve.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    asset_id: str
    version: int
    size: int = 0
    sha256: str | None = None
    etag: str | None = None
    object_metadata: dict[str, Any] = Field(default_factory=dict)
    sealed: bool = False
    created_at: str | None = None


class VersionResource(VersionRead):
    """A version as returned by *write* endpoints (begin/upload/finalize).

    Adds ``storage_key`` for callers that emulate the presigned PUT themselves
    (tests/integration/test_content.py, module upload adapters). Removed once
    those callers stop reading it.
    """

    storage_key: str | None = None


class BeginVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: VersionResource
    upload_url: str
    method: str = "PUT"
    sealed: bool = False


class VersionList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[VersionRead] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    limit: int = Field(default=50, ge=1, le=VERSION_PAGE_MAX)


class VersionAbortResult(BaseModel):
    """Result of aborting an unsealed version (staged upload cleanup)."""

    model_config = ConfigDict(extra="forbid")

    aborted: bool
    asset_id: str
    version_id: str
    version: int
    bytes_removed: bool = False


# --- aliases ----------------------------------------------------------------


class CreateAliasBody(BaseModel):
    model_config = Strict

    slug: str = Field(min_length=3, max_length=128)
    owner_company_id: str | None = None
    visibility: Literal["private", "company"] = "company"
    label: str | None = Field(default=None, max_length=MAX_ALIAS_LABEL_LEN)
    description: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PatchAliasBody(BaseModel):
    model_config = Strict

    label: str | None = Field(default=None, max_length=MAX_ALIAS_LABEL_LEN)
    description: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] | None = None
    status: Literal["active", "disabled"] | None = None
    visibility: Literal["private", "company"] | None = None


class BindAliasBody(BaseModel):
    model_config = Strict

    asset_id: str
    blob_version_id: str | None = None


class AliasResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    slug: str
    owner_scope: str
    owner_company_id: str | None = None
    visibility: str
    label: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: str | None = None
    updated_at: str | None = None


class AliasBindingResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    alias_id: str
    asset_id: str
    blob_version_id: str | None = None
    effective_at: str | None = None
    superseded_at: str | None = None
    bound_by: str | None = None
    replayed: bool | None = None


class AliasBindingList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: AliasResource | None = None
    items: list[AliasBindingResource] = Field(default_factory=list)


# --- ACL --------------------------------------------------------------------


class AclEntryBody(BaseModel):
    model_config = Strict

    principal_kind: Literal["employee", "company", "platform", "cabinet", "project", "role"]
    principal_id: str = Field(min_length=1, max_length=MAX_PRINCIPAL_ID_LEN)
    permission: Literal["read", "write", "admin"] = "read"


class ReplaceAclBody(BaseModel):
    model_config = Strict

    entries: list[AclEntryBody] = Field(default_factory=list, max_length=256)


class AclEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    principal_kind: ContentPrincipalKind
    principal_id: str
    permission: str


class AclList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_kind: str
    resource_id: str
    entries: list[AclEntry] = Field(default_factory=list)


# --- links ------------------------------------------------------------------


class CreateAssetLinkBody(BaseModel):
    model_config = Strict

    link_kind: Literal["project_attachment", "project", "module", "module_catalog"] = (
        "project_attachment"
    )
    link_id: str = Field(min_length=1, max_length=128)
    replace: bool = Field(
        default=False,
        description="re-point an existing (link_kind, link_id) edge owned by another asset",
    )


class AssetLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    asset_id: str
    link_kind: str
    link_id: str
    created_at: str | None = None
    replayed: bool | None = None


class AssetLinkList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AssetLink] = Field(default_factory=list)


class IdempotencyHeader(BaseModel):
    """Documentation model: the ``Idempotency-Key`` header contract."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LEN)


__all__ = [
    "AclEntry",
    "AclEntryBody",
    "AclList",
    "AliasBindingList",
    "AliasBindingResource",
    "AliasResource",
    "AssetDependents",
    "AssetLink",
    "AssetLinkList",
    "AssetList",
    "AssetResource",
    "BeginVersionBody",
    "BeginVersionResponse",
    "BindAliasBody",
    "CreateAliasBody",
    "CreateAssetBody",
    "CreateAssetLinkBody",
    "IdempotencyHeader",
    "PatchAliasBody",
    "PatchAssetBody",
    "ReplaceAclBody",
    "VersionAbortResult",
    "VersionList",
    "VersionResource",
]
