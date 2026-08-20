"""Cabinet pack seed pipeline."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.config.settings import settings
from prodavan.domain.capabilities import build_capabilities_snapshot
from prodavan.infrastructure.persistence.models.tenants import Cabinet, CabinetSeedRun
from prodavan.infrastructure.storage.local_storage import (
    copy_tree,
    ensure_cabinet_storage,
    remove_cabinet_storage,
    write_cabinet_marker,
    write_seed_complete,
)


class SeedError(Exception):
    def __init__(self, step: str, message: str) -> None:
        self.step = step
        self.message = message
        super().__init__(message)


def _load_profile_json(profile_id: str) -> dict:
    pack_dir = settings.packs_root / profile_id
    if not pack_dir.exists() and profile_id != "generic-assistant":
        raise SeedError("validate_profile", f"Pack directory missing: {profile_id}")
    if profile_id == "generic-assistant":
        pack_dir = settings.packs_root / "_template"
    profile_path = pack_dir / "cabinet-profile.json"
    if not profile_path.exists():
        raise SeedError("validate_profile", f"cabinet-profile.json missing for {profile_id}")
    return json.loads(profile_path.read_text(encoding="utf-8"))


async def _record_step(
    session: AsyncSession,
    *,
    cabinet_id: uuid.UUID,
    pack_version: str,
    step: str,
    status: str,
    error_detail: dict | None = None,
) -> None:
    run = CabinetSeedRun(
        cabinet_id=cabinet_id,
        pack_version=pack_version,
        step=step,
        status=status,
        error_detail=error_detail,
        finished_at=datetime.now(UTC) if status != "pending" else None,
    )
    session.add(run)
    await session.flush()


async def run_pack_seed(
    session: AsyncSession,
    *,
    cabinet: Cabinet,
    profile_id: str,
) -> dict:
    """Materialize storage from pack; returns capabilities snapshot."""
    profile = _load_profile_json(profile_id)
    if profile.get("id") and profile["id"] != profile_id:
        raise SeedError("validate_profile", "Profile id mismatch")

    pack_version = profile.get("version", "1.0.0")
    capabilities = build_capabilities_snapshot(profile)

    pack_dir = settings.packs_root / profile_id
    if profile_id == "generic-assistant":
        pack_dir = settings.packs_root / "_template"

    try:
        await _record_step(
            session,
            cabinet_id=cabinet.id,
            pack_version=pack_version,
            step="validate_profile",
            status="ok",
        )

        root = ensure_cabinet_storage(cabinet.tenant_id, cabinet.id)
        seeds = profile.get("seeds", {})

        prompts_src = pack_dir / seeds.get("prompts", "prompts")
        copy_tree(prompts_src, root / "prompts")

        shops_rel = seeds.get("shops")
        if shops_rel:
            shops_src = pack_dir / shops_rel
            shops_dst = root / "shops" / "allowlist.json"
            if shops_src.is_file():
                shops_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(shops_src, shops_dst)
            else:
                copy_tree(shops_src, root / "shops")

        theme_rel = seeds.get("theme")
        if theme_rel:
            theme_src = pack_dir / theme_rel
            theme_dst = root / "theme" / "tokens.json"
            if theme_src.is_file():
                theme_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(theme_src, theme_dst)
            else:
                copy_tree(theme_src, root / "theme")

        if capabilities["integrations"]["s4b"]["enabled"]:
            (root / "catalogs" / "system" / "s4b").mkdir(parents=True, exist_ok=True)
            (root / "catalogs" / "system" / "s4b" / ".reserved").write_text(
                "system DB s4b — cred-gated in M05",
                encoding="utf-8",
            )

        write_cabinet_marker(
            cabinet.tenant_id,
            cabinet.id,
            profile_id=profile_id,
            profile_version=pack_version,
            capabilities=capabilities,
        )
        write_seed_complete(cabinet.tenant_id, cabinet.id)

        await _record_step(
            session,
            cabinet_id=cabinet.id,
            pack_version=pack_version,
            step="materialize_storage",
            status="ok",
        )
        await _record_step(
            session,
            cabinet_id=cabinet.id,
            pack_version=pack_version,
            step="seed_prompts",
            status="ok",
        )
        return capabilities
    except SeedError:
        raise
    except Exception as exc:
        remove_cabinet_storage(cabinet.tenant_id, cabinet.id)
        await _record_step(
            session,
            cabinet_id=cabinet.id,
            pack_version=pack_version,
            step="materialize_storage",
            status="failed",
            error_detail={"message": str(exc)},
        )
        raise SeedError("materialize_storage", str(exc)) from exc
