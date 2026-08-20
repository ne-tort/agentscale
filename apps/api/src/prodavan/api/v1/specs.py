"""Spec run endpoints (M02). Nested under /projects/{project_id}."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from prodavan.api.deps import CabinetSession, get_cabinet_session
from prodavan.application.dto.runs import (
    AdvanceRunRequest,
    CreateRunRequest,
    ExportKpRequest,
    FinalizeRunRequest,
)
from prodavan.application.services.pipeline_service import (
    PipelineError,
    advance_run,
    create_run,
    describe_run,
    export_kp,
    finalize_run,
    list_lineitems,
    list_offers,
    resolve_export_file,
    upload_inbox,
)

router = APIRouter(tags=["specs"])

_ALLOWED_UPLOAD = {".xlsx", ".xls", ".csv", ".txt"}


def _pipeline_error(exc: PipelineError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


@router.post("/projects/{project_id}/inbox/upload", status_code=status.HTTP_201_CREATED)
async def post_inbox_upload(
    project_id: str,
    file: UploadFile = File(...),
    auto_run: bool = Form(default=False),
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    filename = file.filename or "upload.bin"
    suffix = filename[filename.rfind(".") :].lower() if "." in filename else ""
    if suffix not in _ALLOWED_UPLOAD:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_FILENAME", "message": "Allowed: xlsx, xls, csv, txt"},
        )
    data = await file.read()
    try:
        return await upload_inbox(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            project_id=project_id,
            filename=filename,
            data=data,
            auto_run=auto_run,
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.post("/projects/{project_id}/runs", status_code=status.HTTP_201_CREATED)
async def post_run(
    project_id: str,
    body: CreateRunRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    try:
        return await create_run(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            project_id=project_id,
            input_filename=body.input_filename,
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.get("/projects/{project_id}/runs/{run_id}")
async def get_run(
    project_id: str,
    run_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    try:
        return describe_run(cs.ctx.user.tenant_id, cs.ctx.cabinet_id, project_id, run_id)
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.post("/projects/{project_id}/runs/{run_id}/advance", status_code=status.HTTP_202_ACCEPTED)
async def post_advance(
    project_id: str,
    run_id: str,
    body: AdvanceRunRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    try:
        return await advance_run(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            project_id=project_id,
            run_id=run_id,
            target_phase=body.target_phase,
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.post("/projects/{project_id}/runs/{run_id}/finalize")
async def post_finalize(
    project_id: str,
    run_id: str,
    body: FinalizeRunRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    try:
        return await finalize_run(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            project_id=project_id,
            run_id=run_id,
            confirmed=body.confirmed,
            operator_note=body.operator_note,
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.get("/projects/{project_id}/runs/{run_id}/lineitems")
async def get_lineitems(
    project_id: str,
    run_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    try:
        return list_lineitems(cs.ctx.user.tenant_id, cs.ctx.cabinet_id, project_id, run_id)
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.get("/projects/{project_id}/runs/{run_id}/offers")
async def get_offers(
    project_id: str,
    run_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    try:
        return list_offers(cs.ctx.user.tenant_id, cs.ctx.cabinet_id, project_id, run_id)
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.post("/projects/{project_id}/export/kp")
async def post_export_kp(
    project_id: str,
    body: ExportKpRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    try:
        return await export_kp(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            project_id=project_id,
            run_id=body.run_id,
            include_alternatives=body.include_alternatives,
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.get("/projects/{project_id}/export/{filename}")
async def get_export_file(
    project_id: str,
    filename: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> FileResponse:
    try:
        path = resolve_export_file(
            cs.ctx.user.tenant_id, cs.ctx.cabinet_id, project_id, filename
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )
