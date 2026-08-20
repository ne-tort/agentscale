"""Cabinet endpoints (M00)."""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.deps import CurrentUser, get_authenticated_session, get_current_user
from prodavan.application.dto.cabinets import (
    CabinetListResponse,
    CabinetResponse,
    CreateCabinetRequest,
    ManifestResponse,
    PatchCabinetRequest,
    ProfileListResponse,
    ProfileResponse,
    SwitchCabinetResponse,
)
from prodavan.application.services.cabinet_service import (
    CabinetError,
    archive_cabinet,
    create_cabinet,
    get_cabinet,
    list_cabinets,
    restore_cabinet,
    switch_cabinet,
    _storage_uri,
)
from prodavan.config.settings import settings
from prodavan.domain.capabilities import capabilities_preview
from prodavan.infrastructure.auth.jwt import create_access_token
from prodavan.infrastructure.persistence.models.tenants import Cabinet, CabinetProfile

router = APIRouter(tags=["cabinets"])


def _cabinet_error(exc: CabinetError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


def _to_response(cabinet: Cabinet) -> CabinetResponse:
    return CabinetResponse(
        id=cabinet.id,
        slug=cabinet.slug,
        display_name=cabinet.display_name,
        profile_id=cabinet.profile_id,
        status=cabinet.status,
        capabilities=cabinet.capabilities or {},
        storage_uri=_storage_uri(cabinet.tenant_id, cabinet.id) if cabinet.profile_id else None,
        created_at=cabinet.created_at,
    )


@router.get("/cabinet-profiles", response_model=ProfileListResponse)
async def list_profiles(
    session: AsyncSession = Depends(get_authenticated_session),
) -> ProfileListResponse:
    result = await session.execute(
        select(CabinetProfile).where(CabinetProfile.deprecated.is_(False)).order_by(CabinetProfile.id)
    )
    profiles = result.scalars().all()
    return ProfileListResponse(
        items=[
            ProfileResponse(
                id=p.id,
                version=p.version,
                display_name=p.display_name,
                description=p.description,
                deprecated=p.deprecated,
                capabilities_preview=capabilities_preview(p.capabilities_schema),
            )
            for p in profiles
        ]
    )


@router.get("/cabinets", response_model=CabinetListResponse)
async def get_cabinets(
    status_filter: str = Query(default="active", alias="status", pattern="^(active|archived|all)$"),
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_authenticated_session),
) -> CabinetListResponse:
    cabinets = await list_cabinets(
        session,
        tenant_id=current.tenant_id,
        user_id=current.user_id,
        status=status_filter,
    )
    return CabinetListResponse(items=[_to_response(c) for c in cabinets])


@router.post("/cabinets", response_model=CabinetResponse, status_code=status.HTTP_201_CREATED)
async def post_cabinet(
    body: CreateCabinetRequest,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_authenticated_session),
) -> CabinetResponse:
    try:
        cabinet = await create_cabinet(
            session,
            tenant_id=current.tenant_id,
            user_id=current.user_id,
            slug=body.slug,
            display_name=body.display_name,
            profile_id=body.profile_id,
        )
    except CabinetError as exc:
        raise _cabinet_error(exc) from exc
    return _to_response(cabinet)


@router.get("/cabinets/{cabinet_id}", response_model=CabinetResponse)
async def get_cabinet_by_id(
    cabinet_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_authenticated_session),
) -> CabinetResponse:
    try:
        cabinet = await get_cabinet(
            session,
            tenant_id=current.tenant_id,
            user_id=current.user_id,
            cabinet_id=cabinet_id,
        )
    except CabinetError as exc:
        raise _cabinet_error(exc) from exc
    return _to_response(cabinet)


@router.patch("/cabinets/{cabinet_id}", response_model=CabinetResponse)
async def patch_cabinet(
    cabinet_id: uuid.UUID,
    body: PatchCabinetRequest,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_authenticated_session),
) -> CabinetResponse:
    try:
        cabinet = await get_cabinet(
            session,
            tenant_id=current.tenant_id,
            user_id=current.user_id,
            cabinet_id=cabinet_id,
        )
    except CabinetError as exc:
        raise _cabinet_error(exc) from exc
    if body.display_name is not None:
        cabinet.display_name = body.display_name
    await session.commit()
    await session.refresh(cabinet)
    return _to_response(cabinet)


