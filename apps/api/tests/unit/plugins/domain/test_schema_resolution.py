"""Регрессии безопасного разрешения plugin-manifest JSON Schema.

Тесты НЕ запускаются в migration verification freeze.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodavan.domain.plugins import validation
from prodavan.domain.plugins.validation import (
    ManifestValidationErrorCode,
    PluginValidationError,
)


@pytest.fixture(autouse=True)
def _clear_schema_file_cache():
    validation._load_schema_file.cache_clear()
    yield
    validation._load_schema_file.cache_clear()


def _write_schema(path: Path, marker: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "x-test-marker": marker,
            }
        ),
        encoding="utf-8",
    )
    return path


def _disable_container_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(validation, "CONTAINER_SOURCE_ROOT", tmp_path / "absent" / "app" / "src")
    monkeypatch.setattr(
        validation,
        "CONTAINER_SCHEMA_PATH",
        tmp_path / "absent" / "packages" / "schemas" / "plugin-manifest" / "v1" / "schema.json",
    )


def test_repo_layout_is_found_by_structure_not_parent_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    module_file = repo / "apps" / "api" / "src" / "prodavan" / "domain" / "plugins" / "validation.py"
    module_file.parent.mkdir(parents=True)
    _write_schema(repo / validation.SCHEMA_RELATIVE_PATH, "repo")
    monkeypatch.setattr(validation, "__file__", str(module_file))
    monkeypatch.delenv(validation.SCHEMA_ROOT_ENV, raising=False)
    _disable_container_fallback(monkeypatch, tmp_path)

    assert validation.load_schema()["x-test-marker"] == "repo"


def test_app_src_container_layout_uses_packages_schemas(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_root = tmp_path / "app" / "src"
    module_file = source_root / "prodavan" / "domain" / "plugins" / "validation.py"
    module_file.parent.mkdir(parents=True)
    schema_path = _write_schema(
        tmp_path / "packages" / "schemas" / "plugin-manifest" / "v1" / "schema.json",
        "container",
    )
    monkeypatch.setattr(validation, "__file__", str(module_file))
    monkeypatch.setattr(validation, "CONTAINER_SOURCE_ROOT", source_root)
    monkeypatch.setattr(validation, "CONTAINER_SCHEMA_PATH", schema_path)
    monkeypatch.delenv(validation.SCHEMA_ROOT_ENV, raising=False)

    assert validation.load_schema()["x-test-marker"] == "container"


@pytest.mark.parametrize("root_kind", ["repo", "schemas"])
def test_explicit_schema_root_supports_repo_and_schemas_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root_kind: str
) -> None:
    if root_kind == "repo":
        configured_root = tmp_path / "configured-repo"
        schema_path = configured_root / validation.SCHEMA_RELATIVE_PATH
    else:
        configured_root = tmp_path / "packages" / "schemas"
        schema_path = configured_root / "plugin-manifest" / "v1" / "schema.json"
    _write_schema(schema_path, f"explicit-{root_kind}")
    monkeypatch.setenv(validation.SCHEMA_ROOT_ENV, str(configured_root))
    monkeypatch.setattr(validation, "__file__", str(tmp_path / "unrelated" / "validation.py"))
    _disable_container_fallback(monkeypatch, tmp_path)

    assert validation.load_schema()["x-test-marker"] == f"explicit-{root_kind}"


def test_invalid_explicit_schema_root_does_not_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    module_file = repo / "apps" / "api" / "src" / "prodavan" / "domain" / "plugins" / "validation.py"
    module_file.parent.mkdir(parents=True)
    _write_schema(repo / validation.SCHEMA_RELATIVE_PATH, "must-not-be-used")
    monkeypatch.setattr(validation, "__file__", str(module_file))
    monkeypatch.setenv(validation.SCHEMA_ROOT_ENV, str(tmp_path / "missing"))
    _disable_container_fallback(monkeypatch, tmp_path)

    with pytest.raises(PluginValidationError) as exc_info:
        validation.load_schema()
    assert exc_info.value.code is ManifestValidationErrorCode.SCHEMA_UNAVAILABLE
    assert validation.SCHEMA_ROOT_ENV in exc_info.value.message


def test_unknown_layout_fails_closed_without_cwd_or_index_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(validation, "__file__", str(tmp_path / "isolated" / "validation.py"))
    monkeypatch.delenv(validation.SCHEMA_ROOT_ENV, raising=False)
    _disable_container_fallback(monkeypatch, tmp_path)
    cwd = tmp_path / "cwd"
    _write_schema(cwd / validation.SCHEMA_RELATIVE_PATH, "unexpected-cwd")
    monkeypatch.chdir(cwd)

    with pytest.raises(PluginValidationError) as exc_info:
        validation.load_schema()
    assert exc_info.value.code is ManifestValidationErrorCode.SCHEMA_UNAVAILABLE


def test_relative_explicit_schema_root_is_rejected_without_cwd_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cwd = tmp_path / "cwd"
    _write_schema(cwd / "relative-root" / validation.SCHEMA_RELATIVE_PATH, "unexpected-cwd")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv(validation.SCHEMA_ROOT_ENV, "relative-root")
    monkeypatch.setattr(validation, "__file__", str(tmp_path / "isolated" / "validation.py"))
    _disable_container_fallback(monkeypatch, tmp_path)

    with pytest.raises(PluginValidationError) as exc_info:
        validation.load_schema()
    assert exc_info.value.code is ManifestValidationErrorCode.SCHEMA_UNAVAILABLE
    assert "absolute path" in exc_info.value.message


def test_explicit_paths_are_cached_by_resolved_path(tmp_path: Path) -> None:
    first = _write_schema(tmp_path / "first.json", "first")
    second = _write_schema(tmp_path / "second.json", "second")

    assert validation.load_schema(first)["x-test-marker"] == "first"
    assert validation.load_schema(second)["x-test-marker"] == "second"
