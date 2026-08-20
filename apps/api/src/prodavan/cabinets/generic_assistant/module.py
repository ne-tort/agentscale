"""Generic assistant cabinet — platform chat/prompts only, no procurement domain."""

from __future__ import annotations

import json
from typing import Any

from prodavan.cabinets.db import apply_sql_migrations, ensure_cabinet_db
from prodavan.cabinets.spi import HealthResponse, PlatformEvent, SpiContext
from prodavan.config.settings import settings


class GenericAssistantModule:
    pack_id = "generic-assistant"
    pack_version = "1.0.0"

    def health(self) -> HealthResponse:
        return HealthResponse(
            status="ok", pack_id=self.pack_id, pack_version=self.pack_version
        )

    def _pack_dir(self):
        root = settings.packs_root
        if (root / "_template").exists():
            return root / "_template"
        return root / self.pack_id

    async def manifest(self, ctx: SpiContext) -> dict[str, Any]:
        profile_path = self._pack_dir() / "cabinet-profile.json"
        ui: dict[str, Any] = {"projectTabs": ["chat"], "navigation": [], "modules": {}}
        caps: list[str] = ["agent.session"]
        if profile_path.exists():
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            ui = profile.get("ui") or ui
            caps = list(profile.get("capabilities") or caps)
        db = str(ensure_cabinet_db(ctx.tenant_id, ctx.cabinet_id))
        return {
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "capabilities": {"raw": caps},
            "ui": ui,
            "tools": [],
            "commands": [],
            "queries": [],
            "db": db,
        }

    async def migrate(self, ctx: SpiContext) -> dict[str, Any]:
        db_path = ensure_cabinet_db(ctx.tenant_id, ctx.cabinet_id)
        migrations = self._pack_dir() / "migrations"
        applied = apply_sql_migrations(db_path, migrations)
        return {"applied": applied, "db": str(db_path)}

    async def on_platform_event(self, event: PlatformEvent) -> None:
        _ = event

    async def execute_command(
        self, ctx: SpiContext, name: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        raise KeyError(name)

    async def execute_query(
        self, ctx: SpiContext, name: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        raise KeyError(name)
