"""Unit tests — signed ingress enumeration guard (audit API-P2d)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prodavan.application.projects import signed_ingress as mod
from prodavan.application.project_service import ProjectAccessPolicy
from prodavan.domain.errors import AppError
from prodavan.domain.projects import ProjectStatus, webhook_signature

_BODY = b'{"hello":"world"}'


def _project(*, project_id: str = "prj_1", status: str = ProjectStatus.ACTIVE):
    return SimpleNamespace(id=project_id, company_id="co_1", status=status)


class _TriggersStub:
    def __init__(self, _session) -> None:
        self.payload = None

    async def enqueue(self, *, project_id, kind, payload) -> dict:
        self.payload = payload
        return {"ok": True, "project_id": project_id, "kind": kind}


@pytest.mark.asyncio
async def test_missing_project_returns_generic_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ProjectAccessPolicy, "get_project_or_none", AsyncMock(return_value=None)
    )
    with pytest.raises(AppError) as exc:
        await mod.enqueue_signed_trigger(
            object(),
            project_id="prj_missing",
            kind="webhook.http",
            raw_body=_BODY,
            signature_header="sha256=deadbeef",
            secret="s",
        )
    assert exc.value.status == 404
    assert "Project not found" in (exc.value.detail or "")


@pytest.mark.asyncio
async def test_missing_secret_collapses_to_same_404_as_missing_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ProjectAccessPolicy, "get_project_or_none", AsyncMock(return_value=_project())
    )
    with pytest.raises(AppError) as exc:
        await mod.enqueue_signed_trigger(
            object(),
            project_id="prj_1",
            kind="webhook.http",
            raw_body=_BODY,
            signature_header="sha256=deadbeef",
            secret=None,
        )
    # API-P2d: missing secret must look identical to a missing project.
    assert exc.value.status == 404
    assert exc.value.code == "NOT_FOUND"
    assert "Project not found" in (exc.value.detail or "")


@pytest.mark.asyncio
async def test_invalid_signature_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ProjectAccessPolicy, "get_project_or_none", AsyncMock(return_value=_project())
    )
    with pytest.raises(AppError) as exc:
        await mod.enqueue_signed_trigger(
            object(),
            project_id="prj_1",
            kind="webhook.http",
            raw_body=_BODY,
            signature_header="sha256=deadbeef",
            secret="real-secret",
        )
    assert exc.value.status == 401
    assert exc.value.code == "WEBHOOK_SIGNATURE_INVALID"


@pytest.mark.asyncio
async def test_deleted_project_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ProjectAccessPolicy,
        "get_project_or_none",
        AsyncMock(return_value=_project(status=ProjectStatus.DELETED)),
    )
    with pytest.raises(AppError) as exc:
        await mod.enqueue_signed_trigger(
            object(),
            project_id="prj_1",
            kind="webhook.http",
            raw_body=_BODY,
            signature_header=None,
            secret="s",
        )
    assert exc.value.status == 404


@pytest.mark.asyncio
async def test_valid_signature_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ProjectAccessPolicy, "get_project_or_none", AsyncMock(return_value=_project())
    )
    monkeypatch.setattr(mod, "ProjectTriggerService", _TriggersStub)

    session = SimpleNamespace(commit=AsyncMock())
    secret = "real-secret"
    sig = webhook_signature(secret, _BODY)
    result = await mod.enqueue_signed_trigger(
        session,
        project_id="prj_1",
        kind="webhook.http",
        raw_body=_BODY,
        signature_header=sig,
        secret=secret,
    )
    assert result["ok"] is True
    assert result["project_id"] == "prj_1"
    assert result["kind"] == "webhook.http"
    assert json.loads(_BODY) == {"hello": "world"}
