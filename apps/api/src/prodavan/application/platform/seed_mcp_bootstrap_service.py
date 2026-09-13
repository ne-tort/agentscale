"""Upload seed MCP zips to object store and attach to module rows when file_ref empty."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.mcp.seed_mcp_builder import (
    SEED_PACKAGES,
    SeedPackageSpec,
    build_seed_mcp_zip,
    storage_key_for,
)
from prodavan.domain.ownership import OwnerScope
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.content import (
    ContentAssetRow,
    ContentBlobVersionRow,
)
from prodavan.infrastructure.persistence.models.modules import (
    ModuleInstanceDataRow,
    ModuleInstanceRow,
)

logger = logging.getLogger(__name__)

EQUIPMENT_MODULE_ID = "mod_equipment"
EQUIPMENT_MCP_TABLE = "equipment_mcp"
EQUIPMENT_MCP_ROW_ID = "equipment_mcp_default"


class SeedMcpBootstrapService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_seed_mcp_packages(self) -> dict[str, Any]:
        """Idempotent: upload zips + attach file_ref only when empty."""
        results: list[dict[str, Any]] = []
        for spec in SEED_PACKAGES:
            results.append(await self._ensure_one(spec))
        await self._session.commit()
        return {"packages": results}

    async def _ensure_one(self, spec: SeedPackageSpec) -> dict[str, Any]:
        zip_bytes, manifest = build_seed_mcp_zip(spec)
        version = str(manifest.get("version") or "1.0.0")
        storage_key = storage_key_for(spec, version=version)
        sha = hashlib.sha256(zip_bytes).hexdigest()
        filename = f"{spec.name}-{version}.zip"

        asset_id, version_id = await self._ensure_asset(
            package_name=spec.name,
            version=version,
            storage_key=storage_key,
            zip_bytes=zip_bytes,
            sha256=sha,
            filename=filename,
        )
        file_ref = {
            "asset_id": asset_id,
            "version_id": version_id,
            "blob_version_id": version_id,
            "storage_key": storage_key,
            "filename": filename,
            "size": len(zip_bytes),
            "sha256": sha,
        }
        attached = 0
        skipped = 0
        if spec.name == "prodavan-equipment":
            attached, skipped = await self._attach_equipment_mcp(file_ref, manifest)
        return {
            "name": spec.name,
            "version": version,
            "storage_key": storage_key,
            "asset_id": asset_id,
            "attached_rows": attached,
            "skipped_rows": skipped,
        }

    async def _ensure_asset(
        self,
        *,
        package_name: str,
        version: str,
        storage_key: str,
        zip_bytes: bytes,
        sha256: str,
        filename: str,
    ) -> tuple[str, str]:
        """Create or refresh platform ContentAsset + blob for the seed zip."""
        store = ensure_file_store()
        # Reuse existing blob version with same storage_key + sha
        q = await self._session.execute(
            select(ContentBlobVersionRow).where(ContentBlobVersionRow.storage_key == storage_key)
        )
        existing = q.scalars().first()
        if existing is not None and existing.sha256 == sha256:
            try:
                await store.get_bytes(storage_key)
                return existing.asset_id, existing.id
            except FileNotFoundError:
                pass

        await store.put_bytes(storage_key, zip_bytes, content_type="application/zip")

        if existing is not None:
            existing.size = len(zip_bytes)
            existing.sha256 = sha256
            asset = await self._session.get(ContentAssetRow, existing.asset_id)
            if asset is not None:
                asset.title = filename
                asset.mime = "application/zip"
                asset.tags = ["seed-mcp", package_name, f"v{version}"]
            await self._session.flush()
            return existing.asset_id, existing.id

        asset = ContentAssetRow(
            owner_scope=OwnerScope.PLATFORM,
            owner_company_id=None,
            visibility="company",
            mime="application/zip",
            title=filename,
            tags=["seed-mcp", package_name, f"v{version}"],
            created_by=None,
        )
        self._session.add(asset)
        await self._session.flush()
        ver = ContentBlobVersionRow(
            asset_id=asset.id,
            version=1,
            storage_key=storage_key,
            size=len(zip_bytes),
            sha256=sha256,
            object_metadata={"seed_mcp": package_name, "version": version},
        )
        self._session.add(ver)
        await self._session.flush()
        return asset.id, ver.id

    async def _attach_equipment_mcp(
        self,
        file_ref: dict[str, Any],
        manifest: dict[str, Any],
    ) -> tuple[int, int]:
        """Attach seed file_ref to equipment_mcp_default when missing."""
        q = await self._session.execute(
            select(ModuleInstanceRow).where(ModuleInstanceRow.module_id == EQUIPMENT_MODULE_ID)
        )
        instances = list(q.scalars().all())
        attached = 0
        skipped = 0
        version = str(manifest.get("version") or "1.0.0")
        for inst in instances:
            row = await self._get_or_create_mcp_row(inst.id)
            body = dict(row.body or {})
            existing_ref = body.get("file_ref")
            if isinstance(existing_ref, dict) and (
                existing_ref.get("storage_key") or existing_ref.get("asset_id")
            ):
                existing_key = str(existing_ref.get("storage_key") or "")
                # Never clobber a user-uploaded / non-seed package.
                if not existing_key.startswith("platform/seed-mcp/"):
                    skipped += 1
                    continue
                same_key = existing_key == file_ref.get("storage_key")
                same_sha = existing_ref.get("sha256") == file_ref.get("sha256")
                if same_key and same_sha:
                    skipped += 1
                    continue
            body["name"] = "prodavan-equipment"
            body["version"] = version
            body["enabled"] = True if body.get("enabled") is None else body.get("enabled")
            body["file_ref"] = file_ref
            row.body = body
            attached += 1
        await self._session.flush()
        return attached, skipped

    async def _get_or_create_mcp_row(self, instance_id: str) -> ModuleInstanceDataRow:
        q = await self._session.execute(
            select(ModuleInstanceDataRow).where(
                ModuleInstanceDataRow.instance_id == instance_id,
                ModuleInstanceDataRow.table_slug == EQUIPMENT_MCP_TABLE,
                ModuleInstanceDataRow.row_id == EQUIPMENT_MCP_ROW_ID,
            )
        )
        row = q.scalar_one_or_none()
        if row is not None:
            return row
        row = ModuleInstanceDataRow(
            instance_id=instance_id,
            table_slug=EQUIPMENT_MCP_TABLE,
            row_id=EQUIPMENT_MCP_ROW_ID,
            body={
                "name": "prodavan-equipment",
                "version": "1.0.0",
                "enabled": True,
            },
            created_by="platform:seed-mcp",
        )
        self._session.add(row)
        await self._session.flush()
        return row
