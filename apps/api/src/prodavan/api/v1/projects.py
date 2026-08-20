"""Project endpoints (M01)."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from prodavan.api.deps import CabinetSession, get_cabinet_session
from prodavan.application.dto.projects import (
    CreateProjectRequest,
    OpenProjectResponse,
    PatchProjectRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectStats,
    ProjectStatsResponse,
)
from prodavan.application.services.project_service import (
    ProjectError,
    archive_project,
    create_project,
    get_project,
    list_projects,
    open_project,
    project_stats,
    restore_project,
)
from prodavan.infrastructure.auth.jwt import create_access_token
from prodavan.infrastructure.storage.project_storage import project_storage_uri

router = APIRouter(tags=["projects"])


def _project_error(exc: ProjectError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


def _to_response(project, *, include_stats: bool = False) -> ProjectResponse:
    stats = None
    if include_stats:
        raw = project_stats(project.tenant_id, project.cabinet_id, project.id)
        stats = ProjectStats(
            runs_total=raw["runs_total"],
            runs_active=raw["runs_active"],
            inbox_pending=raw["inbox_pending"],
        )
    return ProjectResponse(
        id=project.id,
        slug=project.slug,
        display_name=project.display_name,
        status=project.status,
        workspace_key=project.workspace_key,
        storage_uri=project_storage_uri(project.tenant_id, project.cabinet_id, project.id),
        last_opened_at=project.last_opened_at,
        created_at=project.created_at,
        stats=stats,
    )


@router.get("/projects", response_model=ProjectListResponse)
async def get_projects(
    status_filter: str = Query(default="active", alias="status", pattern="^(active|archived|all)$"),
    cs: CabinetSession = Depends(get_cabinet_session),
) -> ProjectListResponse:
    try:
        projects = await list_projects(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            status=status_filter,
        )
    except ProjectError as exc:
        raise _project_error(exc) from exc
    return ProjectListResponse(
        cabinet_id=str(cs.ctx.cabinet_id),
        items=[_to_response(p, include_stats=True) for p in projects],
    )


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def post_project(
    body: CreateProjectRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> ProjectResponse:
    try:
        project = await create_project(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            slug=body.slug,
            display_name=body.display_name,
        )
    except ProjectError as exc:
        raise _project_error(exc) from exc
    return _to_response(project)


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project_by_id(
    project_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> ProjectResponse:
    try:
        project = await get_project(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            project_id=project_id,
        )
    except ProjectError as exc:
        raise _project_error(exc) from exc
    return _to_response(project, include_stats=True)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def patch_project(
    project_id: str,
    body: PatchProjectRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> ProjectResponse:
    try:
        project = await get_project(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            project_id=project_id,
        )
    except ProjectError as exc:
        raise _project_error(exc) from exc
    if body.display_name is not None:
        project.display_name = body.display_name
    await cs.session.commit()
    await cs.session.refresh(project)
    return _to_response(project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> Response:
    try:
        await archive_project(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            project_id=project_id,
        )
    except ProjectError as exc:
        raise _project_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/restore", response_model=ProjectResponse)
async def post_restore_project(
    project_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> ProjectResponse:
    try:
        project = await restore_project(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            project_id=project_id,
        )
    except ProjectError as exc:
        raise _project_error(exc) from exc
    return _to_response(project)


@router.post("/projects/{project_id}/open", response_model=OpenProjectResponse)
async def post_open_project(
    project_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> OpenProjectResponse:
    try:
        project, paths = await open_project(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            project_id=project_id,
        )
    except ProjectError as exc:
        raise _project_error(exc) from exc

    access_token = create_access_token(
        user_id=cs.ctx.user.user_id,
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_ids=cs.ctx.user.cabinet_ids,
        active_cabinet_id=cs.ctx.cabinet_id,
        active_project_id=project.id,
    )
    opened_at = project.last_opened_at or datetime.now(UTC)
    return OpenProjectResponse(
        project_id=project.id,
        workspace_key=project.workspace_key,
        storage_paths=paths,
        opened_at=opened_at,
        access_token=access_token,
    )


@router.get("/projects/{project_id}/stats", response_model=ProjectStatsResponse)
async def get_project_stats(
    project_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> ProjectStatsResponse:
    try:
        await get_project(
            cs.session,
            tenant_id=cs.ctx.user.tenant_id,
            user_id=cs.ctx.user.user_id,
            cabinet_id=cs.ctx.cabinet_id,
            project_id=project_id,
        )
    except ProjectError as exc:
        raise _project_error(exc) from exc
    raw = project_stats(cs.ctx.user.tenant_id, cs.ctx.cabinet_id, project_id)
    return ProjectStatsResponse(
        runs_by_phase=raw["runs_by_phase"],
        inbox_files=raw["inbox_pending"],
        export_files=raw["export_files"],
    )
