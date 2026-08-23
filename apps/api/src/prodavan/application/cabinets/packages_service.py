"""MCP package registry — deploy / list / disable / export (L06)."""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.quota_service import CompanyQuotaService
from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.cabinets.package_codec import validate_package_zip
from prodavan.infrastructure.cabinets.sql import qident
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


class CabinetPackagesService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)

    async def list_packages(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        await self._ensure_registry(inst.schema_name)
        qschema = qident(inst.schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT id, name, version, runtime, status, content_hash, artifact_ref, created_at
                FROM {qschema}.meta_mcp_packages
                ORDER BY name, version
                """
            )
        )
        return [
            {
                "id": r.id,
                "name": r.name,
                "version": r.version,
                "runtime": r.runtime,
                "status": r.status,
                "content_hash": r.content_hash,
                "artifact_ref": r.artifact_ref,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in q.fetchall()
        ]

    async def deploy(
        self,
        *,
        cabinet_id: str,
        zip_bytes: bytes,
        principal: Principal,
        employee: EmployeeRow | None,
        replace_if_name: bool = False,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        await self._ensure_registry(inst.schema_name)
        validated = validate_package_zip(zip_bytes)
        qschema = qident(inst.schema_name)

        count_q = await self._session.execute(
            text(f"SELECT COUNT(*) FROM {qschema}.meta_mcp_packages WHERE status = 'active'")
        )
        active_count = int(count_q.scalar_one() or 0)

        existing = await self._session.execute(
            text(
                f"""
                SELECT id, name, version, status FROM {qschema}.meta_mcp_packages
                WHERE name = :name
                """
            ),
            {"name": validated["name"]},
        )
        rows = list(existing.fetchall())
        if rows and not replace_if_name:
            raise AppError(
                code="PACKAGE_EXISTS",
                title="Package exists",
                status=409,
                detail=f"package {validated['name']} already deployed; pass replace_if_name",
            )

        if not rows:
            await CompanyQuotaService(self._session).assert_can_add_package(
                inst.company_id,
                active_count=active_count,
            )

        artifact_ref = self._store_artifact(cabinet_id, validated["name"], validated["version"], zip_bytes)
        pkg_id = f"pkg_{uuid.uuid4().hex[:12]}"
        manifest_json = json.dumps(validated["manifest"], ensure_ascii=False, default=str)

        if rows and replace_if_name:
            for old in rows:
                await self._session.execute(
                    text(f"DELETE FROM {qschema}.meta_mcp_packages WHERE id = :id"),
                    {"id": old.id},
                )

        await self._session.execute(
            text(
                f"""
                INSERT INTO {qschema}.meta_mcp_packages
                (id, name, version, runtime, status, artifact_ref, manifest, content_hash)
                VALUES (:id, :name, :ver, :rt, 'active', :aref, CAST(:man AS jsonb), :ch)
                """
            ),
            {
                "id": pkg_id,
                "name": validated["name"],
                "ver": validated["version"],
                "rt": validated["runtime"],
                "aref": artifact_ref,
                "man": manifest_json,
                "ch": validated["content_hash"],
            },
        )
        await self._session.commit()
        return {
            "id": pkg_id,
            "name": validated["name"],
            "version": validated["version"],
            "runtime": validated["runtime"],
            "status": "active",
            "content_hash": validated["content_hash"],
            "artifact_ref": artifact_ref,
            "tools": validated["tool_names"],
        }

    async def deploy_base64(
        self,
        *,
        cabinet_id: str,
        zip_base64: str,
        principal: Principal,
        employee: EmployeeRow | None,
        replace_if_name: bool = False,
    ) -> dict:
        try:
            raw = base64.b64decode(zip_base64, validate=True)
        except Exception as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="invalid zip_base64",
            ) from exc
        return await self.deploy(
            cabinet_id=cabinet_id,
            zip_bytes=raw,
            principal=principal,
            employee=employee,
            replace_if_name=replace_if_name,
        )

    async def disable(
        self,
        *,
        cabinet_id: str,
        name: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        await self._ensure_registry(inst.schema_name)
        qschema = qident(inst.schema_name)
        res = await self._session.execute(
            text(
                f"""
                UPDATE {qschema}.meta_mcp_packages
                SET status = 'disabled'
                WHERE name = :name
                RETURNING id, name, version, status
                """
            ),
            {"name": name},
        )
        row = res.fetchone()
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="package not found")
        await self._session.commit()
        return {"id": row.id, "name": row.name, "version": row.version, "status": row.status}

    async def export_base64(
        self,
        *,
        cabinet_id: str,
        name: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        await self._ensure_registry(inst.schema_name)
        qschema = qident(inst.schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT artifact_ref, version, content_hash
                FROM {qschema}.meta_mcp_packages
                WHERE name = :name
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"name": name},
        )
        row = q.fetchone()
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="package not found")
        path = self._path_from_ref(row.artifact_ref)
        if not path.is_file():
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="artifact missing on disk")
        raw = path.read_bytes()
        return {
            "name": name,
            "version": row.version,
            "content_hash": row.content_hash,
            "zip_base64": base64.b64encode(raw).decode("ascii"),
            "size_bytes": len(raw),
        }

    async def load_enabled_artifacts(self, *, schema_name: str) -> list[tuple[str, bytes]]:
        """For bundle export — (filename, bytes)."""
        await self._ensure_registry(schema_name)
        qschema = qident(schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT name, version, artifact_ref
                FROM {qschema}.meta_mcp_packages
                WHERE status = 'active'
                """
            )
        )
        out: list[tuple[str, bytes]] = []
        for r in q.fetchall():
            path = self._path_from_ref(r.artifact_ref)
            if path.is_file():
                safe = f"{r.name}-{r.version}.zip"
                out.append((safe, path.read_bytes()))
        return out

    def _store_artifact(self, cabinet_id: str, name: str, version: str, raw: bytes) -> str:
        root = Path(settings.storage_root) / "cabinet_packages" / cabinet_id
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{name}-{version}.zip"
        path.write_bytes(raw)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return f"file://cabinet_packages/{cabinet_id}/{name}-{version}.zip"

    @staticmethod
    def _path_from_ref(artifact_ref: str) -> Path:
        prefix = "file://cabinet_packages/"
        if not artifact_ref.startswith(prefix):
            raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=500, detail="bad artifact_ref")
        rel = artifact_ref.removeprefix(prefix)
        return Path(settings.storage_root) / "cabinet_packages" / rel

    async def _ensure_registry(self, schema_name: str) -> None:
        qschema = qident(schema_name)
        await self._session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {qschema}.meta_mcp_packages (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    runtime TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    artifact_ref TEXT NOT NULL,
                    manifest JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    content_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (name, version)
                )
                """
            )
        )
        await self._session.commit()
