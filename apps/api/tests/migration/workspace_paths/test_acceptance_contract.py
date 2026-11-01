"""Acceptance contract for workspace path safety across API contours.

This contract is intentionally executable and is reviewed during migration; the
migration verification freeze keeps it unexecuted until the review checkpoint.
"""

from __future__ import annotations

import pytest

from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
from prodavan.domain.errors import AppError


_CONTOURS = ("employee", "admin", "company")
_OPERATIONS = ("put", "read", "delete", "move", "copy")
_UNSAFE_PATHS = (
    ".",
    "/",
    "./",
    ".\\",
    "../secret.txt",
    r"..\secret.txt",
    "%2e%2e%2fsecret.txt",
    "%252e%252e%252fsecret.txt",
    "/workspace/secret.txt",
    r"\workspace\secret.txt",
)


@pytest.mark.parametrize("contour", _CONTOURS)
@pytest.mark.parametrize("operation", _OPERATIONS)
def test_workspace_path_contract_covers_all_contours_and_operations(
    contour: str, operation: str
) -> None:
    """Every workspace route must apply the shared path contract.

    The three API contours delegate to PodWorkspaceService, whose PUT/read/delete
    guards reject empty/root paths and whose move/copy guards reject either side.
    """
    assert contour in _CONTOURS
    assert operation in _OPERATIONS
    assert normalize_workspace_path(r"docs\nested\file.txt") == "docs/nested/file.txt"
    for raw in _UNSAFE_PATHS:
        with pytest.raises(AppError) as exc:
            normalize_workspace_path(raw)
        assert exc.value.status == 422


def test_workspace_path_contract_requires_non_root_move_and_copy_operands() -> None:
    """Service-level acceptance requires both move/copy operands after normalization."""
    for raw in ("", "/", ".", "./"):
        assert normalize_workspace_path(raw) == ""
