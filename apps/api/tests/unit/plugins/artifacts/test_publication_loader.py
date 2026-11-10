"""Offline HTTP OCI loader checks: digest, size caps and no redirects."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from prodavan.application.plugins.artifacts.publication import HttpxOciArtifactLoader
from prodavan.application.plugins.artifacts.publication import PublicationErrorCode
from prodavan.application.plugins.artifacts.publication import _LoaderError


def _factory(transport: httpx.MockTransport):
    def make() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        )

    return make


@pytest.mark.asyncio
async def test_http_loader_verifies_manifest_and_blob_digest_without_network() -> None:
    manifest = b'{"schemaVersion":2}'
    blob = b'{"plugin_id":"hello_plugin"}\n'
    manifest_digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    blob_digest = "sha256:" + hashlib.sha256(blob).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/manifests/" + manifest_digest):
            return httpx.Response(200, content=manifest)
        if request.url.path.endswith("/blobs/" + blob_digest):
            return httpx.Response(200, content=blob)
        return httpx.Response(404)

    loader = HttpxOciArtifactLoader(client_factory=_factory(httpx.MockTransport(handler)))
    ref = "ghcr.io/ne-tort/plugins/hello/bundle@" + manifest_digest
    loaded_manifest = await loader.load_manifest(ref)
    loaded_blob = await loader.load_blob(
        ref,
        digest=blob_digest,
        expected_size=len(blob),
    )
    assert loaded_manifest.data == manifest
    assert loaded_blob.data == blob


@pytest.mark.asyncio
async def test_http_loader_rejects_wrong_manifest_digest() -> None:
    expected = "sha256:" + "a" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-the-signed-manifest")

    loader = HttpxOciArtifactLoader(client_factory=_factory(httpx.MockTransport(handler)))
    with pytest.raises(_LoaderError) as caught:
        await loader.load_manifest("ghcr.io/ne-tort/plugins/hello/bundle@" + expected)
    assert caught.value.code is PublicationErrorCode.OCI_MANIFEST_MISMATCH


@pytest.mark.asyncio
async def test_http_loader_rejects_oversize_blob_before_reading() -> None:
    digest = "sha256:" + "b" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": "100"}, content=b"x" * 100)

    loader = HttpxOciArtifactLoader(
        max_blob_bytes=10,
        client_factory=_factory(httpx.MockTransport(handler)),
    )
    with pytest.raises(_LoaderError):
        await loader.load_blob(
            "ghcr.io/ne-tort/plugins/hello/bundle@" + ("sha256:" + "a" * 64),
            digest=digest,
        )


@pytest.mark.asyncio
async def test_http_loader_does_not_follow_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://internal.example/secret"})

    loader = HttpxOciArtifactLoader(client_factory=_factory(httpx.MockTransport(handler)))
    with pytest.raises(_LoaderError):
        await loader.load_manifest("ghcr.io/ne-tort/plugins/hello/bundle@" + ("sha256:" + "a" * 64))


def test_loader_construction_does_not_create_network_client(monkeypatch: pytest.MonkeyPatch) -> None:
    class ForbiddenClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("HTTP client must be lazy")

    monkeypatch.setattr(httpx, "AsyncClient", ForbiddenClient)
    loader = HttpxOciArtifactLoader()
    assert loader is not None
