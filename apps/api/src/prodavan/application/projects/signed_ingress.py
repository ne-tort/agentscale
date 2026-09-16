"""Signed external trigger ingress (webhook / telegram) — L07."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.project_service import ProjectAccessPolicy
from prodavan.application.projects.trigger_service import ProjectTriggerService
from prodavan.domain.errors import AppError
from prodavan.domain.projects import ProjectStatus, verify_webhook_signature

if TYPE_CHECKING:
    from prodavan.infrastructure.persistence.models.projects import ProjectRow


def _ingress_not_found() -> AppError:
    """Generic 404 for missing project / missing webhook secret (audit API-P2d)."""
    return AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")


async def enqueue_signed_trigger(
    session: AsyncSession,
    *,
    project_id: str,
    kind: str,
    raw_body: bytes,
    signature_header: str | None,
    secret: str | None,
    project: ProjectRow | None = None,
) -> dict:
    """Enqueue a signed external trigger.

    Audit API-P2d (webhook existence leak): a missing project and a missing
    webhook secret are both answered with the same generic 404 so an attacker
    cannot enumerate project ids by distinguishing 404 (no project) from 503
    (project exists but webhook not configured). Signature failure stays 401
    (it is the same for existing and non-existing projects once the secret is
    known). Callers should load the project via ``get_project_or_none`` and
    pass ``secret=None`` when either the project or the secret is missing so
    both collapse to the generic 404 here.

    ``project`` may be passed by the caller (route handler already loaded it
    for secret resolution) to avoid a duplicate DB round-trip; when ``None``,
    it is loaded here via ``get_project_or_none``.
    """
    if project is None:
        project = await ProjectAccessPolicy(session).get_project_or_none(project_id)
    if project is None or project.status == ProjectStatus.DELETED:
        raise _ingress_not_found()
    if not secret:
        # Same shape as a missing project — do not reveal existence.
        raise _ingress_not_found()
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
