"""Integration — project workspace HTTP API (L07/L14)."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")

from prodavan.application.pod_service.ports.workspace import WorkspaceEntry
from prodavan.application.pod_service.workspace_service import PodWorkspaceService
from prodavan.config.settings import settings
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app
from tests.conftest import requires_postgres
from tests.integration.support import owner_auth_from_company


def _token(*, sub: str, email: str | None = None, platform_admin: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
        "iat": now,
        "platform_admin": platform_admin,
        "roles": [] if not platform_admin else ["platform.admin"],
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")


class _MemoryWorkspace:
    """In-memory workspace port for HTTP integration without k8s."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {
            "AGENTS.md": b"# Agents\n",
            "notes.txt": b"hello workspace",
        }

    async def list_entries(self, *, runtime_ref: str, path: str) -> list[WorkspaceEntry]:
        _ = runtime_ref, path
        return [
            WorkspaceEntry(name=name, path=name, kind="file", size=len(data), modified_at=None)
            for name, data in sorted(self.files.items())
        ]

    async def read_bytes(self, *, runtime_ref: str, path: str, max_bytes: int) -> bytes:
        _ = runtime_ref
        return self.files[path][:max_bytes]

    async def stat(self, *, runtime_ref: str, path: str) -> WorkspaceEntry:
        _ = runtime_ref
        data = self.files[path]
        return WorkspaceEntry(name=path, path=path, kind="file", size=len(data), modified_at=None)

    async def delete(self, *, runtime_ref: str, path: str) -> None:
        _ = runtime_ref
        del self.files[path]

    async def move(self, *, runtime_ref: str, src: str, dst: str) -> None:
        _ = runtime_ref
        self.files[dst] = self.files.pop(src)

    async def copy(self, *, runtime_ref: str, src: str, dst: str) -> None:
        _ = runtime_ref
        self.files[dst] = self.files[src]


async def _running_runtime_gate(
    self: PodWorkspaceService,
    *,
    project_id: str,
    principal,
    employee,
    write: bool = False,
) -> tuple[str, str]:
    await self._access.require_access(
        project_id=project_id,
        principal=principal,
        employee=employee,
        write=write,
        allow_paused=True,
    )
    return "pod-wk-integration", ""


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    reset_invite_client()
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def running_workspace(monkeypatch: pytest.MonkeyPatch) -> _MemoryWorkspace:
    ws = _MemoryWorkspace()
    monkeypatch.setattr(
        "prodavan.application.pod_service.workspace_service.build_pod_workspace",
        lambda: ws,
    )
    monkeypatch.setattr(PodWorkspaceService, "_require_running_runtime", _running_runtime_gate)
    return ws


def _launch_project(client: TestClient) -> tuple[dict[str, str], str, str]:
    suffix = uuid.uuid4().hex[:8]
    admin_h = {"Authorization": f"Bearer {_token(sub=f'ws-admin-{suffix}', platform_admin=True)}"}
    co = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={
            "name": f"WsCo-{suffix}",
            "password": "test-company-pass",
            "admin_email": f"ws-{suffix}@co.test",
        },
    )
    assert co.status_code == 201, co.text
    company_id = co.json()["company"]["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers=admin_h,
        json={
            "name": "Cursor",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-ws-int",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text
    key_id = key.json()["id"]

    owner_h = owner_auth_from_company(_token, co.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers=owner_h,
        json={"name": "WsCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=owner_h,
        json={"name": "WsProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    patched = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=owner_h,
        json={"agent_provider": "cursor", "resolved_ai_key_id": key_id},
    )
    assert patched.status_code == 200, patched.text

    launched = client.post(f"/api/v1/projects/{project_id}/launch", headers=owner_h)
    assert launched.status_code == 200, launched.text
    return owner_h, project_id, company_id


@requires_postgres
def test_workspace_list_entries(client: TestClient, running_workspace: _MemoryWorkspace) -> None:
    owner_h, project_id, _ = _launch_project(client)

    listed = client.get(
        f"/api/v1/projects/{project_id}/container/workspace/entries",
        headers=owner_h,
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    names = {e["name"] for e in body["entries"]}
    assert names == {"AGENTS.md", "notes.txt"}


@requires_postgres
def test_workspace_preview_and_download(client: TestClient, running_workspace: _MemoryWorkspace) -> None:
    owner_h, project_id, _ = _launch_project(client)

    preview = client.get(
        f"/api/v1/projects/{project_id}/container/workspace/preview",
        headers=owner_h,
        params={"path": "AGENTS.md"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["content"].startswith("# Agents")

    download = client.get(
        f"/api/v1/projects/{project_id}/container/workspace/content",
        headers=owner_h,
        params={"path": "notes.txt"},
    )
    assert download.status_code == 200, download.text
    assert download.content == b"hello workspace"


@requires_postgres
def test_workspace_mutations(client: TestClient, running_workspace: _MemoryWorkspace) -> None:
    owner_h, project_id, _ = _launch_project(client)
    base = f"/api/v1/projects/{project_id}/container/workspace"

    deleted = client.delete(f"{base}/entries", headers=owner_h, params={"path": "notes.txt"})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True
    assert "notes.txt" not in running_workspace.files

    moved = client.post(
        f"{base}/move",
        headers=owner_h,
        json={"src": "AGENTS.md", "dst": "README.md"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["dst"] == "README.md"
    assert "README.md" in running_workspace.files

    copied = client.post(
        f"{base}/copy",
        headers=owner_h,
        json={"src": "README.md", "dst": "AGENTS.md"},
    )
    assert copied.status_code == 200, copied.text
    assert "AGENTS.md" in running_workspace.files


@requires_postgres
def test_workspace_rejects_when_pod_not_running(client: TestClient) -> None:
    owner_h, project_id, _ = _launch_project(client)

    listed = client.get(
        f"/api/v1/projects/{project_id}/container/workspace/entries",
        headers=owner_h,
    )
    assert listed.status_code == 409, listed.text
    assert listed.json()["code"] == "POD_NOT_RUNNING"


@requires_postgres
def test_company_workspace_entries(client: TestClient, running_workspace: _MemoryWorkspace) -> None:
    owner_h, project_id, company_id = _launch_project(client)

    listed = client.get(
        f"/api/v1/companies/{company_id}/containers/{project_id}/workspace/entries",
        headers=owner_h,
    )
    assert listed.status_code == 200, listed.text
    names = {e["name"] for e in listed.json()["entries"]}
    assert "AGENTS.md" in names


@requires_postgres
def test_workspace_preview_rejects_binary(client: TestClient, running_workspace: _MemoryWorkspace) -> None:
    owner_h, project_id, _ = _launch_project(client)
    running_workspace.files["blob.bin"] = b"\x00binary"

    preview = client.get(
        f"/api/v1/projects/{project_id}/container/workspace/preview",
        headers=owner_h,
        params={"path": "blob.bin"},
    )
    assert preview.status_code == 415, preview.text
    assert preview.json()["code"] == "UNSUPPORTED_MEDIA"
