"""Bundle codec unit tests (L06) — no Postgres."""

from __future__ import annotations

import pytest

from prodavan.domain.errors import AppError
from prodavan.infrastructure.cabinets.bundle_codec import pack_bundle, unpack_bundle


def test_pack_unpack_roundtrip() -> None:
    raw = pack_bundle(
        name="Demo",
        exported_from_cabinet_id="cab_abc123def4567890",
        tables=[{"id": "tbl_1", "slug": "suppliers", "label": "Suppliers", "storage_kind": "physical"}],
        columns=[
            {
                "id": "col_1",
                "table_id": "tbl_1",
                "table_slug": "suppliers",
                "name": "name",
                "col_type": "text",
                "required": True,
                "unique_col": False,
            }
        ],
        tabs=[{"id": "tab_1", "title": "Suppliers", "order": 100, "system": False}],
        views=[{"id": "view_1", "slug": "suppliers", "ui_json": {"version": 1, "kind": "collection"}}],
        data_by_slug={"suppliers": [{"id": "row_1", "name": "DNS"}]},
    )
    parsed = unpack_bundle(raw)
    assert parsed["manifest"]["format"] == "cabinet.bundle"
    assert parsed["manifest"]["format_version"] == 1
    assert parsed["manifest"]["content_hash"].startswith("sha256:")
    assert parsed["tables"][0]["slug"] == "suppliers"
    assert parsed["data_by_slug"]["suppliers"][0]["name"] == "DNS"


def test_rejects_path_traversal() -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            '{"format":"cabinet.bundle","format_version":1,"name":"x","content_hash":"sha256:0"}',
        )
        zf.writestr("../evil.txt", "nope")
    with pytest.raises(AppError) as ei:
        unpack_bundle(buf.getvalue())
    assert ei.value.code == "BUNDLE_INVALID"


def test_rejects_loose_binary_in_packages() -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            '{"format":"cabinet.bundle","format_version":1,"name":"x","content_hash":"sha256:0"}',
        )
        zf.writestr("mcp_packages/tool.bin", "raw")
    with pytest.raises(AppError) as ei:
        unpack_bundle(buf.getvalue())
    assert ei.value.code == "BUNDLE_INVALID"
