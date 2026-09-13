"""Unit tests — module-scoped content upload service."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from prodavan.application.content.module_upload_service import ModuleContentUploadService
from prodavan.domain.identity import Principal


def _admin_principal() -> Principal:
    return Principal(sub="admin-1", roles=frozenset({"platform.admin"}))


@pytest.mark.asyncio
async def test_upload_for_platform_module_returns_file_ref() -> None:
    session = AsyncMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(storage_key="platform/blobs/abc")
    )
    svc = ModuleContentUploadService(session)
    data = b"zip-bytes"

    with (
        patch(
            "prodavan.application.content.module_upload_service.ModuleService"
        ) as mod_cls,
        patch.object(
            svc._upload,
            "upload_bytes_as_asset",
            new=AsyncMock(return_value=("asset_1", "ver_1")),
        ) as upload,
    ):
        mod_cls.return_value.get_admin = AsyncMock(return_value={"id": "mod_equipment"})
        out = await svc.upload_for_platform_module(
            module_id="mod_equipment",
            data=data,
            filename="pack.zip",
            mime="application/zip",
            principal=_admin_principal(),
        )

    upload.assert_awaited_once()
    kwargs = upload.await_args.kwargs
    assert kwargs["owner_company_id"] is None
    assert kwargs["data"] == data
    assert out["asset_id"] == "asset_1"
    assert out["version_id"] == "ver_1"
    assert out["filename"] == "pack.zip"
    assert out["storage_key"] == "platform/blobs/abc"
    assert out["size"] == len(data)
    assert len(out["sha256"]) == 64


@pytest.mark.asyncio
async def test_upload_for_company_module_uses_company_owner() -> None:
    session = AsyncMock()
    session.get = AsyncMock(return_value=SimpleNamespace(storage_key="co/blobs/x"))
    svc = ModuleContentUploadService(session)

    with (
        patch(
            "prodavan.application.content.module_upload_service.CompanyModuleService"
        ) as co_cls,
        patch.object(
            svc._upload,
            "upload_bytes_as_asset",
            new=AsyncMock(return_value=("a2", "v2")),
        ) as upload,
    ):
        co_cls.return_value.get_for_company = AsyncMock(return_value={"id": "mod_x"})
        out = await svc.upload_for_company_module(
            company_id="co_1",
            module_id="mod_x",
            data=b"hi",
            filename="a.txt",
            mime="text/plain",
            principal=_admin_principal(),
            employee=None,
        )

    assert upload.await_args.kwargs["owner_company_id"] == "co_1"
    assert out["asset_id"] == "a2"
    assert out["filename"] == "a.txt"
