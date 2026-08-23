"""Cabinet workspace docs (AGENTS / prompts) — source of truth for materialize (L06/L07)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.cabinets.sql import qident
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

ALLOWED_DOC_SLUGS = frozenset({"agents", "prompts_index"})
_MAX_BODY = 200_000


class CabinetWorkspaceDocsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)

    async def _ensure_table(self, schema_name: str) -> None:
        qschema = qident(schema_name)
        await self._session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {qschema}.meta_workspace_docs (
                    slug TEXT PRIMARY KEY,
                    body TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )

    async def list_docs(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        await self._ensure_table(inst.schema_name)
        qschema = qident(inst.schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT slug, body, updated_at
                FROM {qschema}.meta_workspace_docs
                ORDER BY slug
                """
            )
        )
        return [
            {
                "slug": r.slug,
                "body": r.body or "",
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "body_len": len(r.body or ""),
            }
            for r in q.fetchall()
        ]

    async def get_doc(
        self,
        *,
        cabinet_id: str,
        slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        slug = slug.strip().lower()
        if slug not in ALLOWED_DOC_SLUGS:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="unknown workspace doc")
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        await self._ensure_table(inst.schema_name)
        qschema = qident(inst.schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT slug, body, updated_at
                FROM {qschema}.meta_workspace_docs
                WHERE slug = :slug
                """
            ),
            {"slug": slug},
        )
        row = q.fetchone()
        if row is None:
            return {"slug": slug, "body": "", "updated_at": None}
        return {
            "slug": row.slug,
            "body": row.body or "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    async def put_doc(
        self,
        *,
        cabinet_id: str,
        slug: str,
        body: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        slug = slug.strip().lower()
        if slug not in ALLOWED_DOC_SLUGS:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"slug must be one of: {', '.join(sorted(ALLOWED_DOC_SLUGS))}",
            )
        if len(body) > _MAX_BODY:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="body too large")
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        await self._ensure_table(inst.schema_name)
        qschema = qident(inst.schema_name)
        await self._session.execute(
            text(
                f"""
                INSERT INTO {qschema}.meta_workspace_docs (slug, body, updated_at)
                VALUES (:slug, :body, now())
                ON CONFLICT (slug) DO UPDATE
                SET body = EXCLUDED.body, updated_at = now()
                """
            ),
            {"slug": slug, "body": body},
        )
        await self._session.commit()
        return await self.get_doc(
            cabinet_id=cabinet_id, slug=slug, principal=principal, employee=employee
        )

    async def load_agents_md(self, *, schema_name: str) -> str | None:
        """Internal materialize path — no ACL (caller already authorized)."""
        if not schema_name:
            return None
        await self._ensure_table(schema_name)
        qschema = qident(schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT body FROM {qschema}.meta_workspace_docs WHERE slug = 'agents'
                """
            )
        )
        body = q.scalar_one_or_none()
        if body is None:
            return None
        text_body = str(body).strip()
        return text_body or None
