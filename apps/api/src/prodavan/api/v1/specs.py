"""Spec run endpoints — platform facade over Cabinet SPI (procurement pipeline)."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from prodavan.api.deps import CabinetSession, get_cabinet_session
from prodavan.application.dto.runs import (
    AdvanceRunRequest,
    CreateRunRequest,
    ExportKpRequest,
    FinalizeRunRequest,
)
from prodavan.cabinets.electronics_procurement.services.pipeline_service import PipelineError
from prodavan.cabinets.host import load_cabinet, module_for_cabinet, require_raw_capability, spi_ctx_from

router = APIRouter(tags=["specs"])

_ALLOWED_UPLOAD = {".xlsx", ".xls", ".csv", ".txt"}


def _pipeline_error(exc: PipelineError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


async def _module(cs: CabinetSession):
    cabinet = await load_cabinet(cs.session, cs.ctx.cabinet_id)
    require_raw_capability(cabinet, "procurement.pipeline")
    return module_for_cabinet(cabinet), cabinet


@router.post("/projects/{project_id}/inbox/upload", status_code=status.HTTP_201_CREATED)
async def post_inbox_upload(
    project_id: str,
    file: UploadFile = File(...),
    auto_run: bool = Form(default=False),
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module, _ = await _module(cs)
    filename = file.filename or "upload.bin"
    suffix = filename[filename.rfind(".") :].lower() if "." in filename else ""
    if suffix not in _ALLOWED_UPLOAD:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_FILENAME", "message": "Allowed: xlsx, xls, csv, txt"},
        )
    data = await file.read()
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    try:
        return await module.execute_command(
            ctx,
            "upload_inbox",
            {
                "session": cs.session,
                "project_id": project_id,
                "filename": filename,
                "data": data,
                "auto_run": auto_run,
            },
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "UNKNOWN_COMMAND", "message": str(exc)}) from exc


@router.post("/projects/{project_id}/runs", status_code=status.HTTP_201_CREATED)
async def post_run(
    project_id: str,
    body: CreateRunRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module, _ = await _module(cs)
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    try:
        return await module.execute_command(
            ctx,
            "create_run",
            {
                "session": cs.session,
                "project_id": project_id,
                "input_filename": body.input_filename,
            },
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.get("/projects/{project_id}/runs")
async def get_runs(
    project_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module, _ = await _module(cs)
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    try:
        return await module.execute_query(
            ctx, "list_runs", {"session": cs.session, "project_id": project_id}
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.get("/projects/{project_id}/runs/{run_id}")
async def get_run(
    project_id: str,
    run_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module, _ = await _module(cs)
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    try:
        return await module.execute_query(
            ctx,
            "describe_run",
            {"session": cs.session, "project_id": project_id, "run_id": run_id},
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.post("/projects/{project_id}/runs/{run_id}/advance", status_code=status.HTTP_202_ACCEPTED)
async def post_advance(
    project_id: str,
    run_id: str,
    body: AdvanceRunRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module, _ = await _module(cs)
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    try:
        return await module.execute_command(
            ctx,
            "advance_run",
            {
                "session": cs.session,
                "project_id": project_id,
                "run_id": run_id,
                "target_phase": body.target_phase,
            },
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
    module, _ = await _module(cs)
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    try:
        return await module.execute_command(
            ctx,
            "finalize_run",
            {
                "session": cs.session,
                "project_id": project_id,
                "run_id": run_id,
                "confirmed": body.confirmed,
                "operator_note": body.operator_note,
            },
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.get("/projects/{project_id}/runs/{run_id}/lineitems")
async def get_lineitems(
    project_id: str,
    run_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module, _ = await _module(cs)
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    try:
        return await module.execute_query(
            ctx,
            "list_lineitems",
            {"session": cs.session, "project_id": project_id, "run_id": run_id},
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.get("/projects/{project_id}/runs/{run_id}/offers")
async def get_offers(
    project_id: str,
    run_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module, _ = await _module(cs)
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    try:
        return await module.execute_query(
            ctx,
            "list_offers",
            {"session": cs.session, "project_id": project_id, "run_id": run_id},
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.post("/projects/{project_id}/export/kp")
async def post_export_kp(
    project_id: str,
    body: ExportKpRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    module, _ = await _module(cs)
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    try:
        return await module.execute_command(
            ctx,
            "export_kp",
            {
                "session": cs.session,
                "project_id": project_id,
                "run_id": body.run_id,
                "include_alternatives": body.include_alternatives,
            },
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc


@router.get("/projects/{project_id}/export/{filename}")
async def get_export_file(
    project_id: str,
    filename: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> FileResponse:
    module, _ = await _module(cs)
    ctx = spi_ctx_from(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    try:
        result = await module.execute_query(
            ctx,
            "resolve_export_file",
            {"session": cs.session, "project_id": project_id, "filename": filename},
        )
    except PipelineError as exc:
        raise _pipeline_error(exc) from exc
    path = result["path"]
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )
