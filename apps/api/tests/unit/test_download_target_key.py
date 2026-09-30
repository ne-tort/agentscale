"""Regression: download target keeps storage_key on the S3 backend.

The proxy download mode (``?proxy=1``, desktop clients outside the cluster)
and inline reads fetch bytes by key; the presigned url keeps serving the 302
flow. Dropping storage_key when presigning made proxy mode 500.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.content.download_service import DownloadService


@pytest.mark.asyncio
async def test_target_keeps_storage_key_when_presigned(monkeypatch) -> None:
    svc = DownloadService(MagicMock())
    asset = MagicMock(id="cast_1", mime="application/pdf")
    asset.__class__.__name__ = "ContentAssetRow"
    ver = MagicMock(storage_key="blobs/k1")

    async def _get(cls, asset_id):
        return asset

    svc._session.get = _get
    svc._acl = MagicMock(require_read_asset=AsyncMock())
    svc._assets = MagicMock(resolve_blob_version=AsyncMock(return_value=ver))

    import prodavan.application.content.download_service as ds_mod

    store = MagicMock(presign_get=AsyncMock(return_value="http://blob/x?sig=1"))
    monkeypatch.setattr(ds_mod, "ensure_file_store", lambda: store)

    target = await svc.presign_asset(
        asset_id="cast_1",
        principal=MagicMock(),
        employee=None,
    )
    assert target.url == "http://blob/x?sig=1"
    assert target.storage_key == "blobs/k1"
    assert target.content_type == "application/pdf"
