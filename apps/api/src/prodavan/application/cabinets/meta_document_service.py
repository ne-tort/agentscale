"""Free-form meta documents (JSONB) — format-only validation."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.cabinets.sql import qident
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _ensure_json_body(body: Any) -> Any:
    """Accept dict/list (or JSON string); reject non-JSON scalars as top-level body."""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="body must be valid JSON",
            ) from exc
    if not isinstance(body, (dict, list)):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="body must be a JSON object or array",
        )
    # Round-trip to ensure JSONB-serializable.
    try:
        json.dumps(body)
    except (TypeError, ValueError) as exc:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="body is not JSON-serializable",
        ) from exc
    return body


def _check_slug(slug: str) -> str:
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad slug")
    return slug


class CabinetMetaDocumentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)

    async def list_documents(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        qschema = qident(inst.schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT slug, body, updated_at
                FROM {qschema}.meta_documents
                ORDER BY slug
                """
            )
        )
        return [
            {
                "slug": r.slug,
                "body": r.body,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in q.fetchall()
        ]

    async def get_document(
        self,
        *,
        cabinet_id: str,
        slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        slug = _check_slug(slug)
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        qschema = qident(inst.schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT slug, body, updated_at
                FROM {qschema}.meta_documents
                WHERE slug = :slug
                """
            ),
            {"slug": slug},
        )
        row = q.fetchone()
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="document not found")
        return {
            "slug": row.slug,
            "body": row.body,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    async def put_document(
        self,
        *,
        cabinet_id: str,
        slug: str,
        body: Any,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        slug = _check_slug(slug)
        body = _ensure_json_body(body)
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        qschema = qident(inst.schema_name)
        await self._session.execute(
            text(
                f"""
                INSERT INTO {qschema}.meta_documents (slug, body, updated_at)
                VALUES (:slug, CAST(:body AS jsonb), now())
                ON CONFLICT (slug) DO UPDATE
                SET body = EXCLUDED.body, updated_at = now()
                """
            ),
            {"slug": slug, "body": json.dumps(body)},
        )
        await self._session.commit()
        return await self.get_document(
            cabinet_id=cabinet_id, slug=slug, principal=principal, employee=employee
        )

    async def delete_document(
        self,
        *,
        cabinet_id: str,
        slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> None:
        slug = _check_slug(slug)
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        qschema = qident(inst.schema_name)
        result = await self._session.execute(
            text(f"DELETE FROM {qschema}.meta_documents WHERE slug = :slug"),
            {"slug": slug},
        )
        if result.rowcount == 0:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="document not found")
        await self._session.commit()
