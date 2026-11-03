"""AliasService — permalink CRUD (no FileService).

An alias is a stable, human-readable pointer that is re-bound to asset versions
over time (see ``alias_binding_service``). Tenant rules are identical to assets:
the owner tenant is validated against the server principal and a foreign tenant
is ``404``.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.access_policy import AccessPolicyService
from prodavan.application.content.contracts import (
    MAX_ALIAS_LABEL_LEN,
    MAX_IDEMPOTENCY_KEY_LEN,
    ContentErrorCode,
    canonical_alias_request,
    canonical_alias_row,
    projections_match,
)
from prodavan.application.content.pagination import decode_cursor, encode_cursor
from prodavan.application.content.scope import (
    ContentActorScope,
    alias_tenant_visible,
    forbidden,
    not_found,
    resolve_actor_scope,
)
from prodavan.domain.content.types import AliasStatus, ContentVisibility
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.ownership import OwnerScope
from prodavan.infrastructure.persistence.models.content import (
    ContentAliasBindingRow,
    ContentAliasRow,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,126}[a-z0-9]$")
_ALIAS_ID_RE = re.compile(r"^cal_[0-9a-f]{16}$")


def alias_public(row: ContentAliasRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "slug": row.slug,
        "owner_scope": row.owner_scope,
        "owner_company_id": row.owner_company_id,
        "visibility": row.visibility,
        "label": row.label,
        "description": row.description,
        "metadata": row.metadata_ or {},
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class AliasService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._acl = AccessPolicyService(session)

    @staticmethod
    def is_alias_id(value: str) -> bool:
        return bool(_ALIAS_ID_RE.match(value))

    @staticmethod
    def normalize_slug(slug: str) -> str:
        normalized = (slug or "").strip().lower()
        if not _SLUG_RE.match(normalized):
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="slug must be lowercase alphanumeric with . _ -",
            )
        return normalized

    @staticmethod
    def _alias_id_for(*, actor_id: str, tenant_id: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(
            f"content-alias\0{actor_id}\0{tenant_id}\0{idempotency_key}".encode("utf-8")
        ).hexdigest()
        return f"cal_{digest[:16]}"

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

    async def create(
        self,
        *,
        slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
        owner_company_id: str | None,
        visibility: str = ContentVisibility.COMPANY,
        label: str | None = None,
        description: str | None = None,
        metadata: dict | None = None,
        scope: ContentActorScope | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        actor.require_employee()
        normalized = self.normalize_slug(slug)
        if owner_company_id is not None and not str(owner_company_id).strip():
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="owner_company_id must be a company id or absent",
            )
        tenant_id = actor.tenant_or_404_for(owner_company_id, detail="company not found")
        if tenant_id is None and not actor.is_platform_admin:
            raise forbidden("platform-owned aliases require platform.admin")
        if visibility not in {str(v) for v in ContentVisibility}:
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="visibility must be private|company",
            )
        if str(visibility) == ContentVisibility.PRIVATE:
            # content_aliases has no created_by column, so "private" cannot be
            # enforced as creator-only. Refuse instead of silently publishing.
            raise AppError(
                code=ContentErrorCode.CONTENT_ALIAS_VISIBILITY_UNSUPPORTED,
                title="Unsupported alias visibility",
                status=422,
                detail=(
                    "aliases do not support private visibility: content_aliases has no "
                    "created_by column, so a per-creator restriction cannot be enforced"
                ),
            )
        if label is not None and len(label) > MAX_ALIAS_LABEL_LEN:
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="label too long",
            )
        key = None
        if idempotency_key is not None:
            key = idempotency_key.strip()
            if not key or len(key) > MAX_IDEMPOTENCY_KEY_LEN:
                raise AppError(
                    code=ContentErrorCode.VALIDATION_ERROR,
                    title="Validation Error",
                    status=422,
                    detail=f"Idempotency-Key must contain 1..{MAX_IDEMPOTENCY_KEY_LEN} characters",
                )

        requested = canonical_alias_request(
            slug=normalized,
            owner_company_id=tenant_id,
            visibility=str(visibility),
            label=label,
            description=description,
            metadata=metadata,
        )
        if key is not None:
            alias_id = self._alias_id_for(
                actor_id=actor.employee_id or principal.sub,
                tenant_id=tenant_id or "platform",
                idempotency_key=key,
            )
            existing = await self._session.get(ContentAliasRow, alias_id)
            if existing is not None:
                if not projections_match(canonical_alias_row(existing), requested):
                    raise AppError(
                        code=ContentErrorCode.IDEMPOTENCY_REPLAY,
                        title="Idempotency replay",
                        status=409,
                        detail="Idempotency-Key was already used with a different body",
                    )
                return alias_public(existing)
        else:
            alias_id = None

        # A slug is globally unique; a different actor reusing it must not learn
        # whether the existing alias belongs to another tenant.
        current = await self.get_by_slug_optional(normalized)
        if current is not None:
            if current.owner_company_id is None and not actor.is_platform_admin:
                # Platform-owned permalinks are not readable by tenants without a
                # grant, so reporting a conflict would disclose their existence.
                raise not_found("alias not found")
            if alias_tenant_visible(current, actor):
                raise AppError(
                    code=ContentErrorCode.CONFLICT,
                    title="Conflict",
                    status=409,
                    detail="slug already exists",
                )
            raise not_found("alias not found")

        row = ContentAliasRow(
            slug=normalized,
            owner_scope=OwnerScope.COMPANY if tenant_id else OwnerScope.PLATFORM,
            owner_company_id=tenant_id,
            visibility=str(visibility),
            label=label,
            description=description,
            metadata_=metadata or {},
            status=AliasStatus.ACTIVE,
        )
        if alias_id is not None:
            row.id = alias_id
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return alias_public(row)

    async def get_by_slug_optional(self, slug: str) -> ContentAliasRow | None:
        q = await self._session.execute(
            select(ContentAliasRow).where(ContentAliasRow.slug == slug)
        )
        return q.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> ContentAliasRow:
        row = await self.get_by_slug_optional(slug)
        if row is None:
            raise not_found("alias not found")
        return row

    async def get_by_id_or_slug(
        self,
        *,
        alias_id_or_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        if self.is_alias_id(alias_id_or_slug):
            return await self.get(
                alias_id=alias_id_or_slug, principal=principal, employee=employee, scope=scope
            )
        row = await self.get_by_slug_optional(alias_id_or_slug)
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        if row is None or not alias_tenant_visible(row, actor):
            raise not_found("alias not found")
        # Metadata of a disabled alias stays readable inside the tenant/ACL so the
        # owner can inspect what was disabled before re-enabling it. Resolving the
        # permalink to bytes remains blocked (``DownloadService`` keeps the
        # active-only check).
        await self._acl.require_read_alias(
            principal=principal, employee=employee, alias=row, scope=actor, allow_disabled=True
        )
        return alias_public(row)

    async def get(
        self,
        *,
        alias_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        row = await self._session.get(ContentAliasRow, alias_id)
        if row is None or not alias_tenant_visible(row, actor):
            raise not_found("alias not found")
        await self._acl.require_read_alias(
            principal=principal, employee=employee, alias=row, scope=actor, allow_disabled=True
        )
        return alias_public(row)

    async def require_management_row(
        self,
        *,
        alias_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> ContentAliasRow:
        """Alias row for an owner-level read (evidence, binding history).

        Requires ``write``/``admin`` access but accepts a disabled alias: the
        binding history is the evidence of what the permalink served and must
        stay inspectable after ``DELETE`` disabled it.
        """
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        row = await self._session.get(ContentAliasRow, alias_id)
        if row is None or not alias_tenant_visible(row, actor):
            raise not_found("alias not found")
        await self._acl.require_write_alias(
            principal=principal, employee=employee, alias=row, scope=actor
        )
        return row

    async def require_row(
        self,
        *,
        alias_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> ContentAliasRow:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        row = await self._session.get(ContentAliasRow, alias_id)
        if row is None or not alias_tenant_visible(row, actor):
            raise not_found("alias not found")
        return row

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
        if not (company_id or "").strip():
            # An empty selector would otherwise degrade into "platform-owned"
            # and list published aliases.
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="company_id is required",
            )
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
        from sqlalchemy import and_, or_

        stmt = select(ContentAliasRow).where(ContentAliasRow.owner_company_id == tenant_id)
        if after is not None:
            created_at, row_id = after
            if created_at is None:
                stmt = stmt.where(ContentAliasRow.id < row_id)
            else:
                stmt = stmt.where(
                    or_(
                        ContentAliasRow.created_at < created_at,
                        and_(
                            ContentAliasRow.created_at == created_at,
                            ContentAliasRow.id < row_id,
                        ),
                    )
                )
        stmt = stmt.order_by(ContentAliasRow.created_at.desc(), ContentAliasRow.id.desc())
        stmt = stmt.limit(size + 1)
        q = await self._session.execute(stmt)
        rows = list(q.scalars().all())
        has_more = len(rows) > size
        rows = rows[:size]
        items: list[dict[str, Any]] = []
        for row in rows:
            # Disabled aliases stay listed with ``status="disabled"``: DELETE
            # disables instead of destroying, so hiding the row would hide the
            # evidence that the permalink exists and was switched off.
            if await self._acl.can_read_alias(
                principal=principal,
                employee=employee,
                alias=row,
                scope=actor,
                allow_disabled=True,
            ):
                items.append(alias_public(row))
        next_cursor = None
        if has_more and rows:
            next_cursor = encode_cursor(created_at=rows[-1].created_at, row_id=rows[-1].id)
        return {"items": items, "next_cursor": next_cursor, "has_more": has_more}

    async def patch(
        self,
        *,
        alias_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        label: str | None = None,
        description: str | None = None,
        metadata: dict | None = None,
        status: str | None = None,
        visibility: str | None = None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        row = await self._session.get(ContentAliasRow, alias_id)
        if row is None or not alias_tenant_visible(row, actor):
            raise not_found("alias not found")
        await self._acl.require_write_alias(
            principal=principal, employee=employee, alias=row, scope=actor
        )
        if status is not None and status not in {str(s) for s in AliasStatus}:
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="status must be active|disabled",
            )
        if visibility is not None and visibility not in {str(v) for v in ContentVisibility}:
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="visibility must be private|company",
            )
        if visibility is not None and str(visibility) == ContentVisibility.PRIVATE:
            raise AppError(
                code=ContentErrorCode.CONTENT_ALIAS_VISIBILITY_UNSUPPORTED,
                title="Unsupported alias visibility",
                status=422,
                detail=(
                    "aliases do not support private visibility: content_aliases has no "
                    "created_by column, so a per-creator restriction cannot be enforced"
                ),
            )
        if label is not None:
            row.label = label
        if description is not None:
            row.description = description
        if metadata is not None:
            row.metadata_ = metadata
        if status is not None:
            row.status = status
        if visibility is not None:
            row.visibility = visibility
        await self._session.commit()
        await self._session.refresh(row)
        return alias_public(row)

    async def delete(
        self,
        *,
        alias_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        """Disable instead of destroying the permalink history.

        ``content_aliases.status`` already models ``disabled``; deleting the row
        would cascade away binding history, which is the evidence that an alias
        previously pointed at a specific asset version.
        """
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        row = await self._session.get(ContentAliasRow, alias_id)
        if row is None or not alias_tenant_visible(row, actor):
            raise not_found("alias not found")
        await self._acl.require_write_alias(
            principal=principal, employee=employee, alias=row, scope=actor
        )
        row.status = AliasStatus.DISABLED
        await self._session.commit()
        await self._session.refresh(row)
        retained = await self._session.execute(
            select(func.count(ContentAliasBindingRow.id)).where(
                ContentAliasBindingRow.alias_id == alias_id
            )
        )
        return {
            "deleted": True,
            "retained_evidence": True,
            "retained_bindings": int(retained.scalar_one() or 0),
            **alias_public(row),
        }


__all__ = ["AliasService", "alias_public"]
