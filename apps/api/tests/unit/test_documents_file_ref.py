"""Unit tests for module document-action FileRef normalization."""

from __future__ import annotations

import pytest

from prodavan.application.modules.documents_file_ref import normalize_documents_file_ref
from prodavan.domain.errors import AppError


def test_normalize_documents_file_ref_preserves_canonical_identity() -> None:
    ref = {
        "asset_id": "asset-canonical",
        "version_id": "version-canonical",
        "blob_version_id": "version-canonical",
        "storage_key": "blobs/untrusted-name",
        "filename": "source.xlsx",
        "size": 12,
        "sha256": "digest",
    }

    normalized = normalize_documents_file_ref(ref)

    assert normalized == ref
    assert normalized is not ref


def test_normalize_documents_file_ref_accepts_legacy_blob_version_id() -> None:
    ref = {
        "asset_id": "asset-1",
        "blob_version_id": "version-legacy",
        "storage_key": "blobs/legacy",
        "filename": "legacy.xlsx",
    }

    normalized = normalize_documents_file_ref(ref)

    assert normalized["asset_id"] == "asset-1"
    assert normalized["version_id"] == "version-legacy"
    assert normalized["blob_version_id"] == "version-legacy"
    assert "version_id" not in ref


@pytest.mark.parametrize(
    ("value", "expected_detail"),
    [
        ("blobs/key", "file_ref must be a FileRef object"),
        (
            {"storage_key": "blobs/key", "filename": "legacy.xlsx"},
            "missing canonical FileRef fields: asset_id, version_id",
        ),
        (
            {"asset_id": "asset-1", "storage_key": "blobs/key"},
            "missing canonical FileRef fields: version_id",
        ),
        (
            {"asset_id": "  ", "version_id": "version-1"},
            "missing canonical FileRef fields: asset_id",
        ),
        (
            {"asset_id": "asset-1", "version_id": 7, "blob_version_id": "version-1"},
            "version_id must be a string",
        ),
        (
            {
                "asset_id": "asset-1",
                "version_id": "version-1",
                "blob_version_id": "version-2",
            },
            "has conflicting version_id and blob_version_id",
        ),
    ],
)
def test_normalize_documents_file_ref_rejects_invalid_shapes(
    value: object,
    expected_detail: str,
) -> None:
    with pytest.raises(AppError) as error:
        normalize_documents_file_ref(value)

    assert error.value.status == 422
    assert expected_detail in (error.value.detail or "")