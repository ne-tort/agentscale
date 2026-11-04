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
from prodavan.domain.errors import AppError
from prodavan.application.project_service.access import ProjectAccessPolicy
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
        self.directories: set[str] = {"folder"}

    async def list_entries(self, *, runtime_ref: str, path: str) -> list[WorkspaceEntry]:
        _ = runtime_ref
        prefix = f"{path}/" if path else ""
        entries = []
        for item_path in sorted([*self.files, *self.directories]):
            if not item_path.startswith(prefix):
                continue
            name = item_path[len(prefix) :]
            if not name or "/" in name:
                continue
            is_dir = item_path in self.directories
            entries.append(
                WorkspaceEntry(
                    name=name,
                    path=item_path,
                    kind="dir" if is_dir else "file",
                    size=None if is_dir else len(self.files[item_path]),
                    modified_at=None,
                )
            )
        return entries

    async def read_bytes(self, *, runtime_ref: str, path: str, max_bytes: int) -> bytes:
        _ = runtime_ref
        return self.files[path][:max_bytes]

    async def stat(self, *, runtime_ref: str, path: str) -> WorkspaceEntry:
        _ = runtime_ref
        if path in self.directories:
            return WorkspaceEntry(name=path.rsplit("/", 1)[-1], path=path, kind="dir", size=None, modified_at=None)
        if path not in self.files:
            raise AppError(
                code="WORKSPACE_NOT_FOUND",
                title="Not Found",
                status=404,
                detail="workspace path not found",
                extra={"path": path},
            )
        data = self.files[path]
        return WorkspaceEntry(name=path, path=path, kind="file", size=len(data), modified_at=None)

    async def create_directory(self, *, runtime_ref: str, path: str) -> None:
        _ = runtime_ref
        if path in self.files or path in self.directories:
            raise AppError(
                code="WORKSPACE_ALREADY_EXISTS",
                title="Conflict",
                status=409,
                detail="workspace path already exists",
                extra={"path": path, "kind": "file" if path in self.files else "dir"},
            )
        parts = path.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in self.files:
                raise AppError(
                    code="WORKSPACE_PATH_CONFLICT",
                    title="Conflict",
                    status=409,
                    detail="a parent workspace path is not a directory",
                    extra={"path": path},
                )
        self.directories.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))

    async def delete(self, *, runtime_ref: str, path: str) -> None:
        _ = runtime_ref
        if path not in self.files:
            raise AppError(
                code="WORKSPACE_NOT_FOUND",
                title="Not Found",
                status=404,
                detail="workspace path not found",
                extra={"path": path},
            )
        del self.files[path]

    async def move(self, *, runtime_ref: str, src: str, dst: str) -> None:
        _ = runtime_ref
        self.files[dst] = self.files.pop(src)

    async def copy(self, *, runtime_ref: str, src: str, dst: str) -> None:
        _ = runtime_ref
        self.files[dst] = self.files[src]

    async def write_bytes(self, *, runtime_ref: str, path: str, data: bytes) -> None:
        _ = runtime_ref
        if path in self.directories:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="path is a directory")
        parts = path.split("/")
        self.directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
        self.files[path] = data


