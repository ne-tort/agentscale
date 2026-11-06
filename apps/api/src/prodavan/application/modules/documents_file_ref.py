"""Normalization for FileRefs consumed by module document actions."""

from __future__ import annotations

from typing import Any

from prodavan.domain.errors import AppError


def normalize_documents_file_ref(
    value: Any,
    *,
    detail: str = "file_ref",
) -> dict[str, Any]:
    """Return a copy with canonical asset/version identity fields.

    Older Content Service references may use ``blob_version_id`` instead of
    ``version_id``. A storage key is only a blob locator and is never used to
    construct either identity field.
    """
    if not isinstance(value, dict):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"{detail} must be a FileRef object",
        )

    asset_id = value.get("asset_id")
    version_id = value.get("version_id")
    blob_version_id = value.get("blob_version_id")
    for name, field in (
        ("asset_id", asset_id),
        ("version_id", version_id),
        ("blob_version_id", blob_version_id),
    ):
        if field is not None and not isinstance(field, str):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"{detail}.{name} must be a string",
            )

    canonical_version = (
        version_id if isinstance(version_id, str) and version_id.strip() else None
    )
    legacy_version = (
        blob_version_id
        if isinstance(blob_version_id, str) and blob_version_id.strip()
        else None
    )

    if canonical_version and legacy_version and canonical_version != legacy_version:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"{detail} has conflicting version_id and blob_version_id",
        )

    effective_version = canonical_version or legacy_version
    missing = [
        name
        for name, field in (("asset_id", asset_id), ("version_id", effective_version))
        if not isinstance(field, str) or not field.strip()
    ]
    if missing:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"{detail} missing canonical FileRef fields: {', '.join(missing)}",
        )

    normalized = dict(value)
    normalized["version_id"] = effective_version
    return normalized