@router.delete("/cabinets/{cabinet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cabinet(
    cabinet_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_authenticated_session),
) -> Response:
    try:
        await archive_cabinet(
            session,
            tenant_id=current.tenant_id,
            user_id=current.user_id,
            cabinet_id=cabinet_id,
        )
    except CabinetError as exc:
        raise _cabinet_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/cabinets/{cabinet_id}/restore", response_model=CabinetResponse)
async def post_restore_cabinet(
    cabinet_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_authenticated_session),
) -> CabinetResponse:
    try:
        cabinet = await restore_cabinet(
            session,
            tenant_id=current.tenant_id,
            user_id=current.user_id,
            cabinet_id=cabinet_id,
        )
    except CabinetError as exc:
        raise _cabinet_error(exc) from exc
    return _to_response(cabinet)


@router.post("/cabinets/{cabinet_id}/switch", response_model=SwitchCabinetResponse)
async def post_switch_cabinet(
    cabinet_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_authenticated_session),
) -> SwitchCabinetResponse:
    try:
        cabinet, cabinet_ids = await switch_cabinet(
            session,
            tenant_id=current.tenant_id,
            user_id=current.user_id,
            cabinet_id=cabinet_id,
        )
    except CabinetError as exc:
        raise _cabinet_error(exc) from exc

    access_token = create_access_token(
        user_id=current.user_id,
        tenant_id=current.tenant_id,
        cabinet_ids=cabinet_ids,
        active_cabinet_id=cabinet.id,
    )
    workspace_key = f"cab:{current.tenant_id}:{cabinet.id}"
    return SwitchCabinetResponse(
        cabinet_id=cabinet.id,
        tenant_id=current.tenant_id,
        workspace_key=workspace_key,
        capabilities=cabinet.capabilities or {},
        access_token=access_token,
    )


@router.get("/cabinets/{cabinet_id}/capabilities")
async def get_cabinet_capabilities(
    cabinet_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_authenticated_session),
) -> dict:
    try:
        cabinet = await get_cabinet(
            session,
            tenant_id=current.tenant_id,
            user_id=current.user_id,
            cabinet_id=cabinet_id,
        )
    except CabinetError as exc:
        raise _cabinet_error(exc) from exc

    caps = cabinet.capabilities or {}
    modules = caps.get("modules", {})
    integrations = caps.get("integrations", {})
    return {
        "cabinet_id": str(cabinet.id),
        "profile_id": cabinet.profile_id,
        "effective": {
            "s4b": integrations.get("s4b", {}).get("enabled", False),
            "specs_kp": modules.get("specs_kp", {}).get("enabled", False),
            "equipment_cards": modules.get("equipment_cards", {}).get("enabled", False),
            "prompts": modules.get("prompts", {}).get("enabled", True),
            "catalogs_user": modules.get("catalogs_user", {}).get("enabled", True),
        },
        "forbidden_always": ["s4b_override"],
    }


def _load_profile_ui(profile_id: str) -> dict:
    pack_dir = settings.packs_root / profile_id
    if profile_id == "generic-assistant":
        pack_dir = settings.packs_root / "_template"
    profile_path = pack_dir / "cabinet-profile.json"
    if not profile_path.exists():
        return {}
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    return data.get("ui", {})


@router.get("/cabinets/{cabinet_id}/manifest", response_model=ManifestResponse)
async def get_cabinet_manifest(
    cabinet_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_authenticated_session),
) -> ManifestResponse:
    try:
        cabinet = await get_cabinet(
            session,
            tenant_id=current.tenant_id,
            user_id=current.user_id,
            cabinet_id=cabinet_id,
        )
    except CabinetError as exc:
        raise _cabinet_error(exc) from exc

    ui = _load_profile_ui(cabinet.profile_id) if cabinet.profile_id else {}
    marker_path = (
        settings.storage_root
        / "cabinets"
        / str(cabinet.tenant_id)
        / str(cabinet.id)
        / ".cabinet.json"
    )
    if marker_path.exists() and not ui:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        ui = marker.get("ui", ui)

    return ManifestResponse(
        cabinet_id=cabinet.id,
        profile_id=cabinet.profile_id,
        ui=ui,
        capabilities=cabinet.capabilities or {},
    )
