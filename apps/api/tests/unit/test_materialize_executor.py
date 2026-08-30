"""Unit tests for MaterializeExecutor format handlers."""

from __future__ import annotations

import json

from prodavan.application.projects.materialize_executor import (
    MaterializeExecutor,
    _render_template,
    _should_skip_copy_blob,
)
from prodavan.application.projects.materialize_planner import MaterializeOp


class _MemoryWriter:
    def __init__(self) -> None:
        self.text_files: dict[str, str] = {}
        self.byte_files: dict[str, bytes] = {}

    def write_text_file(self, *, relative_path: str, text: str) -> None:
        self.text_files[relative_path] = text

    def write_bytes_file(self, *, relative_path: str, data: bytes) -> None:
        self.byte_files[relative_path] = data

    def read_bytes_file(self, relative_path: str) -> bytes | None:
        return self.byte_files.get(relative_path)


def test_write_json_rows() -> None:
    writer = _MemoryWriter()
    executor = MaterializeExecutor(session=None)  # type: ignore[arg-type]
    op = MaterializeOp(
        rule_id="r1",
        module_id="mod_1",
        workspace_path="cabinet-seed/suppliers.json",
        format="json_rows",
        source_type="rows",
        rows_bodies=[{"row_id": "row_1", "name": "ACME", "status": "active"}],
    )
    path = executor._write_json_rows(writer, op)
    assert path == "cabinet-seed/suppliers.json"
    data = json.loads(writer.text_files[path])
    assert data == [{"name": "ACME", "status": "active"}]


def test_write_template() -> None:
    writer = _MemoryWriter()
    executor = MaterializeExecutor(session=None)  # type: ignore[arg-type]
    op = MaterializeOp(
        rule_id="r2",
        module_id="mod_1",
        workspace_path="prompts/hello.md",
        format="template",
        source_type="row",
        row_body={"name": "World"},
        template_text="# Hello {{name}}",
    )
    path = executor._write_template(writer, op)
    assert writer.text_files[path] == "# Hello World"


def test_render_template() -> None:
    assert _render_template("x={{a}} y={{missing}}", {"a": 1}) == "x=1 y="


def test_should_skip_copy_blob_when_sha256_matches() -> None:
    writer = _MemoryWriter()
    data = b"same-content"
    import hashlib

    digest = hashlib.sha256(data).hexdigest()
    writer.write_bytes_file(relative_path="seed/a.pdf", data=data)
    assert _should_skip_copy_blob(writer, "seed/a.pdf", {"sha256": digest})
    assert not _should_skip_copy_blob(writer, "seed/a.pdf", {"sha256": "deadbeef"})
    assert not _should_skip_copy_blob(writer, "seed/missing.pdf", {"sha256": digest})
