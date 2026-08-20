"""Cabinet catalog use cases (M04)."""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.services.cabinet_service import CabinetError, get_cabinet
from prodavan.cabinets.electronics_procurement.catalogs.indexer import CatalogIndexError, index_csv_bytes
from prodavan.cabinets.electronics_procurement.integrations.s4b_runtime import get_s4b_gateway
from prodavan.cabinets.spi import CabinetDomainError
from prodavan.infrastructure.auth.s4b_vault import delete_vault, public_status, save_vault
from prodavan.infrastructure.storage.catalog_storage import (
    catalog_dir,
    now_iso,
    read_json,
    user_catalogs_root,
    write_json,
)
from prodavan.infrastructure.storage.run_storage import sanitize_filename, RunStorageError


class CatalogError(CabinetDomainError):
    """Catalog / S4B vault domain error."""


async def _cabinet(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    active_cabinet_id: uuid.UUID,
):
    if cabinet_id != active_cabinet_id:
        raise CatalogError("CABINET_MISMATCH", "Cabinet id mismatch", 403)
    try:
        return await get_cabinet(
            session, tenant_id=tenant_id, user_id=user_id, cabinet_id=cabinet_id
        )
    except CabinetError as exc:
        raise CatalogError(exc.code, exc.message, exc.status) from exc


def _s4b_enabled(cabinet) -> bool:
    caps = cabinet.capabilities or {}
    return bool(caps.get("integrations", {}).get("s4b", {}).get("enabled"))


async def upload_catalog(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    active_cabinet_id: uuid.UUID,
    filename: str,
    data: bytes,
    slug: str,
    display_name: str,
    trusted_seller: bool,
) -> dict:
    await _cabinet(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        cabinet_id=cabinet_id,
        active_cabinet_id=active_cabinet_id,
    )
    try:
        safe_name = sanitize_filename(filename)
    except RunStorageError as exc:
        raise CatalogError(exc.code, exc.message, 400) from exc
    if not safe_name.lower().endswith(".csv"):
        raise CatalogError("UNSUPPORTED_FORMAT", "I5 indexes CSV only", 422)

    catalog_id = f"cat_{secrets.token_hex(4)}"
    root = catalog_dir(tenant_id, cabinet_id, slug)
    root.mkdir(parents=True, exist_ok=True)
    (root / "source.csv").write_bytes(data)
    try:
        rows = index_csv_bytes(root / "catalog.sqlite", data)
    except CatalogIndexError as exc:
        write_json(
            root / "manifest.json",
            {"catalog_id": catalog_id, "slug": slug, "status": "failed", "error": str(exc)},
        )
        raise CatalogError("INDEX_FAILED", str(exc), 422) from exc

    manifest = {
        "catalog_id": catalog_id,
        "slug": slug,
        "display_name": display_name,
        "format": "csv",
        "status": "ready",
        "trusted_seller": trusted_seller,
        "stats": {"rows": rows, "last_indexed_at": now_iso()},
        "schema": {
            "table": "products",
            "columns": {"pn": "part_number", "price": "price", "title": "title", "stock": "stock"},
        },
        "indexed_at": now_iso(),
        "row_count": rows,
        "archived": False,
    }
    write_json(root / "manifest.json", manifest)
    (root / ".index-complete").write_text("ok", encoding="utf-8")
    return {
        "catalog_id": catalog_id,
        "status": "ready",
        "job_id": f"job_index_{catalog_id}",
        "stats": manifest["stats"],
    }


async def list_catalogs(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    active_cabinet_id: uuid.UUID,
) -> dict:
    await _cabinet(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        cabinet_id=cabinet_id,
        active_cabinet_id=active_cabinet_id,
    )
    root = user_catalogs_root(tenant_id, cabinet_id)
    items = []
    if root.exists():
        for path in sorted(root.iterdir()):
            manifest = read_json(path / "manifest.json")
            if not manifest or manifest.get("archived"):
                continue
            items.append(
                {
                    "id": manifest.get("catalog_id"),
                    "slug": manifest.get("slug"),
                    "display_name": manifest.get("display_name"),
                    "format": manifest.get("format"),
                    "status": manifest.get("status"),
                    "trusted_seller": manifest.get("trusted_seller"),
                    "stats": manifest.get("stats") or {"rows": manifest.get("row_count", 0)},
                }
            )
    return {"items": items}


async def archive_catalog(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    active_cabinet_id: uuid.UUID,
    catalog_id: str,
) -> None:
    await _cabinet(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        cabinet_id=cabinet_id,
        active_cabinet_id=active_cabinet_id,
    )
    root = user_catalogs_root(tenant_id, cabinet_id)
    if not root.exists():
        raise CatalogError("CATALOG_NOT_FOUND", "Catalog not found", 404)
    for path in root.iterdir():
        manifest = read_json(path / "manifest.json")
        if manifest and manifest.get("catalog_id") == catalog_id:
            if manifest.get("system"):
                raise CatalogError("SYSTEM_DATABASE_NON_DELETABLE", "System DB cannot be deleted", 403)
            manifest["archived"] = True
            manifest["status"] = "archived"
            write_json(path / "manifest.json", manifest)
            return
    raise CatalogError("CATALOG_NOT_FOUND", "Catalog not found", 404)


async def list_system_databases(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    active_cabinet_id: uuid.UUID,
) -> dict:
    cabinet = await _cabinet(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        cabinet_id=cabinet_id,
        active_cabinet_id=active_cabinet_id,
    )
    if cabinet.profile_id != "electronics-procurement" or not _s4b_enabled(cabinet):
        return {"items": []}
    return {
        "items": [
            {
                "id": "s4b-cache",
                "display_name": "S4B API Cache",
                "type": "s4b_api_cache",
                "deletable": False,
                "virtual": True,
                "requires_profile": "electronics-procurement",
                "stats": {"cached_part_numbers": 0, "last_refresh_at": None},
            }
        ]
    }


def refuse_system_delete(system_id: str) -> None:
    raise CatalogError(
        "SYSTEM_DATABASE_NON_DELETABLE",
        f"{system_id} cannot be deleted",
        403,
    )


def s4b_status(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> dict:
    return public_status(tenant_id, cabinet_id)


def put_s4b_credentials(
    tenant_id: uuid.UUID, cabinet_id: uuid.UUID, username: str, password: str
) -> dict:
    ping = get_s4b_gateway().ping(username, password)
    if ping.get("ok") or ping.get("error_code") == "rate_limited":
        # Too frequently still means the account was accepted.
        save_vault(
            tenant_id, cabinet_id, username, password, state="credentials_valid", last_error=None
        )
    else:
        save_vault(
            tenant_id,
            cabinet_id,
            username,
            password,
            state="credentials_invalid",
            last_error=str(ping.get("error") or ping.get("error_code") or "s4b_ping_failed"),
        )
    status = public_status(tenant_id, cabinet_id)
    return {
        "state": status["state"],
        "validated_at": status.get("last_validated_at"),
        "last_error": status.get("last_error"),
    }


def delete_s4b_credentials(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> dict:
    delete_vault(tenant_id, cabinet_id)
    return public_status(tenant_id, cabinet_id)
