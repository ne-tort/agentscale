"""Signed external trigger ingress (webhook / telegram) — L07."""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.projects.access import ProjectAccessService
from prodavan.application.projects.trigger_service import ProjectTriggerService
from prodavan.domain.errors import AppError
from prodavan.domain.projects import ProjectStatus, verify_webhook_signature


async def enqueue_signed_trigger(
    session: AsyncSession,
    *,
    project_id: str,
    kind: str,
    raw_body: bytes,
    signature_header: str | None,
    secret: str | None,
    secret_name: str,
) -> dict:
    project = await ProjectAccessService(session).get_project(project_id)
    if project.status == ProjectStatus.DELETED:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
    if not secret:
        raise AppError(
            code="WEBHOOK_NOT_CONFIGURED",
            title="Ingress not configured",
            status=503,
            detail=f"company {secret_name} not set",
        )
    if not verify_webhook_signature(secret=secret, body=raw_body, header=signature_header):
        raise AppError(
            code="WEBHOOK_SIGNATURE_INVALID",
            title="Invalid signature",
            status=401,
            detail="X-Prodavan-Signature mismatch",
        )
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="body must be JSON object",
        ) from exc
    if not isinstance(payload, dict):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="body must be JSON object",
        )
    result = await ProjectTriggerService(session).enqueue(
        project_id=project_id, kind=kind, payload=payload
    )
    await session.commit()
    return result
