"""Prompt endpoints (M03)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from prodavan.api.deps import CabinetSession, get_cabinet_session
from prodavan.application.dto.prompts import (
    CreatePromptVersionRequest,
    PromptFileResponse,
    PromptTreeResponse,
    PromptVersionListResponse,
    PromptVersionResponse,
    PutPromptFileRequest,
    RollbackPromptVersionRequest,
)
from prodavan.application.services.prompt_service import (
    PromptError,
    create_prompt_version,
    ensure_prompts_access,
    get_prompt_file,
    get_prompt_tree,
    get_prompt_version,
    list_prompt_versions,
    rollback_prompt_version,
    save_prompt_file,
)

router = APIRouter(tags=["prompts"])


def _prompt_error(exc: PromptError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


def _to_version_response(manifest: dict, *, include_files: bool = False) -> PromptVersionResponse:
    files = manifest.get("files", [])
    return PromptVersionResponse(
        version_id=manifest["version_id"],
        cabinet_id=manifest["cabinet_id"],
        label=manifest.get("label"),
        parent_version_id=manifest.get("parent_version_id"),
        created_at=datetime.fromisoformat(manifest["created_at"]),
        created_by=manifest["created_by"],
        files_count=len(files),
        files=files if include_files else None,
    )


@router.get("/cabinets/{cabinet_id}/prompts/tree", response_model=PromptTreeResponse)
async def get_prompts_tree(
    cabinet_id: uuid.UUID,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> PromptTreeResponse:
    try:
        await ensure_prompts_access(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cabinet_id,
            active_cabinet_id=cs.ctx.cabinet_id,
        )
        data = get_prompt_tree(cs.ctx.user.tenant_id, cabinet_id)
    except PromptError as exc:
        raise _prompt_error(exc) from exc
    return PromptTreeResponse(**data)


@router.get("/cabinets/{cabinet_id}/prompts/file", response_model=PromptFileResponse)
async def get_prompt_file_endpoint(
    cabinet_id: uuid.UUID,
    path: str = Query(min_length=1),
    cs: CabinetSession = Depends(get_cabinet_session),
) -> PromptFileResponse:
    try:
        await ensure_prompts_access(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cabinet_id,
            active_cabinet_id=cs.ctx.cabinet_id,
        )
        data = get_prompt_file(cs.ctx.user.tenant_id, cabinet_id, path)
    except PromptError as exc:
        raise _prompt_error(exc) from exc
    return PromptFileResponse(**data)


@router.put("/cabinets/{cabinet_id}/prompts/file", response_model=PromptFileResponse)
async def put_prompt_file(
    cabinet_id: uuid.UUID,
    body: PutPromptFileRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> PromptFileResponse:
    try:
        await ensure_prompts_access(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cabinet_id,
            active_cabinet_id=cs.ctx.cabinet_id,
        )
        data = save_prompt_file(
            cs.ctx.user.tenant_id,
            cabinet_id,
            body.path,
            body.content,
            expected_etag=if_match,
        )
    except PromptError as exc:
        raise _prompt_error(exc) from exc
    return PromptFileResponse(**data)


@router.post(
    "/cabinets/{cabinet_id}/prompts/versions",
    response_model=PromptVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_prompt_version(
    cabinet_id: uuid.UUID,
    body: CreatePromptVersionRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> PromptVersionResponse:
    try:
        await ensure_prompts_access(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cabinet_id,
            active_cabinet_id=cs.ctx.cabinet_id,
        )
        manifest = create_prompt_version(
            cs.ctx.user.tenant_id,
            cabinet_id,
            label=body.label,
            user_id=cs.ctx.user.user_id,
        )
    except PromptError as exc:
        raise _prompt_error(exc) from exc
    return _to_version_response(manifest)


@router.get("/cabinets/{cabinet_id}/prompts/versions", response_model=PromptVersionListResponse)
async def get_prompt_versions(
    cabinet_id: uuid.UUID,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> PromptVersionListResponse:
    try:
        await ensure_prompts_access(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cabinet_id,
            active_cabinet_id=cs.ctx.cabinet_id,
        )
        items = list_prompt_versions(cs.ctx.user.tenant_id, cabinet_id)
    except PromptError as exc:
        raise _prompt_error(exc) from exc
    return PromptVersionListResponse(
        items=[_to_version_response(item) for item in items]
    )


@router.get(
    "/cabinets/{cabinet_id}/prompts/versions/{version_id}",
    response_model=PromptVersionResponse,
)
async def get_prompt_version_by_id(
    cabinet_id: uuid.UUID,
    version_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> PromptVersionResponse:
    try:
        await ensure_prompts_access(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cabinet_id,
            active_cabinet_id=cs.ctx.cabinet_id,
        )
        manifest = get_prompt_version(cs.ctx.user.tenant_id, cabinet_id, version_id)
    except PromptError as exc:
        raise _prompt_error(exc) from exc
    return _to_version_response(manifest, include_files=True)


@router.post(
    "/cabinets/{cabinet_id}/prompts/versions/{version_id}/rollback",
    response_model=PromptVersionResponse,
)
async def post_prompt_rollback(
    cabinet_id: uuid.UUID,
    version_id: str,
    body: RollbackPromptVersionRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> PromptVersionResponse:
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail={"code": "CONFIRM_REQUIRED", "message": "confirm=true required"},
        )
    try:
        await ensure_prompts_access(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cabinet_id,
            active_cabinet_id=cs.ctx.cabinet_id,
        )
        manifest = rollback_prompt_version(
            cs.ctx.user.tenant_id,
            cabinet_id,
            version_id,
            user_id=cs.ctx.user.user_id,
            create_backup=body.create_backup_version,
        )
    except PromptError as exc:
        raise _prompt_error(exc) from exc
    return _to_version_response(manifest)
