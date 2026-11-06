"""Acceptance contract for workspace path safety across API contours.

Executable again: the migration verification freeze was lifted, so this contract is
run, not just reviewed. Root markers (``""``, ``"/"``, ``"."``, ``"./"``, ``".\\"``)
are *not* normalizer errors — they denote ``/workspace`` itself and normalize to
``""``; the ``PodWorkspaceService`` guards reject that empty path with 422 for every
mutating/reading operation. Traversal and absolute paths are refused by
``normalize_workspace_path`` itself. Both halves are asserted below, and the service
half exercises the real guards against a port stub that fails the test if any
filesystem I/O is attempted for a rejected path.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
from prodavan.application.pod_service.workspace_service import PodWorkspaceService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal

# Contour -> module that serves that contour's workspace routes. All three delegate
# to the same PodWorkspaceService, which is why one path contract covers them.
_CONTOURS = {
    "employee": "prodavan.api.v1.projects",
    "admin": "prodavan.api.v1.admin_containers",
    "company": "prodavan.api.v1.company_containers",
}

# Operation -> the PodWorkspaceService method the contour routes delegate to.
_OPERATIONS = {
    "put": "write_content",
    "read": "read_content",
    "delete": "delete_entry",
    "move": "move_entry",
    "copy": "copy_entry",
}

# Paths addressing the workspace root itself. ``normalize_workspace_path`` maps them
# to ``""`` (see ``tests/unit/test_workspace_paths.py``); the empty value is then
# refused by the service guards — never silently treated as a valid operand.
_ROOT_MARKERS = ("", "/", "./", ".\\", ".")

# Traversal / absolute attempts: refused by the normalizer before any I/O.
_TRAVERSAL_PATHS = (
    "../secret.txt",
    r"..\secret.txt",
    "%2e%2e%2fsecret.txt",
    "%252e%252e%252fsecret.txt",
    "..%2Fsecret.txt",
    "/workspace/secret.txt",
    r"\workspace\secret.txt",
    "docs/../secret.txt",
    "docs/../../secret.txt",
    "assets/..",
)

_PRINCIPAL = Principal(sub="user:acceptance")


class _RejectingWorkspace:
    """Port stub: any filesystem call means a rejected path leaked past the guard."""

    def __getattr__(self, name: str):
        async def _fail(**kwargs):
            raise AssertionError(
                f"workspace port call {name!r} reached for rejected path: {kwargs!r}"
            )

        return _fail


@pytest.fixture
def guarded_service(monkeypatch: pytest.MonkeyPatch) -> PodWorkspaceService:
    """Service whose runtime lookup succeeds and whose port refuses all I/O."""

    async def _fake_runtime(self, **kwargs):  # noqa: ANN001, ANN202
        return "pod-test", ""

    monkeypatch.setattr(PodWorkspaceService, "_require_running_runtime", _fake_runtime)
    return PodWorkspaceService(None, workspace=_RejectingWorkspace())  # type: ignore[arg-type]


async def _call_operation(service: PodWorkspaceService, operation: str, path: str) -> dict:
    kwargs = {"project_id": "proj-test", "principal": _PRINCIPAL, "employee": None}
    if operation == "put":
        return await service.write_content(path=path, data=b"payload", **kwargs)
    if operation == "read":
        return await service.read_content(path=path, **kwargs)
    if operation == "delete":
        return await service.delete_entry(path=path, **kwargs)
    if operation in {"move", "copy"}:
        method = service.move_entry if operation == "move" else service.copy_entry
        return await method(src=path, dst="docs/ok.txt", **kwargs)
    raise AssertionError(f"unknown operation {operation!r}")


@pytest.mark.parametrize("contour", tuple(_CONTOURS))
@pytest.mark.parametrize("operation", tuple(_OPERATIONS))
def test_workspace_path_contract_covers_all_contours_and_operations(
    contour: str, operation: str
) -> None:
    """Every workspace route must apply the shared path contract.

    Each contour module routes the operation through ``PodWorkspaceService``; the
    shared normalizer is then the single place that refuses traversal, and Windows
    separators are canonicalised rather than treated as escapes.
    """
    module = importlib.import_module(_CONTOURS[contour])
    source = inspect.getsource(module)
    assert f"PodWorkspaceService(session).{_OPERATIONS[operation]}(" in source

    assert normalize_workspace_path(r"docs\nested\file.txt") == "docs/nested/file.txt"
    for raw in _TRAVERSAL_PATHS:
        with pytest.raises(AppError) as exc:
            normalize_workspace_path(raw)
        assert exc.value.status == 422


def test_workspace_path_contract_requires_non_root_move_and_copy_operands() -> None:
    """Root markers normalize to the empty path — never to a valid operand.

    The emptiness is what the service guards turn into a 422; the normalizer must
    not raise here because ``""`` is the legitimate root used by directory listing
    and by ``workspace_abs_path``.
    """
    for raw in _ROOT_MARKERS:
        assert normalize_workspace_path(raw) == ""


@pytest.mark.parametrize("operation", tuple(_OPERATIONS))
@pytest.mark.parametrize("marker", _ROOT_MARKERS)
async def test_service_rejects_root_markers_before_touching_the_workspace(
    guarded_service: PodWorkspaceService, operation: str, marker: str
) -> None:
    """An empty (root) path must be a 422 for put/read/delete/move/copy alike."""
    with pytest.raises(AppError) as exc:
        await _call_operation(guarded_service, operation, marker)
    assert exc.value.status == 422
    assert exc.value.code == "VALIDATION_ERROR"


@pytest.mark.parametrize("operation", ("move", "copy"))
@pytest.mark.parametrize("side", ("src", "dst"))
@pytest.mark.parametrize("bad", _ROOT_MARKERS + _TRAVERSAL_PATHS)
async def test_move_and_copy_reject_either_unsafe_operand(
    guarded_service: PodWorkspaceService, operation: str, side: str, bad: str
) -> None:
    """Both operands of move/copy are guarded: neither side may be root or traversal."""
    src, dst = ("docs/ok.txt", "docs/copy.txt")
    if side == "src":
        src = bad
    else:
        dst = bad
    method = guarded_service.move_entry if operation == "move" else guarded_service.copy_entry
    with pytest.raises(AppError) as exc:
        await method(
            project_id="proj-test",
            src=src,
            dst=dst,
            principal=_PRINCIPAL,
            employee=None,
        )
    assert exc.value.status == 422
    assert exc.value.code == "VALIDATION_ERROR"
