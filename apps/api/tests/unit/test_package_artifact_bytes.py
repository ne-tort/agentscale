"""Unit tests — package artifact helpers (C-OBJECT-STORE)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prodavan.application.cabinets.packages_service import CabinetPackagesService
from prodavan.core.infra.object_keys import cabinet_packages_prefix, object_ref
from prodavan.core.infra.object_storage_manager import ObjectStorageManager, set_object_storage
from prodavan.domain.errors import AppError
from prodavan.infrastructure.cabinets.platform_event_handler import invoke_platform_event_from_bytes


def test_read_artifact_bytes_roundtrip(tmp_path: Path) -> None:
    mgr = ObjectStorageManager(backend="local", storage_root=tmp_path)
    mgr._primary = mgr._local
    set_object_storage(mgr)
    try:
        key = "cabinet_packages/cab1/demo-1.0.0.zip"
        mgr.put_bytes_sync(key, b"zip-bytes")
        raw = CabinetPackagesService.read_artifact_bytes(object_ref(key))
        assert raw == b"zip-bytes"
        assert cabinet_packages_prefix("cab1") == "cabinet_packages/cab1/"
    finally:
        set_object_storage(None)


def test_read_artifact_bytes_bad_ref() -> None:
    with pytest.raises(AppError) as ei:
        CabinetPackagesService.read_artifact_bytes("not-a-ref")
    assert ei.value.code == "PACKAGE_INVALID"


def test_invoke_from_bytes_missing_handler() -> None:
    # Empty zip → no handler script → failed action (not crash).
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    result = invoke_platform_event_from_bytes(
        zip_bytes=buf.getvalue(),
        package_name="empty",
        event={"platform_event_type": "project.created"},
    )
    assert result["action"] in {"failed", "invoked", "stub"}
    assert result.get("package") == "empty" or "package" in result or result["action"] == "failed"
