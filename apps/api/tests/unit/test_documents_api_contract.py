"""Acceptance checks for the pod-facing Documents FileRef boundary.

These tests are intentionally kept alongside the unit suite and are not run
as part of the migration freeze. They protect the tenant/blob authorization
contract before the document service reads a source file.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.api.v1.documents import FileRefBody, _source_ref
from prodavan.application.pod_identity.bridge import PodBridgeClaims
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.content import (
    ContentAssetRow,
    ContentBlobVersionRow,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


def _bridge() -> PodBridgeClaims:
    return PodBridgeClaims(
        project_id="pr1",
        cabinet_id="cab1",
        company_id="co1",
        pod_id="pod1",
        gen=1,
        scopes=("documents",),
        jti="jti1",
        exp=2_000_000_000,
        acting_employee_id="emp1",
    )


@pytest.mark.asyncio
async def test_source_ref_uses_db_blob_not_client_storage_key() -> None:
    version = MagicMock(spec=ContentBlobVersionRow)
    version.id = "ver1"
    version.asset_id = "asset1"
    version.storage_key = "blobs/canonical"
    version.size = 7
    version.sha256 = "canonical-sha"
    asset = MagicMock(spec=ContentAssetRow)
    asset.id = "asset1"
    asset.owner_company_id = "co1"
    asset.title = "source.xlsx"
    employee = MagicMock(spec=EmployeeRow)
    employee.id = "emp1"

    session = MagicMock()
    session.get = AsyncMock(side_effect=[version, asset])
    with patch(
        "prodavan.api.v1.documents.AccessPolicyService.require_read_asset",
        new=AsyncMock(),
    ):
        ref, filename = await _source_ref(
            FileRefBody(
                asset_id="asset1",
                version_id="ver1",
                storage_key="blobs/attacker-controlled",
                filename="source.xlsx",
            ),
            None,
            bridge=_bridge(),
            session=session,
            employee=employee,
        )

    assert filename == "source.xlsx"
    assert ref["storage_key"] == "blobs/canonical"
    assert ref["sha256"] == "canonical-sha"


@pytest.mark.asyncio
async def test_source_ref_rejects_asset_from_another_company() -> None:
    version = MagicMock(spec=ContentBlobVersionRow)
    version.id = "ver1"
    version.asset_id = "asset1"
    asset = MagicMock(spec=ContentAssetRow)
    asset.id = "asset1"
    asset.owner_company_id = "other-company"
    employee = MagicMock(spec=EmployeeRow)
    employee.id = "emp1"

    session = MagicMock()
    session.get = AsyncMock(side_effect=[version, asset])

    with pytest.raises(AppError) as error:
        await _source_ref(
            FileRefBody(asset_id="asset1", version_id="ver1", filename="source.xlsx"),
            None,
            bridge=_bridge(),
            session=session,
            employee=employee,
        )

    assert error.value.status == 404
