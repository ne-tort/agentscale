"""Module meta documents (platform DB JSONB)."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_meta_validator import (
    validate_document_body,
    validate_merged_slug_map,
)
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.modules import ModuleMetaDocumentRow, ModuleRow

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _ensure_json_body(body: Any) -> Any:
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


class ModuleMetaDocumentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _require_module(self, module_id: str) -> ModuleRow:
        row = await self._session.get(ModuleRow, module_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="module not found")
        return row

    async def list_documents(self, *, module_id: str) -> list[dict]:
        await self._require_module(module_id)
        q = await self._session.execute(
            select(ModuleMetaDocumentRow)
            .where(ModuleMetaDocumentRow.module_id == module_id)
            .order_by(ModuleMetaDocumentRow.slug)
        )
        return [
            {
                "slug": row.slug,
                "body": row.body,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in q.scalars().all()
        ]

    async def get_document(self, *, module_id: str, slug: str) -> dict:
        slug = _check_slug(slug)
        await self._require_module(module_id)
        q = await self._session.execute(
            select(ModuleMetaDocumentRow).where(
                ModuleMetaDocumentRow.module_id == module_id,
                ModuleMetaDocumentRow.slug == slug,
            )
        )
        row = q.scalar_one_or_none()
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="document not found")
        return {
            "slug": row.slug,
            "body": row.body,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    async def put_document(self, *, module_id: str, slug: str, body: Any) -> dict:
        slug = _check_slug(slug)
        body = _ensure_json_body(body)
        validate_document_body(slug, body)
        await self._require_module(module_id)
        q = await self._session.execute(
            select(ModuleMetaDocumentRow).where(ModuleMetaDocumentRow.module_id == module_id)
        )
        slug_map = {row.slug: row.body for row in q.scalars().all()}
        slug_map[slug] = body
        validate_merged_slug_map(slug_map)
        q = await self._session.execute(
            select(ModuleMetaDocumentRow).where(
                ModuleMetaDocumentRow.module_id == module_id,
                ModuleMetaDocumentRow.slug == slug,
            )
        )
        row = q.scalar_one_or_none()
        if row is None:
            row = ModuleMetaDocumentRow(module_id=module_id, slug=slug, body=body)
            self._session.add(row)
        else:
            row.body = body
        await self._session.commit()
        return await self.get_document(module_id=module_id, slug=slug)

    async def delete_document(self, *, module_id: str, slug: str) -> None:
        slug = _check_slug(slug)
        await self._require_module(module_id)
        q = await self._session.execute(
            select(ModuleMetaDocumentRow).where(
                ModuleMetaDocumentRow.module_id == module_id,
                ModuleMetaDocumentRow.slug == slug,
            )
        )
        row = q.scalar_one_or_none()
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="document not found")
        await self._session.delete(row)
        await self._session.commit()
