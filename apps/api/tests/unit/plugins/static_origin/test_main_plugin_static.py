"""Offline app-level checks for main_plugin_static (no network, DI loader)."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from prodavan.main_plugin_static import (
    STATIC_EVIDENCE_READY_PATH,
    create_app,
)
from tests.unit.plugins.static_origin.test_static_evidence_service import (
    ENTRY,
    PLUGIN_ID,
    RELEASE_PREFIX,
    VERSION,
    FakeLoader,
    _build_bundle,
    _register,
)

SHELL_ORIGIN = "https://shell.agentscale.example"
QUARANTINE_PREFIX = "ghcr.io/ne-tort/plugins-quarantine"


def _settings(*, shell_origin: str | None = SHELL_ORIGIN) -> SimpleNamespace:
    return SimpleNamespace(
        app_version="test",
        plugin_oci_registry=RELEASE_PREFIX,
        plugin_shell_origin=shell_origin,
    )


def _wired_app(*, shell_origin: str | None = SHELL_ORIGIN):
    bundle = _build_bundle()
    loader = FakeLoader()
    _register(bundle, loader, source="quarantine")
    app = create_app(_settings(shell_origin=shell_origin), artifact_loader=loader)
    return app, loader, bundle


def test_ready_path_contract() -> None:
    assert (
        STATIC_EVIDENCE_READY_PATH
        == "/internal/plugin-static/v1/bundles"
        "/{plugin_id}/{version}/{bundle_digest}/ready"
    )


def test_health_ready_when_configured() -> None:
    app, _loader, _bundle = _wired_app()
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}


def test_get_metadata_is_minimal_json() -> None:
    app, _loader, bundle = _wired_app()
    path = STATIC_EVIDENCE_READY_PATH.format(
        plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest,
    )
    with TestClient(app) as client:
        response = client.get(path)
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "status": "ready",
            "plugin_id": PLUGIN_ID,
            "version": VERSION,
            "bundle_digest": bundle.bundle_digest,
            "frontend_entry": ENTRY,
            "source": "quarantine",
        }
        # No blobs, no secrets, no registry URLs in the metadata body.
        serialized = response.text
        assert "ghcr.io" not in serialized
        assert "Authorization" not in serialized


def test_head_is_empty_with_static_policy_headers() -> None:
    app, _loader, bundle = _wired_app()
    path = STATIC_EVIDENCE_READY_PATH.format(
        plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest,
    )
    with TestClient(app) as client:
        response = client.head(path)
        assert response.status_code == 200
        assert response.content == b""
        assert response.headers["X-AgentScale-Bundle-Digest"] == bundle.bundle_digest
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert SHELL_ORIGIN in response.headers["Content-Security-Policy"]


def test_other_methods_answer_405() -> None:
    app, _loader, bundle = _wired_app()
    path = STATIC_EVIDENCE_READY_PATH.format(
        plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest,
    )
    with TestClient(app) as client:
        assert client.post(path).status_code == 405
        assert client.delete(path).status_code == 405


def test_invalid_identity_and_unverified_bundle_are_typed_404() -> None:
    app, loader, bundle = _wired_app()
    # The registry holds different bytes under the probed digest: a verified
    # negative (404), and the negative cache stops repeated registry reads.
    unverified_digest = "sha256:" + hashlib.sha256(b"unverified").hexdigest()
    loader.manifests[
        f"{RELEASE_PREFIX}/{PLUGIN_ID}/bundle@{unverified_digest}"
    ] = bundle.manifest_bytes
    with TestClient(app) as client:
        bad_id = STATIC_EVIDENCE_READY_PATH.format(
            plugin_id="Demo", version=VERSION, bundle_digest="sha256:" + "a" * 64,
        )
        response = client.get(bad_id)
        assert response.status_code == 404
        assert response.json() == {"code": "BUNDLE_NOT_READY"}

        unverified = STATIC_EVIDENCE_READY_PATH.format(
            plugin_id=PLUGIN_ID,
            version=VERSION,
            bundle_digest=unverified_digest,
        )
        assert client.get(unverified).status_code == 404
        assert client.get(unverified).json() == {"code": "BUNDLE_NOT_READY"}
        # Negative cache: the registry is not asked again for the same key.
        assert len(loader.manifest_calls) == 2


def test_unconfigured_shell_origin_answers_fail_closed() -> None:
    app, _loader, bundle = _wired_app(shell_origin="")
    path = STATIC_EVIDENCE_READY_PATH.format(
        plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest,
    )
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 503
        response = client.get(path)
        assert response.status_code == 503
        assert response.json() == {"code": "EVIDENCE_UNAVAILABLE"}


def test_composition_failure_never_crashes_create_app() -> None:
    # Empty registry setting fails composition, but liveness stays available.
    settings = SimpleNamespace(
        app_version="test",
        plugin_oci_registry="",
        plugin_shell_origin=SHELL_ORIGIN,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503


@pytest.mark.asyncio
async def test_proven404_adapter_makes_real_loader_fall_back_offline() -> None:
    """Real loader + proven-404 adapter: 404-only fallback, outage stays 503."""
    import httpx
    from prodavan.application.plugins.artifacts.publication import (
        HttpxOciArtifactLoader,
    )
    from prodavan.application.plugins.static_origin import (
        PluginStaticEvidenceService,
        StaticEvidenceConfig,
        StaticEvidenceError,
        StaticEvidenceErrorCode,
    )
    from prodavan.main_plugin_static import (
        NotFoundAwareOciLoader,
        _proven404_client_factory,
    )

    bundle = _build_bundle()
    release_repo = f"ne-tort/plugins/{PLUGIN_ID}/bundle"
    absent_digest = "sha256:" + "a" * 64
    outage_digest = "sha256:" + "f" * 64
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path)
        if "/manifests/" in path:
            digest = path.rsplit("/", 1)[1]
            if digest == bundle.bundle_digest:
                if path.startswith(f"/v2/{release_repo}"):
                    return httpx.Response(200, content=bundle.manifest_bytes)
                return httpx.Response(404)
            if digest == absent_digest:
                return httpx.Response(404)
            return httpx.Response(500)
        if "/blobs/" in path:
            data = bundle.blobs.get(path.rsplit("/", 1)[1])
            if data is not None:
                return httpx.Response(200, content=data)
            return httpx.Response(404)
        return httpx.Response(404)

    def factory() -> httpx.AsyncClient:
        client = _proven404_client_factory()
        client._transport = httpx.MockTransport(handler)  # noqa: SLF001
        return client

    loader = NotFoundAwareOciLoader(
        HttpxOciArtifactLoader(
            allowed_registries=("ghcr.io",),
            max_manifest_bytes=4 * 1024 * 1024,
            max_blob_bytes=64 * 1024 * 1024,
            client_factory=factory,
        ),
    )
    service = PluginStaticEvidenceService(
        artifact_loader=loader,
        config=StaticEvidenceConfig(
            release_repository_prefix=RELEASE_PREFIX,
            quarantine_repository_prefix=QUARANTINE_PREFIX,
        ),
    )

    evidence = await service.verify_bundle(
        plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=bundle.bundle_digest,
    )
    assert evidence.source == "release"

    # Proven 404 in BOTH contours is a typed 404, not an outage.
    with pytest.raises(StaticEvidenceError) as caught:
        await service.verify_bundle(
            plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=absent_digest,
        )
    assert caught.value.code is StaticEvidenceErrorCode.BUNDLE_NOT_READY

    # A 500 is never a not-found: typed 503 and NO release retry.
    requests.clear()
    with pytest.raises(StaticEvidenceError) as caught:
        await service.verify_bundle(
            plugin_id=PLUGIN_ID, version=VERSION, bundle_digest=outage_digest,
        )
    assert caught.value.code is StaticEvidenceErrorCode.EVIDENCE_UNAVAILABLE
    assert not any(
        path.startswith(f"/v2/{release_repo}") for path in requests
    )
