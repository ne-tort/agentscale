"""Prompt read endpoints (M03 slice)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from prodavan.api.deps import CabinetSession, get_cabinet_session
from prodavan.application.dto.prompts import PromptFileResponse, PromptTreeResponse
from prodavan.application.services.cabinet_service import CabinetError, get_cabinet
from prodavan.infrastructure.storage.prompt_storage import (
    PromptPathError,
    build_prompt_tree,
    prompts_root,
    read_prompt_file,
)

router = APIRouter(tags=["prompts"])


@router.get("/cabinets/{cabinet_id}/prompts/tree", response_model=PromptTreeResponse)
async def get_prompts_tree(
    cabinet_id: uuid.UUID,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> PromptTreeResponse:
    if cabinet_id != cs.ctx.cabinet_id:
        raise HTTPException(
            status_code=403,
            detail={"code": "CABINET_MISMATCH", "message": "Cabinet id mismatch"},
        )
    try:
        await get_cabinet(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cabinet_id,
        )
    except CabinetError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    root = prompts_root(cs.ctx.user.tenant_id, cabinet_id)
    return PromptTreeResponse(root="prompts/", tree=build_prompt_tree(root))


@router.get("/cabinets/{cabinet_id}/prompts/file", response_model=PromptFileResponse)
async def get_prompt_file(
    cabinet_id: uuid.UUID,
    path: str = Query(min_length=1),
    cs: CabinetSession = Depends(get_cabinet_session),
) -> PromptFileResponse:
    if cabinet_id != cs.ctx.cabinet_id:
        raise HTTPException(
            status_code=403,
            detail={"code": "CABINET_MISMATCH", "message": "Cabinet id mismatch"},
        )
    try:
        data = read_prompt_file(cs.ctx.user.tenant_id, cabinet_id, path)
    except PromptPathError as exc:
        code = str(exc)
        status = 404 if code == "FILE_NOT_FOUND" else 422
        raise HTTPException(
            status_code=status,
            detail={"code": code, "message": code},
        ) from exc
    return PromptFileResponse(**data)