async def _running_runtime_gate(
    self: PodWorkspaceService,
    *,
    project_id: str,
    principal,
    employee,
    write: bool = False,
) -> tuple[str, str]:
    await ProjectAccessPolicy(self._session).require_access(
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
    assert names == {"AGENTS.md", "notes.txt", "folder"}


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
def test_workspace_download_contract_is_consistent_across_scopes(
    client: TestClient, running_workspace: _MemoryWorkspace
) -> None:
    owner_h, project_id, company_id = _launch_project(client)
    routes = (
        (f"/api/v1/projects/{project_id}/container/workspace/content", owner_h),
        (f"/api/v1/companies/{company_id}/containers/{project_id}/workspace/content", owner_h),
        (
            f"/api/v1/admin/containers/{project_id}/workspace/content",
            {"Authorization": f"Bearer {_token(sub='workspace-contract-admin', platform_admin=True)}"},
        ),
    )

    for endpoint, headers in routes:
        response = client.get(endpoint, headers=headers, params={"path": "./notes.txt"})
        assert response.status_code == 200, response.text
        assert response.content == b"hello workspace"
        assert response.headers["x-workspace-path"] == "notes.txt"
        assert response.headers["x-workspace-size"] == str(len(running_workspace.files["notes.txt"]))
        assert response.headers["content-disposition"] == 'attachment; filename="notes.txt"'


@requires_postgres
def test_workspace_download_rejects_oversized_file(
    client: TestClient, running_workspace: _MemoryWorkspace
) -> None:
    owner_h, project_id, _ = _launch_project(client)
    running_workspace.files["large.bin"] = b"x" * (10_485_760 + 1)

    download = client.get(
        f"/api/v1/projects/{project_id}/container/workspace/content",
        headers=owner_h,
        params={"path": "large.bin"},
    )

    assert download.status_code == 413, download.text
    assert download.json()["code"] == "PAYLOAD_TOO_LARGE"


@requires_postgres
def test_workspace_upload_creates_and_replaces_file(
    client: TestClient, running_workspace: _MemoryWorkspace
) -> None:
    owner_h, project_id, _ = _launch_project(client)
    endpoint = f"/api/v1/projects/{project_id}/container/workspace/content"

    uploaded = client.put(
        endpoint,
        headers=owner_h,
        params={"path": "notes/new.txt"},
        content=b"created from UI",
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json() == {"written": True, "path": "notes/new.txt", "size": 15}
    assert running_workspace.files["notes/new.txt"] == b"created from UI"

    replaced = client.put(
        endpoint,
        headers=owner_h,
        params={"path": "notes/new.txt"},
        content=b"replaced",
    )
    assert replaced.status_code == 200, replaced.text
    assert running_workspace.files["notes/new.txt"] == b"replaced"


@requires_postgres
def test_workspace_upload_acceptance_empty_and_normalized_atomic_replace(
    client: TestClient, running_workspace: _MemoryWorkspace
) -> None:
    owner_h, project_id, _ = _launch_project(client)
    endpoint = f"/api/v1/projects/{project_id}/container/workspace/content"

    created = client.put(
        endpoint,
        headers=owner_h,
        params={"path": "notes/./atomic.txt"},
        content=b"first",
    )
    assert created.status_code == 200, created.text
    assert created.json() == {"written": True, "path": "notes/atomic.txt", "size": 5}

    emptied = client.put(
        endpoint,
        headers={**owner_h, "Content-Type": "application/octet-stream"},
        params={"path": "notes\\\\atomic.txt"},
        content=b"",
    )
    assert emptied.status_code == 200, emptied.text
    assert emptied.json() == {"written": True, "path": "notes/atomic.txt", "size": 0}
    assert running_workspace.files["notes/atomic.txt"] == b""


@requires_postgres
def test_workspace_upload_rejects_root_normalization_paths(
    client: TestClient, running_workspace: _MemoryWorkspace
) -> None:
    owner_h, project_id, _ = _launch_project(client)
    endpoint = f"/api/v1/projects/{project_id}/container/workspace/content"

    for path in (".", "notes/.."):
        uploaded = client.put(
            endpoint,
            headers=owner_h,
            params={"path": path},
            content=b"must not overwrite workspace root",
        )
        assert uploaded.status_code == 422, uploaded.text

    assert running_workspace.files["notes.txt"] == b"hello workspace"


@requires_postgres
def test_workspace_upload_rejects_directory_path_with_metadata(
    client: TestClient, running_workspace: _MemoryWorkspace
) -> None:
    owner_h, project_id, _ = _launch_project(client)
    endpoint = f"/api/v1/projects/{project_id}/container/workspace/content"

    uploaded = client.put(
        endpoint,
        headers=owner_h,
        params={"path": "folder"},
        content=b"must not replace directory",
    )

    assert uploaded.status_code == 422, uploaded.text
    assert uploaded.json()["code"] == "VALIDATION_ERROR"
    assert uploaded.json()["path"] == "folder"
    assert uploaded.json()["kind"] == "dir"


@requires_postgres
def test_workspace_mkdir_creates_root_and_nested_directories(
    client: TestClient, running_workspace: _MemoryWorkspace
) -> None:
    owner_h, project_id, _ = _launch_project(client)
    endpoint = f"/api/v1/projects/{project_id}/container/workspace/mkdir"

    created = client.post(endpoint, headers=owner_h, json={"path": "docs"})
    assert created.status_code == 201, created.text
    assert created.json() == {"created": True, "path": "docs"}

    nested = client.post(endpoint, headers=owner_h, json={"path": "docs/reference"})
    assert nested.status_code == 201, nested.text
    assert nested.json() == {"created": True, "path": "docs/reference"}

    readback = client.get(
        f"/api/v1/projects/{project_id}/container/workspace/entries",
        headers=owner_h,
        params={"path": "docs"},
    )
    assert readback.status_code == 200, readback.text
    assert [(entry["path"], entry["kind"]) for entry in readback.json()["entries"]] == [
        ("docs/reference", "dir")
    ]
    assert "docs" in running_workspace.directories
    assert "docs/reference" in running_workspace.directories


@requires_postgres
def test_workspace_mkdir_rejects_collisions_and_traversal(
    client: TestClient, running_workspace: _MemoryWorkspace
) -> None:
    owner_h, project_id, _ = _launch_project(client)
    endpoint = f"/api/v1/projects/{project_id}/container/workspace/mkdir"

    for path, kind in (("folder", "dir"), ("notes.txt", "file")):
        conflict = client.post(endpoint, headers=owner_h, json={"path": path})
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["code"] == "WORKSPACE_ALREADY_EXISTS"
        assert conflict.json()["path"] == path
        if kind == "file":
            assert conflict.json()["kind"] == kind

    traversal = client.post(endpoint, headers=owner_h, json={"path": "%252e%252e%252fsecret"})
    assert traversal.status_code == 422, traversal.text
    assert "secret" not in running_workspace.files

    parent_file = client.post(endpoint, headers=owner_h, json={"path": "notes.txt/child"})
    assert parent_file.status_code == 409, parent_file.text
    assert parent_file.json()["code"] == "WORKSPACE_PATH_CONFLICT"


def test_workspace_mkdir_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/api/v1/projects/project-1/container/workspace/mkdir",
        json={"path": "docs"},
    )
    assert response.status_code == 401, response.text


@requires_postgres
def test_workspace_mkdir_route_is_project_scoped(
    client: TestClient, running_workspace: _MemoryWorkspace
) -> None:
    owner_h, project_id, company_id = _launch_project(client)
    body = {"path": "project-only"}
    project_route = f"/api/v1/projects/{project_id}/container/workspace/mkdir"
    created = client.post(project_route, headers=owner_h, json=body)
    assert created.status_code == 201, created.text

    company_route = f"/api/v1/companies/{company_id}/containers/{project_id}/workspace/mkdir"
    company_response = client.post(company_route, headers=owner_h, json=body)
    assert company_response.status_code == 404, company_response.text

    admin_route = f"/api/v1/admin/containers/{project_id}/workspace/mkdir"
    admin_headers = {"Authorization": f"Bearer {_token(sub='workspace-mkdir-scope-admin', platform_admin=True)}"}
    admin_response = client.post(admin_route, headers=admin_headers, json=body)
    assert admin_response.status_code == 404, admin_response.text
    assert running_workspace.directories == {"folder", "project-only"}


@requires_postgres
@pytest.mark.parametrize(("code", "status"), [("PROJECT_PAUSED", 409), ("POD_NOT_RUNNING", 409)])
def test_workspace_mkdir_rejects_paused_or_unavailable_pod(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    status: int,
) -> None:
    owner_h, project_id, _ = _launch_project(client)

    async def _deny(self, *, project_id, principal, employee, write=False):
        _ = self, project_id, principal, employee, write
        raise AppError(code=code, title="Conflict", status=status, detail="workspace not writable")

    monkeypatch.setattr(PodWorkspaceService, "_require_running_runtime", _deny)
    response = client.post(
        f"/api/v1/projects/{project_id}/container/workspace/mkdir",
        headers=owner_h,
        json={"path": "blocked"},
    )
    assert response.status_code == status, response.text
    assert response.json()["code"] == code


@requires_postgres
def test_workspace_upload_rejects_oversized_content(
    client: TestClient, running_workspace: _MemoryWorkspace
) -> None:
    owner_h, project_id, _ = _launch_project(client)
    endpoint = f"/api/v1/projects/{project_id}/container/workspace/content"
    original = running_workspace.files["notes.txt"]

    uploaded = client.put(
        endpoint,
        headers=owner_h,
        params={"path": "notes.txt"},
        content=b"x" * (10_485_760 + 1),
    )

    assert uploaded.status_code == 413, uploaded.text
    assert uploaded.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert running_workspace.files["notes.txt"] == original


@requires_postgres
def test_workspace_delete_missing_returns_stable_metadata(
    client: TestClient, running_workspace: _MemoryWorkspace
) -> None:
    owner_h, project_id, _ = _launch_project(client)
    endpoint = f"/api/v1/projects/{project_id}/container/workspace/entries"

    # Backslash separators are a client formatting artefact and are folded to "/";
    # a leading "/" is *not* (see test_workspace_user_paths_must_be_relative).
    deleted = client.delete(endpoint, headers=owner_h, params={"path": r"missing\\file.txt"})

    assert deleted.status_code == 404, deleted.text
    assert deleted.json()["code"] == "WORKSPACE_NOT_FOUND"
    assert deleted.json()["path"] == "missing/file.txt"


@requires_postgres
def test_workspace_user_paths_must_be_relative(
    client: TestClient, running_workspace: _MemoryWorkspace
) -> None:
    """Absolute-looking user paths are refused, not silently rebased.

    ``normalize_workspace_path`` treats a leading ``/`` on *user input* as an
    attempt to address a path outside /workspace and answers 422. Only the
    backend's own paths go through ``coerce_internal_workspace_path``. Earlier
    versions of the two tests above asserted the lenient reading (``/notes/./x``
    being folded into ``notes/x``), which is exactly the behaviour the contract
    forbids — the fail-closed refusal is the property worth pinning.
    """
    owner_h, project_id, _ = _launch_project(client)
    base = f"/api/v1/projects/{project_id}/container/workspace"

    for path in ("/notes/atomic.txt", "/missing/file.txt"):
        uploaded = client.put(
            f"{base}/content", headers=owner_h, params={"path": path}, content=b"x"
        )
        assert uploaded.status_code == 422, uploaded.text
        assert uploaded.json()["code"] == "VALIDATION_ERROR"

        deleted = client.delete(f"{base}/entries", headers=owner_h, params={"path": path})
        assert deleted.status_code == 422, deleted.text

    # Nothing leaked into the workspace behind the refusal.
    assert "notes/atomic.txt" not in running_workspace.files


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
