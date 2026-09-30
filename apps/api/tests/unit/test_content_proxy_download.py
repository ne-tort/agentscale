"""Proxy download mode for content assets (desktop clients outside the cluster).

Dev diagnosis (2026-09-30): presigned blob URLs point at in-cluster DNS
(``prodavan-minio:9000``) — unreachable from the user's desktop, so module
export downloads (budget xlsx / КП PDF) died on the 302 hop. ``proxy=1``
streams the bytes through the API instead.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prodavan.api.v1.content import _download_response


@pytest.mark.asyncio
async def test_download_response_redirects_when_presigned() -> None:
    target = SimpleNamespace(url="http://blob/presigned?sig=1", storage_key="blobs/k1")
    res = await _download_response(target)
    assert res.status_code == 302
    assert res.headers["location"].startswith("http://blob/presigned")


@pytest.mark.asyncio
async def test_download_response_proxy_streams_bytes(monkeypatch) -> None:
    target = SimpleNamespace(
        url="http://blob/presigned?sig=1",
        storage_key="blobs/k1",
        content_type="application/pdf",
    )
    import prodavan.api.v1.content as content_mod

    store = SimpleNamespace(get_bytes=AsyncMock(return_value=b"%PDF-1.7-bytes"))
    monkeypatch.setattr(content_mod, "ensure_file_store", lambda: store)
    res = await _download_response(target, proxy=True)
    assert res.status_code == 200
    assert res.body == b"%PDF-1.7-bytes"
    assert res.media_type == "application/pdf"
    store.get_bytes.assert_awaited_once_with("blobs/k1")


@pytest.mark.asyncio
async def test_download_response_local_store_streams(monkeypatch) -> None:
    target = SimpleNamespace(url=None, storage_key="blobs/k2", content_type=None)
    import prodavan.api.v1.content as content_mod

    store = SimpleNamespace(get_bytes=AsyncMock(return_value=b"xlsx-bytes"))
    monkeypatch.setattr(content_mod, "ensure_file_store", lambda: store)
    res = await _download_response(target)
    assert res.status_code == 200
    assert res.body == b"xlsx-bytes"
    assert res.media_type == "application/octet-stream"
