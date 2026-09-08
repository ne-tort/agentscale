"""Module instance cascade — fork meta+data on bind (Admin→Company→Cabinet→Project)."""

from __future__ import annotations

import copy
import json
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.errors import AppError
from prodavan.infrastructure.cabinets.sql import qident
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.modules import (
    ModuleCabinetBindingRow,
    ModuleInstanceDataRow,
    ModuleInstanceMetaDocumentRow,
    ModuleInstanceRow,
    ModuleMetaDocumentRow,
    ModuleRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow

OWNER_PLATFORM = "platform"
OWNER_COMPANY = "company"
OWNER_CABINET = "cabinet"
OWNER_PROJECT = "project"
PLATFORM_OWNER_ID = "platform"


def _new_instance_id() -> str:
    return f"minst_{uuid.uuid4().hex[:16]}"


def _new_meta_id() -> str:
    return f"mimd_{uuid.uuid4().hex[:16]}"


def _new_data_id() -> str:
    return f"midr_{uuid.uuid4().hex[:16]}"


def row_applies_to_project(body: dict[str, Any], project_id: str) -> bool:
    """Legacy project_ids filter used only during cabinet→project backfill/fork."""
    pids = body.get("project_ids")
    if pids is None or not isinstance(pids, list) or len(pids) == 0:
        return True
    return project_id in [str(p) for p in pids]


class ModuleInstanceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_instance(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        module_id: str,
    ) -> ModuleInstanceRow | None:
        q = await self._session.execute(
            select(ModuleInstanceRow).where(
                ModuleInstanceRow.owner_kind == owner_kind,
                ModuleInstanceRow.owner_id == owner_id,
                ModuleInstanceRow.module_id == module_id,
            )
        )
        return q.scalar_one_or_none()

    async def require_instance(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        module_id: str,
    ) -> ModuleInstanceRow:
        inst = await self.get_instance(
            owner_kind=owner_kind, owner_id=owner_id, module_id=module_id
        )
        if inst is None:
            raise AppError(
                code="NOT_FOUND",
                title="Not Found",
                status=404,
                detail=f"module instance not found ({owner_kind}/{owner_id}/{module_id})",
            )
        return inst

    async def ensure_platform_instance(self, *, module_id: str) -> ModuleInstanceRow:
        existing = await self.get_instance(
            owner_kind=OWNER_PLATFORM, owner_id=PLATFORM_OWNER_ID, module_id=module_id
        )
        if existing is not None:
            return existing
        mod = await self._session.get(ModuleRow, module_id)
        if mod is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="module not found")
        inst = ModuleInstanceRow(
            id=_new_instance_id(),
            module_id=module_id,
            owner_kind=OWNER_PLATFORM,
            owner_id=PLATFORM_OWNER_ID,
            parent_instance_id=None,
        )
        self._session.add(inst)
        await self._session.flush()
        await self._copy_template_meta(module_id=module_id, instance_id=inst.id)
        await self._seed_data_from_template(module_id=module_id, instance_id=inst.id)
        return inst

    async def fork_instance(
        self,
        *,
        parent: ModuleInstanceRow,
        owner_kind: str,
        owner_id: str,
        project_id_filter: str | None = None,
    ) -> ModuleInstanceRow:
        """Deep-copy parent meta+data into a new child instance (idempotent by owner key)."""
        existing = await self.get_instance(
            owner_kind=owner_kind, owner_id=owner_id, module_id=parent.module_id
        )
        if existing is not None:
            return existing
        child = ModuleInstanceRow(
            id=_new_instance_id(),
            module_id=parent.module_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
            parent_instance_id=parent.id,
        )
        self._session.add(child)
        await self._session.flush()
        await self._copy_instance_meta(src_id=parent.id, dst_id=child.id)
        await self._copy_instance_data(
            src_id=parent.id,
            dst_id=child.id,
            project_id_filter=project_id_filter,
        )
        return child

    async def ensure_company_instance(self, *, company_id: str, module_id: str) -> ModuleInstanceRow:
        existing = await self.get_instance(
            owner_kind=OWNER_COMPANY, owner_id=company_id, module_id=module_id
        )
        if existing is not None:
            return existing
        parent = await self.ensure_platform_instance(module_id=module_id)
        return await self.fork_instance(
            parent=parent, owner_kind=OWNER_COMPANY, owner_id=company_id
        )

    async def ensure_cabinet_instance(self, *, cabinet_id: str, module_id: str) -> ModuleInstanceRow:
        existing = await self.get_instance(
            owner_kind=OWNER_CABINET, owner_id=cabinet_id, module_id=module_id
        )
        if existing is not None:
            return existing
        cab = await self._session.get(CabinetInstanceRow, cabinet_id)
        company_id = None
        if cab is not None:
            company_id = cab.owner_company_id or cab.company_id
        if company_id:
            parent = await self.ensure_company_instance(company_id=company_id, module_id=module_id)
        else:
            parent = await self.ensure_platform_instance(module_id=module_id)
        return await self.fork_instance(
            parent=parent, owner_kind=OWNER_CABINET, owner_id=cabinet_id
        )

    async def ensure_project_instance(self, *, project_id: str, module_id: str) -> ModuleInstanceRow:
        existing = await self.get_instance(
            owner_kind=OWNER_PROJECT, owner_id=project_id, module_id=module_id
        )
        if existing is not None:
            return existing
        project = await self._session.get(ProjectRow, project_id)
        if project is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="project not found")
        parent = await self.ensure_cabinet_instance(
            cabinet_id=project.cabinet_id, module_id=module_id
        )
        return await self.fork_instance(
            parent=parent,
            owner_kind=OWNER_PROJECT,
            owner_id=project_id,
            project_id_filter=project_id,
        )

    async def ensure_project_instances_for_cabinet_modules(
        self, *, project_id: str
    ) -> list[ModuleInstanceRow]:
        project = await self._session.get(ProjectRow, project_id)
        if project is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="project not found")
        q = await self._session.execute(
            select(ModuleCabinetBindingRow.module_id).where(
                ModuleCabinetBindingRow.cabinet_id == project.cabinet_id
            )
        )
        out: list[ModuleInstanceRow] = []
        for module_id in q.scalars().all():
            out.append(await self.ensure_project_instance(project_id=project_id, module_id=module_id))
        return out

    async def delete_instance(self, *, instance_id: str) -> bool:
        row = await self._session.get(ModuleInstanceRow, instance_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def delete_cabinet_module_instances(self, *, cabinet_id: str, module_id: str) -> int:
        """Delete cabinet instance and project leaf instances for projects in that cabinet."""
        deleted = 0
        cab = await self.get_instance(
            owner_kind=OWNER_CABINET, owner_id=cabinet_id, module_id=module_id
        )
        projects = await self._session.execute(
            select(ProjectRow.id).where(ProjectRow.cabinet_id == cabinet_id)
        )
        for (project_id,) in projects.all():
            pr = await self.get_instance(
                owner_kind=OWNER_PROJECT, owner_id=project_id, module_id=module_id
            )
            if pr is not None:
                await self._session.delete(pr)
                deleted += 1
        if cab is not None:
            await self._session.delete(cab)
            deleted += 1
        await self._session.flush()
        return deleted

    async def refresh_meta_from_template(self, *, instance_id: str, module_id: str) -> None:
        """Replace instance meta docs from template catalog (data rows untouched)."""
        existing = await self._session.execute(
            select(ModuleInstanceMetaDocumentRow).where(
                ModuleInstanceMetaDocumentRow.instance_id == instance_id
            )
        )
        for row in existing.scalars().all():
            await self._session.delete(row)
        await self._session.flush()
        await self._copy_template_meta(module_id=module_id, instance_id=instance_id)

    async def refresh_platform_meta_from_template(self, *, module_id: str) -> None:
        inst = await self.get_instance(
            owner_kind=OWNER_PLATFORM, owner_id=PLATFORM_OWNER_ID, module_id=module_id
        )
        if inst is None:
            await self.ensure_platform_instance(module_id=module_id)
            return
        await self.refresh_meta_from_template(instance_id=inst.id, module_id=module_id)

    async def resolve_columns_body(self, *, instance_id: str, module_id: str) -> Any:
        """Instance columns meta with template fallback."""
        try:
            doc = await self.get_meta_document(instance_id=instance_id, slug="columns")
            return doc.get("body")
        except AppError:
            q = await self._session.execute(
                select(ModuleMetaDocumentRow.body).where(
                    ModuleMetaDocumentRow.module_id == module_id,
                    ModuleMetaDocumentRow.slug == "columns",
                )
            )
            return q.scalar_one_or_none()

    async def get_meta_document(self, *, instance_id: str, slug: str) -> dict[str, Any]:
        q = await self._session.execute(
            select(ModuleInstanceMetaDocumentRow).where(
                ModuleInstanceMetaDocumentRow.instance_id == instance_id,
                ModuleInstanceMetaDocumentRow.slug == slug,
            )
        )
        row = q.scalar_one_or_none()
        if row is None:
            raise AppError(
                code="NOT_FOUND", title="Not Found", status=404, detail="meta document not found"
            )
        return {"slug": row.slug, "body": row.body}

    async def list_meta_documents(self, *, instance_id: str) -> list[dict[str, Any]]:
        q = await self._session.execute(
            select(ModuleInstanceMetaDocumentRow)
            .where(ModuleInstanceMetaDocumentRow.instance_id == instance_id)
            .order_by(ModuleInstanceMetaDocumentRow.slug)
        )
        return [{"slug": r.slug, "body": r.body} for r in q.scalars().all()]

    async def put_meta_document(self, *, instance_id: str, slug: str, body: Any) -> dict[str, Any]:
        q = await self._session.execute(
            select(ModuleInstanceMetaDocumentRow).where(
                ModuleInstanceMetaDocumentRow.instance_id == instance_id,
                ModuleInstanceMetaDocumentRow.slug == slug,
            )
        )
        row = q.scalar_one_or_none()
        payload: Any = body if isinstance(body, (dict, list)) else {}
        if row is None:
            row = ModuleInstanceMetaDocumentRow(
                id=_new_meta_id(),
                instance_id=instance_id,
                slug=slug,
                body=payload,
            )
            self._session.add(row)
        else:
            row.body = payload
        await self._session.flush()
        return {"slug": row.slug, "body": row.body}

    async def list_data_rows(self, *, instance_id: str, table_slug: str) -> list[dict[str, Any]]:
        q = await self._session.execute(
            select(ModuleInstanceDataRow)
            .where(
                ModuleInstanceDataRow.instance_id == instance_id,
                ModuleInstanceDataRow.table_slug == table_slug,
            )
            .order_by(ModuleInstanceDataRow.updated_at.desc())
        )
        return [
            {
                "row_id": r.row_id,
                "table_slug": r.table_slug,
                "body": r.body,
                "created_by": r.created_by,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in q.scalars().all()
        ]

    async def get_data_row(
        self, *, instance_id: str, table_slug: str, row_id: str
    ) -> dict[str, Any] | None:
        q = await self._session.execute(
            select(ModuleInstanceDataRow).where(
                ModuleInstanceDataRow.instance_id == instance_id,
                ModuleInstanceDataRow.table_slug == table_slug,
                ModuleInstanceDataRow.row_id == row_id,
            )
        )
        r = q.scalar_one_or_none()
        if r is None:
            return None
        return {
            "row_id": r.row_id,
            "table_slug": r.table_slug,
            "body": r.body,
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }

    async def upsert_data_row(
        self,
        *,
        instance_id: str,
        table_slug: str,
        row_id: str,
        body: dict[str, Any],
        created_by: str | None = None,
    ) -> dict[str, Any]:
        q = await self._session.execute(
            select(ModuleInstanceDataRow).where(
                ModuleInstanceDataRow.instance_id == instance_id,
                ModuleInstanceDataRow.table_slug == table_slug,
                ModuleInstanceDataRow.row_id == row_id,
            )
        )
        row = q.scalar_one_or_none()
        if row is None:
            row = ModuleInstanceDataRow(
                id=_new_data_id(),
                instance_id=instance_id,
                table_slug=table_slug,
                row_id=row_id,
                body=body,
                created_by=created_by,
            )
            self._session.add(row)
        else:
            row.body = body
            # API / user edits clear seed provenance so product upsert never clobbers.
            if created_by is not None:
                row.created_by = created_by
            elif row.created_by in ("module_seed", None):
                row.created_by = "user"
        await self._session.flush()
        return {
            "row_id": row.row_id,
            "table_slug": row.table_slug,
            "body": row.body,
            "created_by": row.created_by,
        }

    async def create_data_row(
        self,
        *,
        instance_id: str,
        table_slug: str,
        body: dict[str, Any],
        created_by: str | None = None,
    ) -> dict[str, Any]:
        row_id = f"row_{uuid.uuid4().hex[:12]}"
        return await self.upsert_data_row(
            instance_id=instance_id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            created_by=created_by,
        )

    async def delete_data_row(self, *, instance_id: str, table_slug: str, row_id: str) -> bool:
        q = await self._session.execute(
            select(ModuleInstanceDataRow).where(
                ModuleInstanceDataRow.instance_id == instance_id,
                ModuleInstanceDataRow.table_slug == table_slug,
                ModuleInstanceDataRow.row_id == row_id,
            )
        )
        row = q.scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def list_all_data_rows(self, *, instance_id: str) -> list[ModuleInstanceDataRow]:
        q = await self._session.execute(
            select(ModuleInstanceDataRow).where(ModuleInstanceDataRow.instance_id == instance_id)
        )
        return list(q.scalars().all())

    async def import_cabinet_schema_rows(
        self,
        *,
        instance_id: str,
        schema_name: str,
        module_id: str,
        project_id_filter: str | None = None,
    ) -> int:
        """Copy legacy cab_inst_*.module_data_rows into instance (for backfill)."""
        qschema = qident(schema_name)
        try:
            q = await self._session.execute(
                text(
                    f"""
                    SELECT table_slug, row_id, body, created_by
                    FROM {qschema}.module_data_rows
                    WHERE module_id = :module_id
                    """
                ),
                {"module_id": module_id},
            )
        except Exception:
            return 0
        count = 0
        for r in q.fetchall():
            body: Any = r.body if isinstance(r.body, dict) else {}
            if isinstance(r.body, str):
                try:
                    body = json.loads(r.body)
                except json.JSONDecodeError:
                    body = {}
            if not isinstance(body, dict):
                body = {}
            if project_id_filter and not row_applies_to_project(body, project_id_filter):
                continue
            await self.upsert_data_row(
                instance_id=instance_id,
                table_slug=str(r.table_slug),
                row_id=str(r.row_id),
                body=body,
                created_by=r.created_by,
            )
            count += 1
        return count

    async def _copy_template_meta(self, *, module_id: str, instance_id: str) -> None:
        q = await self._session.execute(
            select(ModuleMetaDocumentRow).where(ModuleMetaDocumentRow.module_id == module_id)
        )
        for doc in q.scalars().all():
            self._session.add(
                ModuleInstanceMetaDocumentRow(
                    id=_new_meta_id(),
                    instance_id=instance_id,
                    slug=doc.slug,
                    body=copy.deepcopy(doc.body) if isinstance(doc.body, (dict, list)) else doc.body,
                )
            )
        await self._session.flush()

    async def _seed_data_from_template(self, *, module_id: str, instance_id: str) -> None:
        q = await self._session.execute(
            select(ModuleMetaDocumentRow).where(
                ModuleMetaDocumentRow.module_id == module_id,
                ModuleMetaDocumentRow.slug == "seed_rows",
            )
        )
        doc = q.scalar_one_or_none()
        if doc is None or not isinstance(doc.body, dict):
            return
        items = doc.body.get("items")
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            table_slug = item.get("table_slug")
            row_id = item.get("row_id")
            body = item.get("body", {})
            if not isinstance(table_slug, str) or not isinstance(row_id, str):
                continue
            if not isinstance(body, dict):
                body = {}
            await self.upsert_data_row(
                instance_id=instance_id,
                table_slug=table_slug,
                row_id=row_id,
                body=copy.deepcopy(body),
                created_by="module_seed",
            )

    async def _copy_instance_meta(self, *, src_id: str, dst_id: str) -> None:
        q = await self._session.execute(
            select(ModuleInstanceMetaDocumentRow).where(
                ModuleInstanceMetaDocumentRow.instance_id == src_id
            )
        )
        for doc in q.scalars().all():
            self._session.add(
                ModuleInstanceMetaDocumentRow(
                    id=_new_meta_id(),
                    instance_id=dst_id,
                    slug=doc.slug,
                    body=copy.deepcopy(doc.body) if isinstance(doc.body, (dict, list)) else doc.body,
                )
            )
        await self._session.flush()

    async def _copy_instance_data(
        self,
        *,
        src_id: str,
        dst_id: str,
        project_id_filter: str | None = None,
    ) -> None:
        for row in await self.list_all_data_rows(instance_id=src_id):
            body = copy.deepcopy(row.body) if isinstance(row.body, dict) else {}
            if project_id_filter and not row_applies_to_project(body, project_id_filter):
                continue
            if project_id_filter and isinstance(body, dict) and "project_ids" in body:
                body = {**body, "project_ids": []}
            await self.upsert_data_row(
                instance_id=dst_id,
                table_slug=row.table_slug,
                row_id=row.row_id,
                body=body,
                created_by=row.created_by,
            )
