"""Unit tests — workspace path normalization."""

from __future__ import annotations

import pytest

from prodavan.application.pod_service.workspace_paths import normalize_workspace_path, workspace_abs_path
from prodavan.domain.errors import AppError


def test_normalize_empty_path() -> None:
    assert normalize_workspace_path("") == ""
    assert normalize_workspace_path("/") == ""
    assert workspace_abs_path("") == "/workspace"


def test_normalize_nested_path() -> None:
    assert normalize_workspace_path("assets/hello.txt") == "assets/hello.txt"
    assert workspace_abs_path("assets/hello.txt") == "/workspace/assets/hello.txt"


def test_reject_parent_segments() -> None:
    with pytest.raises(AppError) as exc:
        normalize_workspace_path("../etc/passwd")
    assert exc.value.status == 422
