"""Electronics procurement cabinet module (SPI)."""

from __future__ import annotations

import json
from typing import Any

from prodavan.cabinets.db import apply_sql_migrations, ensure_cabinet_db
from prodavan.cabinets.electronics_procurement.services import (
    catalog_service,
    pipeline_service,
)
from prodavan.cabinets.spi import HealthResponse, PlatformEvent, SpiContext
from prodavan.config.settings import settings


class ElectronicsProcurementModule:
    """SPI adapter over cabinet-owned pipeline/catalog services."""

    pack_id = "electronics-procurement"
    pack_version = "1.0.0"

    COMMANDS = frozenset(
        {
            "upload_inbox",
            "create_run",
            "advance_run",
            "finalize_run",
            "export_kp",
            "upload_catalog",
            "archive_catalog",
            "put_s4b_credentials",
            "delete_s4b_credentials",
        }
    )
    QUERIES = frozenset(
        {
            "list_runs",
            "describe_run",
            "list_lineitems",
            "list_offers",
            "list_catalogs",
            "list_system_databases",
            "s4b_status",
            "resolve_export_file",
        }
    )

    def health(self) -> HealthResponse:
        return HealthResponse(
            status="ok", pack_id=self.pack_id, pack_version=self.pack_version
        )

    async def manifest(self, ctx: SpiContext) -> dict[str, Any]:
        pack = settings.packs_root / self.pack_id
        profile_path = pack / "cabinet-profile.json"
        ui: dict[str, Any] = {}
        caps: list[str] = []
        tools: list[dict[str, Any]] = []
        if profile_path.exists():
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            ui = profile.get("ui") or {}
            caps = list(profile.get("capabilities") or [])
            allow = (profile.get("mcp") or {}).get("tools_allowlist") or []
            tools = [{"name": n, "allow": True} for n in allow]
        db = str(ensure_cabinet_db(ctx.tenant_id, ctx.cabinet_id))
        return {
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "capabilities": {"raw": caps},
            "ui": ui,
            "tools": tools,
            "commands": sorted(self.COMMANDS),
            "queries": sorted(self.QUERIES),
            "db": db,
        }

    async def migrate(self, ctx: SpiContext) -> dict[str, Any]:
        db_path = ensure_cabinet_db(ctx.tenant_id, ctx.cabinet_id)
        migrations = settings.packs_root / self.pack_id / "migrations"
        applied = apply_sql_migrations(db_path, migrations)
        return {"applied": applied, "db": str(db_path)}

    async def on_platform_event(self, event: PlatformEvent) -> None:
        # Hook for pack-specific reactions (e.g. auto-index after file.uploaded).
        if event.type == "file.uploaded":
            return
        if event.type == "project.created":
            return
        return

    async def execute_command(
        self, ctx: SpiContext, name: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if name not in self.COMMANDS:
            raise KeyError(name)
        session = payload["session"]
        tenant_id = ctx.tenant_id
        cabinet_id = ctx.cabinet_id
        user_id = ctx.user_id
        project_id = payload.get("project_id") or ctx.project_id

        if name == "upload_inbox":
            return await pipeline_service.upload_inbox(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                cabinet_id=cabinet_id,
                project_id=project_id,
                filename=payload["filename"],
                data=payload["data"],
                auto_run=bool(payload.get("auto_run", False)),
            )
        if name == "create_run":
            return await pipeline_service.create_run(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                cabinet_id=cabinet_id,
                project_id=project_id,
                input_filename=payload["input_filename"],
            )
        if name == "advance_run":
            return await pipeline_service.advance_run(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                cabinet_id=cabinet_id,
                project_id=project_id,
                run_id=payload["run_id"],
                target_phase=payload["target_phase"],
            )
        if name == "finalize_run":
            return await pipeline_service.finalize_run(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                cabinet_id=cabinet_id,
                project_id=project_id,
                run_id=payload["run_id"],
                confirmed=payload.get("confirmed", True),
                operator_note=payload.get("operator_note"),
            )
        if name == "export_kp":
            return await pipeline_service.export_kp(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                cabinet_id=cabinet_id,
                project_id=project_id,
                run_id=payload["run_id"],
                include_alternatives=bool(payload.get("include_alternatives", True)),
            )
        if name == "upload_catalog":
            return await catalog_service.upload_catalog(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                cabinet_id=cabinet_id,
                active_cabinet_id=payload.get("active_cabinet_id", cabinet_id),
                filename=payload["filename"],
                data=payload["data"],
                slug=payload["slug"],
                display_name=payload["display_name"],
                trusted_seller=bool(payload.get("trusted_seller", True)),
            )
        if name == "archive_catalog":
            await catalog_service.archive_catalog(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                cabinet_id=cabinet_id,
                active_cabinet_id=payload.get("active_cabinet_id", cabinet_id),
                catalog_id=payload["catalog_id"],
            )
            return {}
        if name == "put_s4b_credentials":
            return catalog_service.put_s4b_credentials(
                tenant_id, cabinet_id, payload["username"], payload["password"]
            )
        if name == "delete_s4b_credentials":
            return catalog_service.delete_s4b_credentials(tenant_id, cabinet_id)
        raise KeyError(name)

    async def execute_query(
        self, ctx: SpiContext, name: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        if name not in self.QUERIES:
            raise KeyError(name)
        session = params.get("session")
        tenant_id = ctx.tenant_id
        cabinet_id = ctx.cabinet_id
        user_id = ctx.user_id
        project_id = params.get("project_id") or ctx.project_id

        if name == "list_runs":
            return pipeline_service.list_runs(tenant_id, cabinet_id, project_id)
        if name == "describe_run":
            return pipeline_service.describe_run(
                tenant_id, cabinet_id, project_id, params["run_id"]
            )
        if name == "list_lineitems":
            return pipeline_service.list_lineitems(
                tenant_id, cabinet_id, project_id, params["run_id"]
            )
        if name == "list_offers":
            return pipeline_service.list_offers(
                tenant_id, cabinet_id, project_id, params["run_id"]
            )
        if name == "list_catalogs":
            return await catalog_service.list_catalogs(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                cabinet_id=cabinet_id,
                active_cabinet_id=params.get("active_cabinet_id", cabinet_id),
            )
        if name == "list_system_databases":
            return await catalog_service.list_system_databases(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                cabinet_id=cabinet_id,
                active_cabinet_id=params.get("active_cabinet_id", cabinet_id),
            )
        if name == "s4b_status":
            return catalog_service.s4b_status(tenant_id, cabinet_id)
        if name == "resolve_export_file":
            path = pipeline_service.resolve_export_file(
                tenant_id, cabinet_id, project_id, params["filename"]
            )
            return {"path": str(path)}
        raise KeyError(name)
