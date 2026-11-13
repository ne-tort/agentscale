"""Offline ASGI tests for the cluster-internal static evidence endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from prodavan.application.plugins.gateway.ports import StaticResponsePolicy
from prodavan.main_plugin_static import (
    STATIC_EVIDENCE_READY_PATH,
    create_app,
)
from tests.unit.plugins.static_origin.test_service import (
    INDEX_HTML,
    PLUGIN_ID,
    QUARANTINE_REPOSITORY,
    RELEASE_PREFIX,
    SHELL_ORIGIN,
    VERSION,
    ExactByteLoader,
    FakeLoaderFailure,
    make_bundle,
    make_service,
    sha256,
    wrong_oci_media_type,
)


def app_settings(*, configured: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        app_version="offline-test",
        plugin_shell_origin=SHELL_ORIGIN,
        plugin_oci_registry=RELEASE_PREFIX if configured else "",
    )


def ready_path(digest: str) -> str:
    return STATIC_EVIDENCE_READY_PATH.format(
        plugin_id=PLUGIN_ID,
        version=VERSION,
        bundle_digest=digest,
    )


def policy_headers(digest: str) -> dict[str, str]:
    return StaticResponsePolicy(shell_origin=SHELL_ORIGIN).response_headers(
        artifact_digest=digest
    )


def assert_exact_security_headers(response: httpx.Response, digest: str) -> None:
    for name, expected in policy_headers(digest).items():
        assert response.headers[name] == expected
    assert response.headers["X-AgentScale-Bundle-Digest"] == digest
    assert "Set-Cookie" not in response.headers
    assert "Access-Control-Allow-Origin" not in response.headers


def app_with_loader(
    loader: ExactByteLoader,
    **config_overrides: Any,
) -> Any:
    return create_app(
        app_settings=app_settings(),
        service=make_service(loader, **config_overrides),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ("GET", "HEAD"))
async def test_valid_quarantine_get_head_verify_actual_bytes_and_exact_headers(
    method: str,
) -> None:
    bundle = make_bundle()
    loader = ExactByteLoader()
    loader.add(bundle)
    app = app_with_loader(loader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://plugin-static"
    ) as client:
        response = await client.request(method, ready_path(bundle.digest))

    assert response.status_code == 200
    assert_exact_security_headers(response, bundle.digest)
    assert loader.calls == [
        ("manifest", bundle.ref(QUARANTINE_REPOSITORY), None),
        ("blob", bundle.ref(QUARANTINE_REPOSITORY), bundle.config_digest),
        ("blob", bundle.ref(QUARANTINE_REPOSITORY), bundle.frontend_digest),
    ]
    assert loader.blobs[QUARANTINE_REPOSITORY][bundle.frontend_digest] == bundle.frontend
    assert bundle.frontend != INDEX_HTML  # compressed bytes are genuinely fetched
    if method == "HEAD":
        assert response.content == b""
    else:
        assert response.headers["Content-Type"] == "application/json"
        assert response.json() == {
            "status": "ready",
            "plugin_id": PLUGIN_ID,
            "version": VERSION,
            "bundle_digest": bundle.digest,
            "frontend_entry": "index.html",
            "source": "quarantine",
        }
        assert str(len(bundle.frontend)) not in response.text
        assert bundle.config.decode("utf-8") not in response.text


@pytest.mark.asyncio
async def test_create_app_artifact_loader_override_uses_the_same_verification_path() -> None:
    bundle = make_bundle()
    loader = ExactByteLoader()
    loader.add(bundle)
    app = create_app(app_settings=app_settings(), artifact_loader=loader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://plugin-static"
    ) as client:
        response = await client.get(ready_path(bundle.digest))

    assert response.status_code == 200
    assert response.json()["bundle_digest"] == bundle.digest
    assert loader.count("blob") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"))
async def test_write_and_other_methods_are_405_without_loader_access(method: str) -> None:
    bundle = make_bundle()
    loader = ExactByteLoader()
    loader.add(bundle)
    app = app_with_loader(loader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://plugin-static"
    ) as client:
        response = await client.request(method, ready_path(bundle.digest))

    assert response.status_code == 405
    assert response.json() == {"detail": "Method Not Allowed"}
    allow = {item.strip() for item in response.headers["Allow"].split(",")}
    assert allow == {"GET", "HEAD"}
    assert "X-AgentScale-Bundle-Digest" not in response.headers
    assert loader.calls == []


@pytest.mark.asyncio
async def test_registry_error_is_sanitized_and_has_no_success_headers() -> None:
    secrets = (
        "ghp_registry_secret",
        "postgresql://user:password@tenant-db/private",
        "https://user:password@ghcr.internal/private",
    )
    bundle = make_bundle()
    loader = ExactByteLoader()
    loader.manifest_errors[bundle.ref(QUARANTINE_REPOSITORY)] = [
        FakeLoaderFailure(" ".join(secrets))
    ]
    app = app_with_loader(loader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://plugin-static"
    ) as client:
        response = await client.get(ready_path(bundle.digest))

    assert response.status_code == 503
    assert response.json() == {"code": "EVIDENCE_UNAVAILABLE"}
    assert "X-AgentScale-Bundle-Digest" not in response.headers
    assert "Content-Security-Policy" not in response.headers
    for secret in secrets:
        assert secret not in response.text
        assert secret not in repr(response.headers)
        assert secret not in str(response.request.url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected_status", "expected_code"),
    (
        ("invalid", 404, "BUNDLE_NOT_READY"),
        ("oversize", 413, "BUNDLE_TOO_LARGE"),
    ),
)
async def test_invalid_and_oversize_errors_are_typed_and_sanitized(
    kind: str, expected_status: int, expected_code: str
) -> None:
    bundle = make_bundle(
        manifest_mutator=wrong_oci_media_type if kind == "invalid" else None
    )
    loader = ExactByteLoader()
    loader.add(bundle)
    app = app_with_loader(
        loader, **({"max_config_bytes": 1} if kind == "oversize" else {})
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://plugin-static"
    ) as client:
        response = await client.get(ready_path(bundle.digest))

    assert response.status_code == expected_status
    assert response.json() == {"code": expected_code}
    assert response.text == '{"code":"' + expected_code + '"}'
    assert "X-AgentScale-Bundle-Digest" not in response.headers


@pytest.mark.asyncio
async def test_head_success_is_empty_even_with_explicit_accept_html() -> None:
    bundle = make_bundle()
    loader = ExactByteLoader()
    loader.add(bundle)
    app = app_with_loader(loader)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://plugin-static"
    ) as client:
        response = await client.head(
            ready_path(bundle.digest),
            headers={"Accept": "text/html", "Cache-Control": "no-cache"},
        )

    assert response.status_code == 200
    assert response.content == b""
    assert_exact_security_headers(response, bundle.digest)
    assert loader.count("blob") == 2


@pytest.mark.asyncio
async def test_liveness_and_readiness_reflect_only_explicit_composition() -> None:
    bundle = make_bundle()
    loader = ExactByteLoader()
    loader.add(bundle)
    configured = app_with_loader(loader)
    unconfigured = create_app(app_settings=app_settings(configured=False))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=configured), base_url="http://plugin-static"
    ) as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=unconfigured), base_url="http://plugin-static"
    ) as client:
        unconfigured_ready = await client.get("/health/ready")
        unconfigured_evidence = await client.get(ready_path(bundle.digest))

    assert live.status_code == 200
    assert live.json() == {"status": "alive"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert unconfigured_ready.status_code == 503
    assert unconfigured_ready.json() == {"status": "unconfigured"}
    assert unconfigured_evidence.status_code == 503
    assert unconfigured_evidence.json() == {"code": "EVIDENCE_UNAVAILABLE"}
    # Health composition probes do not pretend that a bundle was verified.
    assert loader.calls == []


@pytest.mark.asyncio
async def test_unknown_routes_do_not_invoke_loader_or_expose_write_surface() -> None:
    loader = ExactByteLoader()
    app = app_with_loader(loader)
    paths = (
        "/",
        "/internal/plugin-static/v1/bundles",
        "/internal/plugin-static/v1/write",
        ready_path(sha256(b"unknown")),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://plugin-static"
    ) as client:
        responses = [await client.get(path) for path in paths]

    assert [response.status_code for response in responses] == [404, 404, 404, 404]
    assert responses[-1].json() == {"code": "BUNDLE_NOT_READY"}
    # The unknown digest is a real evidence route and therefore performs a
    # failed registry lookup; the three non-routes above perform no lookup.
    evidence_calls = [
        call for call in loader.calls if call[1].endswith("@" + sha256(b"unknown"))
    ]
    assert len(evidence_calls) == 2  # quarantine, then not-found release fallback
    assert all(
        call[1].endswith("@" + sha256(b"unknown")) for call in loader.calls
    )
    evidence_route = next(
        route
        for route in app.routes
        if getattr(route, "name", None) == "plugin_static_bundle_ready"
    )
    assert evidence_route.methods == {"GET", "HEAD"}
    assert not any(
        str(getattr(route, "path", "")).startswith(
            "/internal/plugin-static/v1/write"
        )
        for route in app.routes
    )
