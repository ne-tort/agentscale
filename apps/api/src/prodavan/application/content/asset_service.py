"""AssetService — logical assets, immutable digest-bound blob versions.

Invariants enforced here (see ``CONTRACT.md`` §3):

* ``owner_scope`` is derived from the validated ``owner_company_id``; the client
  never supplies it.
* A caller-selected ``owner_company_id`` is validated against the tenant set the
  server derived from the principal (``scope.ContentActorScope``); a foreign
  tenant yields ``404``, never ``403``.
* A blob version is immutable once its digest is recorded. Changing bytes
  requires a **new** version row; re-uploading into a sealed version is
  ``409 CONTENT_VERSION_SEALED``. A declared digest that does not match the
  received bytes is ``422 CONTENT_DIGEST_MISMATCH`` and no bytes are stored.
* ``POST /content/assets`` is idempotent per ``Idempotency-Key``: the asset id is
  derived from ``(actor, tenant, key)`` and the replay compares the persisted
  request projection. Same key + different body → ``409 IDEMPOTENCY_REPLAY``.
* Archive / soft delete is **blocked**: ``content_assets`` has no
  ``archived_at``/``status`` column (see ``contracts.ARCHIVE_BLOCKER``). The
  public route fails closed; the current hard delete stays available only to
  owning domain services and to a platform-admin explicit purge.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.access_policy import AccessPolicyService
from prodavan.application.content.contracts import (
    ARCHIVE_BLOCKER,
    ARCHIVE_BLOCKER_CODE,
    MAX_IDEMPOTENCY_KEY_LEN,
    MAX_MIME_LEN,
    MAX_TAGS,
    MAX_TAG_LEN,
    MAX_TITLE_LEN,
    MAX_UPLOAD_BYTES,
    VERSION_PAGE_DEFAULT,
    VERSION_PAGE_MAX,
    VERSION_TTL_DEFAULT,
    VERSION_TTL_MAX,
    VERSION_TTL_MIN,
    ContentErrorCode,
    canonical_asset_request,
    canonical_asset_row,
    digest_of,
    projections_match,
)
from prodavan.application.content.pagination import decode_cursor, encode_cursor
from prodavan.application.content.scope import (
    ContentActorScope,
    asset_owner_scope,
    asset_tenant_visible,
    forbidden,
    not_found,
    resolve_actor_scope,
)
from prodavan.domain.content.types import ContentVisibility
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.files.keys import new_blob_key
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.content import (
    ContentAliasBindingRow,
    ContentAssetLinkRow,
    ContentAssetRow,
    ContentBlobVersionRow,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_HEX_RE = re.compile(r"^[0-9a-f]{16}$")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def asset_public(
    row: ContentAssetRow,
    *,
    latest_version: int | None = None,
    latest_version_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "owner_scope": row.owner_scope,
        "owner_company_id": row.owner_company_id,
        "visibility": row.visibility,
        "mime": row.mime,
        "title": row.title,
        "tags": list(row.tags or []),
        "created_by": row.created_by,
        "latest_version": latest_version,
        "latest_version_id": latest_version_id,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def version_public(row: ContentBlobVersionRow, *, include_storage_key: bool = False) -> dict[str, Any]:
    metadata = dict(row.object_metadata or {})
    sealed = bool(metadata.get("sealed")) or row.sha256 is not None
    out: dict[str, Any] = {
        "id": row.id,
        "asset_id": row.asset_id,
        "version": row.version,
        "size": row.size,
        "sha256": row.sha256,
        "etag": row.etag,
        "object_metadata": metadata,
        "sealed": sealed,
        "created_at": _iso(row.created_at),
    }
    if include_storage_key:
        out["storage_key"] = row.storage_key
    return out


class AssetService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._acl = AccessPolicyService(session)

    # -- helpers ------------------------------------------------------------

    async def _scope(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None,
    ) -> ContentActorScope:
        if scope is not None:
            return scope
        return await resolve_actor_scope(self._session, principal=principal, employee=employee)

    @staticmethod
    def _validate_asset_fields(
        *,
        visibility: str,
        mime: str | None,
        title: str | None,
        tags: list | None,
    ) -> None:
        if visibility not in {str(v) for v in ContentVisibility}:
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="visibility must be private|company",
            )
        if mime is not None and len(mime) > MAX_MIME_LEN:
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="mime too long",
            )
        if title is not None and len(title) > MAX_TITLE_LEN:
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="title too long",
            )
        if tags is not None:
            if len(tags) > MAX_TAGS:
                raise AppError(
                    code=ContentErrorCode.VALIDATION_ERROR,
                    title="Validation Error",
                    status=422,
                    detail="too many tags",
                )
            for tag in tags:
                if not isinstance(tag, str) or len(tag) > MAX_TAG_LEN:
                    raise AppError(
                        code=ContentErrorCode.VALIDATION_ERROR,
                        title="Validation Error",
                        status=422,
                        detail="invalid tag",
                    )

    @staticmethod
    def _asset_id_for(*, actor_id: str, tenant_id: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(
            f"content-asset\0{actor_id}\0{tenant_id}\0{idempotency_key}".encode("utf-8")
        ).hexdigest()
        return f"cast_{digest[:16]}"

    @staticmethod
    def _check_idempotency_key(key: str | None) -> str | None:
        if key is None:
            return None
        text = key.strip()
        if not text or len(text) > MAX_IDEMPOTENCY_KEY_LEN:
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail=f"Idempotency-Key must contain 1..{MAX_IDEMPOTENCY_KEY_LEN} characters",
            )
        return text

    # -- assets -------------------------------------------------------------

    async def create(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        owner_company_id: str | None,
        visibility: str = ContentVisibility.COMPANY,
        mime: str | None = None,
        title: str | None = None,
        tags: list | None = None,
        scope: ContentActorScope | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        actor.require_employee()
        self._validate_asset_fields(visibility=visibility, mime=mime, title=title, tags=tags)
        tenant_id = actor.tenant_or_404_for(owner_company_id, detail="company not found")
        if tenant_id is None and not actor.is_platform_admin:
            # A company tenant cannot create platform-published assets.
            raise forbidden("platform-owned assets require platform.admin")
        key = self._check_idempotency_key(idempotency_key)

        requested = canonical_asset_request(
            owner_company_id=tenant_id,
            visibility=str(visibility),
            mime=mime,
            title=title,
            tags=tags,
        )

        if key is not None:
            asset_id = self._asset_id_for(
                actor_id=actor.employee_id or principal.sub,
                tenant_id=tenant_id or "platform",
                idempotency_key=key,
            )
            existing = await self._session.get(ContentAssetRow, asset_id)
            if existing is not None:
                if not projections_match(canonical_asset_row(existing), requested):
                    raise AppError(
                        code=ContentErrorCode.IDEMPOTENCY_REPLAY,
                        title="Idempotency replay",
                        status=409,
                        detail="Idempotency-Key was already used with a different body",
                    )
                latest, latest_id = await self._latest_version(asset_id)
                return asset_public(existing, latest_version=latest, latest_version_id=latest_id)
        else:
            asset_id = None

        row = ContentAssetRow(
            owner_scope=asset_owner_scope(owner_company_id=tenant_id),
            owner_company_id=tenant_id,
            visibility=str(visibility),
            mime=mime,
            title=title,
            tags=sorted({str(tag) for tag in (tags or [])}),
            created_by=actor.employee_id,
        )
        if asset_id is not None:
            row.id = asset_id
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return asset_public(row, latest_version=None, latest_version_id=None)

    async def get(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        row = await self._session.get(ContentAssetRow, asset_id)
        if row is None or not asset_tenant_visible(row, actor):
            raise not_found("asset not found")
        await self._acl.require_read_asset(
            principal=principal, employee=employee, asset=row, scope=actor
        )
        latest, latest_id = await self._latest_version(asset_id)
        return asset_public(row, latest_version=latest, latest_version_id=latest_id)

    async def list_for_company(
        self,
        *,
        company_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        limit: int = 50,
        cursor: str | None = None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        tenant_id = actor.tenant_or_404_for(company_id, detail="company not found")
        size = max(1, min(limit, 200))
        try:
            after = decode_cursor(cursor)
        except ValueError as exc:
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="malformed cursor",
            ) from exc

        stmt = select(ContentAssetRow).where(ContentAssetRow.owner_company_id == tenant_id)
        if after is not None:
            created_at, row_id = after
            if created_at is None:
                stmt = stmt.where(ContentAssetRow.id < row_id)
            else:
                stmt = stmt.where(
                    or_(
                        ContentAssetRow.created_at < created_at,
                        and_(
                            ContentAssetRow.created_at == created_at,
                            ContentAssetRow.id < row_id,
                        ),
                    )
                )
        stmt = stmt.order_by(
            ContentAssetRow.created_at.desc(), ContentAssetRow.id.desc()
        ).limit(size + 1)

        q = await self._session.execute(stmt)
        rows = list(q.scalars().all())
        has_more = len(rows) > size
        rows = rows[:size]

        items: list[dict[str, Any]] = []
        for row in rows:
            if not await self._acl.can_read_asset(
                principal=principal, employee=employee, asset=row, scope=actor
            ):
                continue
            latest, latest_id = await self._latest_version(row.id)
            items.append(asset_public(row, latest_version=latest, latest_version_id=latest_id))

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(created_at=last.created_at, row_id=last.id)
        return {"items": items, "next_cursor": next_cursor, "has_more": has_more}

    async def patch(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        title: str | None = None,
        tags: list | None = None,
        visibility: str | None = None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        row = await self._session.get(ContentAssetRow, asset_id)
        if row is None or not asset_tenant_visible(row, actor):
            raise not_found("asset not found")
        await self._acl.require_write_asset(
            principal=principal, employee=employee, asset=row, scope=actor
        )
        self._validate_asset_fields(
            visibility=visibility if visibility is not None else row.visibility,
            mime=row.mime,
            title=title,
            tags=tags,
        )
        if title is not None:
            row.title = title
        if tags is not None:
            row.tags = sorted({str(tag) for tag in tags})
        if visibility is not None:
            row.visibility = str(visibility)
        await self._session.commit()
        await self._session.refresh(row)
        latest, latest_id = await self._latest_version(asset_id)
        return asset_public(row, latest_version=latest, latest_version_id=latest_id)

    # -- archive / purge ----------------------------------------------------

    async def archive(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        """Soft delete is not persistable yet — fail closed with a stable code."""
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        row = await self._session.get(ContentAssetRow, asset_id)
        if row is None or not asset_tenant_visible(row, actor):
            raise not_found("asset not found")
        await self._acl.require_admin_asset(
            principal=principal, employee=employee, asset=row, scope=actor
        )
        raise AppError(
            code=ARCHIVE_BLOCKER_CODE,
            title="Content archive unavailable",
            status=409,
            detail=ARCHIVE_BLOCKER,
            extra={"blocker": "content_assets_archive_columns_missing"},
        )

    async def dependency_report(self, asset_id: str) -> dict[str, Any]:
        """Evidence retained for a delete decision (rows are never removed here)."""
        bindings = await self._session.execute(
            select(ContentAliasBindingRow.id).where(
                ContentAliasBindingRow.asset_id == asset_id,
                ContentAliasBindingRow.superseded_at.is_(None),
            )
        )
        links = await self._session.execute(
            select(ContentAssetLinkRow.id).where(ContentAssetLinkRow.asset_id == asset_id)
        )
        versions = await self._session.execute(
            select(func.count(ContentBlobVersionRow.id)).where(
                ContentBlobVersionRow.asset_id == asset_id
            )
        )
        active_bindings = list(bindings.scalars().all())
        asset_links = list(links.scalars().all())
        return {
            "asset_id": asset_id,
            "active_alias_bindings": active_bindings,
            "asset_links": asset_links,
            "version_count": int(versions.scalar_one() or 0),
            "safe_to_purge": not active_bindings and not asset_links,
        }

    async def purge(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        cascade: bool = False,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        """Explicit destructive purge. Platform admin only, dependents denied."""
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        if not actor.is_platform_admin:
            row = await self._session.get(ContentAssetRow, asset_id)
            if row is None or not asset_tenant_visible(row, actor):
                raise not_found("asset not found")
            raise forbidden("destructive delete requires platform.admin")
        row = await self._session.get(ContentAssetRow, asset_id)
        if row is None:
            raise not_found("asset not found")
        report = await self.dependency_report(asset_id)
        if not report["safe_to_purge"] and not cascade:
            raise AppError(
                code=ContentErrorCode.CONTENT_DELETE_HAS_DEPENDENTS,
                title="Content asset has dependents",
                status=409,
                detail="asset is referenced by alias bindings or asset links; pass cascade=true to detach",
                extra={
                    "active_alias_bindings": report["active_alias_bindings"],
                    "asset_links": report["asset_links"],
                },
            )
        if cascade:
            q = await self._session.execute(
                select(ContentAssetLinkRow).where(ContentAssetLinkRow.asset_id == asset_id)
            )
            for link in q.scalars().all():
                await self._session.delete(link)
            q = await self._session.execute(
                select(ContentAliasBindingRow).where(ContentAliasBindingRow.asset_id == asset_id)
            )
            for binding in q.scalars().all():
                binding.superseded_at = datetime.now(UTC)
        return await self._hard_delete(row, report=report)

    async def delete(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        """Hard delete for **owning domain services** (e.g. project attachments).

        Not reachable from the public HTTP route: ``DELETE /content/assets/{id}``
        calls :meth:`archive` (blocked) or :meth:`purge` (platform admin).
        """
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        row = await self._session.get(ContentAssetRow, asset_id)
        if row is None or not asset_tenant_visible(row, actor):
            raise not_found("asset not found")
        await self._acl.require_admin_asset(
            principal=principal, employee=employee, asset=row, scope=actor
        )
        report = await self.dependency_report(asset_id)
        return await self._hard_delete(row, report=report)

    async def _hard_delete(self, row: ContentAssetRow, *, report: dict[str, Any]) -> dict[str, Any]:
        public = asset_public(row)
        q = await self._session.execute(
            select(ContentBlobVersionRow.storage_key).where(
                ContentBlobVersionRow.asset_id == row.id
            )
        )
        keys = [key for key in q.scalars().all() if key]
        await self._session.delete(row)
        await self._session.commit()
        store = ensure_file_store()
        for key in keys:
            await store.delete(key)
        return {
            "deleted": True,
            "deleted_versions": len(keys),
            "dependents": report,
            **public,
        }

    # -- versions -----------------------------------------------------------

    async def begin_version_upload(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        mime: str | None = None,
        ttl_seconds: int = VERSION_TTL_DEFAULT,
        declared_sha256: str | None = None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None or not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        await self._acl.require_write_asset(
            principal=principal, employee=employee, asset=asset, scope=actor
        )
        if mime is not None and len(mime) > MAX_MIME_LEN:
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="mime too long",
            )
        declared = self._normalize_digest(declared_sha256)
        ttl = max(VERSION_TTL_MIN, min(int(ttl_seconds), VERSION_TTL_MAX))

        version = await self._next_version_number(asset_id)
        storage_key = new_blob_key()
        store = ensure_file_store()
        upload_url = await store.presign_put(storage_key, ttl_seconds=ttl, content_type=mime)
        metadata: dict[str, Any] = {"sealed": False}
        if declared:
            metadata["declared_sha256"] = declared
        ver_row = ContentBlobVersionRow(
            asset_id=asset_id,
            version=version,
            storage_key=storage_key,
            size=0,
            object_metadata=metadata,
        )
        self._session.add(ver_row)
        if mime:
            asset.mime = mime
        await self._session.commit()
        await self._session.refresh(ver_row)
        return {
            "version": version_public(ver_row, include_storage_key=True),
            "upload_url": upload_url,
            "method": "PUT",
            "sealed": False,
        }

    async def upload_version_bytes(
        self,
        *,
        asset_id: str,
        version_id: str,
        data: bytes,
        principal: Principal,
        employee: EmployeeRow | None,
        mime: str | None = None,
        expected_sha256: str | None = None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        """Store bytes once and seal the version's digest.

        A sealed version rejects further bytes (``409 CONTENT_VERSION_SEALED``);
        the caller must open a new version row. ``expected_sha256`` (from the
        ``X-Content-SHA256`` header) and the digest declared at ``begin`` are both
        compared before any byte is written.
        """
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None or not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        await self._acl.require_write_asset(
            principal=principal, employee=employee, asset=asset, scope=actor
        )
        ver = await self._require_version(asset_id=asset_id, version_id=version_id)
        if self._is_sealed(ver):
            raise AppError(
                code=ContentErrorCode.CONTENT_VERSION_SEALED,
                title="Version sealed",
                status=409,
                detail="version digest is immutable; create a new version instead",
                extra={"version_id": ver.id, "sha256": ver.sha256},
            )
        if len(data) > MAX_UPLOAD_BYTES:
            raise AppError(
                code=ContentErrorCode.CONTENT_UPLOAD_TOO_LARGE,
                title="Payload too large",
                status=413,
                detail="upload exceeds 100 MiB limit",
            )
        actual = digest_of(data)
        declared = (ver.object_metadata or {}).get("declared_sha256")
        expected = self._normalize_digest(expected_sha256)
        for candidate in (declared, expected):
            if candidate and candidate != actual:
                raise AppError(
                    code=ContentErrorCode.CONTENT_DIGEST_MISMATCH,
                    title="Digest mismatch",
                    status=422,
                    detail="received bytes do not match the declared SHA-256",
                    extra={"expected_sha256": candidate, "actual_sha256": actual},
                )

        store = ensure_file_store()
        await store.put_bytes(ver.storage_key, data, content_type=mime)
        ver.size = len(data)
        ver.sha256 = actual
        ver.object_metadata = {**(ver.object_metadata or {}), "sealed": True}
        if mime:
            ver.etag = None
            asset.mime = mime
        await self._session.commit()
        await self._session.refresh(ver)
        return version_public(ver)

    async def finalize_version(
        self,
        *,
        asset_id: str,
        version_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        expected_sha256: str | None = None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        """Seal a presigned upload after verifying the stored bytes' digest."""
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        row = await self._session.get(ContentAssetRow, asset_id)
        if row is None or not asset_tenant_visible(row, actor):
            raise not_found("asset not found")
        await self._acl.require_write_asset(
            principal=principal, employee=employee, asset=row, scope=actor
        )
        ver = await self._require_version(asset_id=asset_id, version_id=version_id)
        store = ensure_file_store()
        try:
            head = await store.head(ver.storage_key)
        except FileNotFoundError as exc:
            raise AppError(
                code=ContentErrorCode.NOT_READY,
                title="Not Ready",
                status=422,
                detail="blob not uploaded yet",
            ) from exc
        try:
            raw = await store.get_bytes(ver.storage_key)
        except FileNotFoundError as exc:
            raise AppError(
                code=ContentErrorCode.NOT_READY,
                title="Not Ready",
                status=422,
                detail="blob bytes are unavailable for digest verification",
            ) from exc
        actual = digest_of(raw)
        if ver.sha256 and ver.sha256 != actual:
            raise AppError(
                code=ContentErrorCode.CONTENT_DIGEST_MISMATCH,
                title="Digest mismatch",
                status=422,
                detail="stored bytes no longer match the sealed SHA-256",
                extra={"expected_sha256": ver.sha256, "actual_sha256": actual},
            )
        declared = (ver.object_metadata or {}).get("declared_sha256")
        expected = self._normalize_digest(expected_sha256)
        for candidate in (declared, expected):
            if candidate and candidate != actual:
                raise AppError(
                    code=ContentErrorCode.CONTENT_DIGEST_MISMATCH,
                    title="Digest mismatch",
                    status=422,
                    detail="stored bytes do not match the declared SHA-256",
                    extra={"expected_sha256": candidate, "actual_sha256": actual},
                )
        ver.size = head.size
        ver.etag = head.etag
        ver.sha256 = actual
        ver.object_metadata = {**(ver.object_metadata or {}), "sealed": True}
        if head.content_type and not row.mime:
            row.mime = head.content_type
        await self._session.commit()
        await self._session.refresh(ver)
        return version_public(ver)

    async def list_versions(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        limit: int = VERSION_PAGE_DEFAULT,
        cursor: str | None = None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None or not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        await self._acl.require_read_asset(
            principal=principal, employee=employee, asset=asset, scope=actor
        )
        size = max(1, min(limit, VERSION_PAGE_MAX))
        before: int | None = None
        if cursor:
            try:
                after = decode_cursor(cursor)
            except ValueError as exc:
                raise AppError(
                    code=ContentErrorCode.VALIDATION_ERROR,
                    title="Validation Error",
                    status=422,
                    detail="malformed cursor",
                ) from exc
            if after is not None:
                # Version cursors carry the version number in ``row_id``.
                try:
                    before = int(after[1])
                except (TypeError, ValueError) as exc:
                    raise AppError(
                        code=ContentErrorCode.VALIDATION_ERROR,
                        title="Validation Error",
                        status=422,
                        detail="malformed cursor",
                    ) from exc
        stmt = select(ContentBlobVersionRow).where(ContentBlobVersionRow.asset_id == asset_id)
        if before is not None:
            stmt = stmt.where(ContentBlobVersionRow.version < before)
        stmt = stmt.order_by(ContentBlobVersionRow.version.desc()).limit(size + 1)
        q = await self._session.execute(stmt)
        rows = list(q.scalars().all())
        has_more = len(rows) > size
        rows = rows[:size]
        next_cursor = None
        if has_more and rows:
            next_cursor = encode_cursor(created_at=None, row_id=str(rows[-1].version))
        return {
            "items": [version_public(r) for r in rows],
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    async def get_version(
        self,
        *,
        asset_id: str,
        version_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None or not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        await self._acl.require_read_asset(
            principal=principal, employee=employee, asset=asset, scope=actor
        )
        ver = await self._require_version(asset_id=asset_id, version_id=version_id)
        return version_public(ver)

    async def resolve_blob_version(
        self,
        asset_id: str,
        *,
        blob_version_id: str | None = None,
    ) -> ContentBlobVersionRow:
        if blob_version_id:
            return await self._require_version(asset_id=asset_id, version_id=blob_version_id)
        q = await self._session.execute(
            select(ContentBlobVersionRow)
            .where(ContentBlobVersionRow.asset_id == asset_id)
            .order_by(ContentBlobVersionRow.version.desc())
            .limit(1)
        )
        ver = q.scalar_one_or_none()
        if ver is None:
            raise not_found("no blob version")
        return ver

    # -- internals ----------------------------------------------------------

    async def _require_version(self, *, asset_id: str, version_id: str) -> ContentBlobVersionRow:
        ver = await self._session.get(ContentBlobVersionRow, version_id)
        if ver is None or ver.asset_id != asset_id:
            raise not_found("version not found")
        return ver

    @staticmethod
    def _is_sealed(ver: ContentBlobVersionRow) -> bool:
        if ver.sha256:
            return True
        return bool((ver.object_metadata or {}).get("sealed"))

    @staticmethod
    def _normalize_digest(value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip().lower()
        if not text:
            return None
        if not _SHA256_RE.match(text):
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="sha256 must be 64 lowercase hex characters",
            )
        return text

    async def _latest_version(self, asset_id: str) -> tuple[int | None, str | None]:
        q = await self._session.execute(
            select(ContentBlobVersionRow.version, ContentBlobVersionRow.id)
            .where(ContentBlobVersionRow.asset_id == asset_id)
            .order_by(ContentBlobVersionRow.version.desc())
            .limit(1)
        )
        row = q.first()
        if row is None:
            return None, None
        return int(row[0]), str(row[1])

    async def _latest_version_number(self, asset_id: str) -> int | None:
        latest, _ = await self._latest_version(asset_id)
        return latest

    async def _next_version_number(self, asset_id: str) -> int:
        latest = await self._latest_version_number(asset_id)
        return (latest or 0) + 1


__all__ = ["AssetService", "asset_public", "version_public"]
