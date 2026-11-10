"""Typed internal publication API tests (router remains unmounted here)."""

from __future__ import annotations

import json

import pytest
from fastapi import Response
from pydantic import ValidationError

from prodavan.api.internal.plugin_artifacts import PluginArtifactPublicationBody
from prodavan.api.internal.plugin_artifacts import publish_plugin_artifact


def _body() -> PluginArtifactPublicationBody:
    return PluginArtifactPublicationBody(
        source_id="psrc_0123456789abcdef",
        plugin_id="hello_plugin",
        version="1.2.0",
        bundle_ref="ghcr.io/ne-tort/plugins/hello/bundle@sha256:" + "a" * 64,
        bundle_digest="sha256:" + "a" * 64,
        backend_ref="ghcr.io/ne-tort/plugins/hello/backend@sha256:" + "b" * 64,
        backend_digest="sha256:" + "b" * 64,
        manifest={},
        commit_sha="c" * 40,
        ref="refs/heads/main",
        run_url="https://github.com/ne-tort/example/actions/runs/1/attempts/1",
        evidence={"scan_verified": True, "csp_verified": True},
    )


@pytest.mark.asyncio
async def test_unwired_service_returns_safe_typed_quarantine() -> None:
    body = _body()
    response = Response()
    result = await publish_plugin_artifact(
        response,
        body,
        service=None,
        authorization="Bearer super-secret-ci-token",
    )
    assert response.status_code == 503
    assert result.status == "QUARANTINED"
    assert result.code == "PUBLICATION_INTEGRATION_UNAVAILABLE"
    encoded = json.dumps(result.model_dump())
    assert "super-secret-ci-token" not in encoded
    assert body.run_url not in encoded
    assert "internal.example" not in encoded


@pytest.mark.asyncio
async def test_missing_bearer_is_typed_quarantine_not_keycloak_auth() -> None:
    response = Response()
    result = await publish_plugin_artifact(
        response,
        _body(),
        service=None,
        authorization=None,
    )
    assert response.status_code == 401
    assert result.status == "QUARANTINED"
    assert result.code == "OIDC_TOKEN_MISSING"


def test_caller_capability_grant_is_not_accepted_as_evidence() -> None:
    data = _body().model_dump()
    data["evidence"]["capabilities"] = ["secrets", "network"]
    with pytest.raises(ValidationError):
        PluginArtifactPublicationBody.model_validate(data)


def test_latest_and_mismatched_digest_are_rejected_at_typed_body_boundary() -> None:
    data = _body().model_dump()
    data["bundle_ref"] = "ghcr.io/ne-tort/plugins/hello/bundle:latest"
    with pytest.raises(ValidationError):
        PluginArtifactPublicationBody.model_validate(data)

    data = _body().model_dump()
    data["bundle_digest"] = "sha256:" + "d" * 64
    with pytest.raises(ValidationError):
        PluginArtifactPublicationBody.model_validate(data)
