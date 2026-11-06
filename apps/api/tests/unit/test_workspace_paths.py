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
    for raw in (
        "../etc/passwd",
        "assets/..",
        "./../etc/passwd",
        r"..\etc\passwd",
        "%2e%2e%2fetc%2fpasswd",
        "%252e%252e%252fetc%252fpasswd",
        "/workspace/etc/passwd",
        r"\workspace\etc\passwd",
    ):
        with pytest.raises(AppError) as exc:
            normalize_workspace_path(raw)
        assert exc.value.status == 422


def test_normalize_windows_separators_without_escape() -> None:
    assert normalize_workspace_path(r"assets\nested\hello.txt") == "assets/nested/hello.txt"


def test_normalize_dot_path_to_workspace_root() -> None:
    assert normalize_workspace_path(".") == ""
    assert normalize_workspace_path("./") == ""
