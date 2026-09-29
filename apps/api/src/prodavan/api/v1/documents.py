"""Documents Service HTTP — convert / read / create / fill-template (DOCUM).

Project-scoped surface for Project Pods (Pod Identity Bridge JWT + the
``documents`` scope), mirroring the pod_modules / tenant_infra pattern.
Outputs are content assets; the response carries the canonical FileRef.
Admin health: ``GET /documents/health`` (platform admin, converter status).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from prodavan.api.agent_auth import PodBridgeDep
from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.api.rate_limit import enforce_rate_limit
from prodavan.application.documents.service import DocumentsService
from prodavan.application.pod_identity.bridge import SCOPE_DOCUMENTS, PodBridgeClaims
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(tags=["documents"])


class FileRefBody(BaseModel):
    """Canonical FileRef (same shape cabinet/module uploads return)."""

    model_config = {"extra": "forbid"}

    asset_id: str | None = None
    version_id: str | None = None
    blob_version_id: str | None = None
    storage_key: str | None = None
    filename: str | None = None
    size: int | None = None
    sha256: str | None = None


class ConvertBody(BaseModel):
    model_config = {"extra": "forbid"}

    file_ref: FileRefBody
    filename: str | None = None
    target_format: str


class ReadBody(BaseModel):
    model_config = {"extra": "forbid"}

    file_ref: FileRefBody
    filename: str | None = None
    sheet: str | None = None
    limit: int | None = Field(default=None, ge=1, le=5000)


class CreateBody(BaseModel):
    model_config = {"extra": "forbid"}

    format: str
    spec: dict[str, Any]
    filename: str | None = None


class FillTemplateBody(BaseModel):
    model_config = {"extra": "forbid"}

    template_ref: FileRefBody
    filename: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    output_format: str | None = None


def _bridge_principal(bridge: PodBridgeClaims) -> Principal:
    """Audit attribution for pod-driven document operations (mirrors internal/pods)."""
    sub = f"pod-bridge:{bridge.pod_id}"
    if bridge.acting_employee_id:
        sub = f"pod-bridge:{bridge.pod_id}:emp:{bridge.acting_employee_id}"
    return Principal(sub=sub, roles=frozenset())


async def _bridge_employee(bridge: PodBridgeClaims, session: SessionDep) -> EmployeeRow:
    """Asset creation requires an employee (same guard as agent routes)."""
    if not bridge.acting_employee_id:
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="employee required for documents operations",
        )
    employee = await session.get(EmployeeRow, str(bridge.acting_employee_id))
    if employee is None:
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="acting employee not found",
        )
    return employee


def _require(bridge: PodBridgeClaims, project_id: str) -> None:
    bridge.require_project(project_id)
    bridge.require_scope(SCOPE_DOCUMENTS)


def _source_ref(body_ref: FileRefBody, fallback_filename: str | None) -> tuple[dict[str, Any], str]:
    ref = body_ref.model_dump()
    filename = (fallback_filename or "").strip() or str(ref.get("filename") or "").strip()
    if not ref.get("storage_key"):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="file_ref.storage_key required",
        )
    if not filename:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="filename required",
        )
    return ref, filename


@router.post("/projects/{project_id}/documents/convert")
async def convert_document(
    project_id: str,
    body: ConvertBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Convert a stored document (FileRef) and persist the result as an asset."""
    _require(bridge, project_id)
    await enforce_rate_limit(
        key=f"documents_convert:project:{project_id}",
        limit=settings.gotenberg_rate_limit_per_minute,
        window_sec=60,
        detail="documents convert rate limit exceeded for this project",
    )
    employee = await _bridge_employee(bridge, session)
    source, filename = _source_ref(body.file_ref, body.filename)
    service = DocumentsService(session)
    # session=None for emits: UploadService already committed the output, so
    # events/metrics publish directly to the bus instead of waiting for a
    # follow-up commit that never happens on this request path.
    ref = await service.convert(
        source,
        filename=filename,
        target_format=body.target_format,
        company_id=bridge.company_id,
        cabinet_id=bridge.cabinet_id,
        project_id=bridge.project_id,
        principal=_bridge_principal(bridge),
        employee=employee,
        session=None,
    )
    return {"file_ref": ref}


@router.post("/projects/{project_id}/documents/read")
async def read_document(
    project_id: str,
    body: ReadBody,
    bridge: PodBridgeDep,
) -> dict[str, Any]:
    """Structured read: xlsx sheets+rows, docx/pdf/text plain text."""
    _require(bridge, project_id)
    source, filename = _source_ref(body.file_ref, body.filename)
    service = DocumentsService()
    return await service.read_document(
        source,
        filename=filename,
        company_id=bridge.company_id,
        cabinet_id=bridge.cabinet_id,
        project_id=bridge.project_id,
        session=None,
        sheet=body.sheet,
        limit=body.limit,
    )


@router.post("/projects/{project_id}/documents/create")
async def create_document(
    project_id: str,
    body: CreateBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Create a new document from a declarative spec (xlsx / docx / text)."""
    _require(bridge, project_id)
    employee = await _bridge_employee(bridge, session)
    service = DocumentsService(session)
    ref = await service.create_document(
        format=body.format,
        spec=body.spec,
        company_id=bridge.company_id,
        cabinet_id=bridge.cabinet_id,
        project_id=bridge.project_id,
        principal=_bridge_principal(bridge),
        employee=employee,
        session=None,
        filename=body.filename,
    )
    return {"file_ref": ref}


@router.post("/projects/{project_id}/documents/fill-template")
async def fill_document_template(
    project_id: str,
    body: FillTemplateBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Fill a docx (context) / xlsx (cells) template and persist the output."""
    _require(bridge, project_id)
    employee = await _bridge_employee(bridge, session)
    template, filename = _source_ref(body.template_ref, body.filename)
    service = DocumentsService(session)
    ref = await service.fill_template(
        template,
        filename=filename,
        data=body.data,
        output_format=body.output_format,
        company_id=bridge.company_id,
        cabinet_id=bridge.cabinet_id,
        project_id=bridge.project_id,
        principal=_bridge_principal(bridge),
        employee=employee,
        session=None,
    )
    return {"file_ref": ref}


@router.get("/documents/health")
async def documents_health(
    _admin: PlatformAdminDep,
) -> dict[str, Any]:
    """Converter status: gotenberg when the manager is live, else local."""
    from prodavan.core.infra.gotenberg_manager import get_gotenberg_manager

    mgr = get_gotenberg_manager()
    if mgr is not None and mgr.enabled and mgr.converter is not None:
        return {"converter": "gotenberg", "ok": await mgr.ping()}
    return {"converter": "local", "ok": True}
