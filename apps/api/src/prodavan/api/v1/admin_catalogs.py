"""Admin reference catalog HTTP API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.catalog.service import CatalogService

router = APIRouter(prefix="/admin/catalogs", tags=["admin-catalogs"])


class CreateEntryBody(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(min_length=1, max_length=200)
    id: str | None = Field(default=None, max_length=64)
    subtitle: str | None = Field(default=None, max_length=500)
    icon_name: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 100


class PatchEntryBody(BaseModel):
    model_config = {"extra": "forbid"}

    title: str | None = Field(default=None, min_length=1, max_length=200)
    subtitle: str | None = None
    icon_name: str | None = None
    payload: dict[str, Any] | None = None
    sort_order: int | None = None


@router.get("/{catalog_id}/entries")
async def list_entries(
    catalog_id: str,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    items = await CatalogService(session).list_entries(catalog_id)
    return {"items": items}


@router.post("/{catalog_id}/entries", status_code=201)
async def create_entry(
    catalog_id: str,
    body: CreateEntryBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await CatalogService(session).create_entry(
        catalog_id,
        title=body.title,
        id=body.id,
        subtitle=body.subtitle,
        icon_name=body.icon_name,
        payload=body.payload,
        sort_order=body.sort_order,
    )


@router.patch("/{catalog_id}/entries/{entry_id}")
async def patch_entry(
    catalog_id: str,
    entry_id: str,
    body: PatchEntryBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await CatalogService(session).patch_entry(
        catalog_id,
        entry_id,
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/{catalog_id}/entries/{entry_id}")
async def delete_entry(
    catalog_id: str,
    entry_id: str,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await CatalogService(session).delete_entry(catalog_id, entry_id)
