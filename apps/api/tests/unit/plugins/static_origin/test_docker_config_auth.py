"""Offline security checks for the plugin-static dockerconfigjson fallback."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from prodavan.application.plugins.artifacts.publication import (
    HttpxOciArtifactLoader,
    _LoaderError,
)
from prodavan.application.plugins.wiring import module_sources
from prodavan.application.plugins.wiring.install import PluginCompositionError
from prodavan.infrastructure.plugins.artifacts.docker_config_auth import (
    GhcrDockerConfigAuthorizer,
)
from prodavan.infrastructure.plugins.artifacts.ghcr_auth import (
    GhcrAuthConfigurationError,
    GhcrAuthorizerClosedError,
    GhcrRepositoryError,
)
from prodavan.main_plugin_static import (
    PLUGIN_STATIC_GHCR_DOCKER_CONFIG_PATH_ENV,
    STATIC_EVIDENCE_READY_PATH,
    _compose_static_evidence,
    _proven404_client_factory,
    create_app,
)
from tests.unit.plugins.static_origin.test_main_plugin_static import _settings
from tests.unit.plugins.static_origin.test_static_evidence_service import (
    PLUGIN_ID,
    RELEASE_PREFIX,
    VERSION,
    _build_bundle,
)

REPOSITORY = "ne-tort/plugins/demo_plugin/bundle"


def _full_app_settings() -> Any:
    settings = _settings()
    values: dict[str, Any] = {
        "plugin_module_source_github_app_organization": "ne-tort",
        "plugin_module_source_github_app_template_owner": "ne-tort",
        "plugin_module_source_github_app_template_name": "template",
        "plugin_module_source_github_app_template_agents_revision": "agents-v1",
        "plugin_module_source_github_app_template_security_revision": "security-v1",
        "plugin_module_source_installation_credential_ref": "k8s-secret://app/credential",
        "plugin_module_source_trusted_workflow_ref": "k8s-secret://app/workflow",
        "plugin_module_source_required_status_checks": "ci-evidence",
        "plugin_module_source_default_branch": "main",
        "plugin_module_source_github_app_private_key_secret_ref": (
            "k8s-secret://plugin-module-source-github-app/private-key"
        ),
        "plugin_module_source_github_app_id": 123,
        "plugin_module_source_github_app_installation_id": 42,
        "plugin_module_source_required_approving_review_count": 1,
    }
    for name, value in values.items():
        setattr(settings, name, value)
    return settings
CREDENTIAL = "read-only-user:read-only-token"
ENCODED_CREDENTIAL = base64.b64encode(CREDENTIAL.encode("ascii")).decode("ascii")
EXPECTED_AUTHORIZATION = f"Basic {ENCODED_CREDENTIAL}"


def _valid_config(path: Path, *, auth: str = ENCODED_CREDENTIAL) -> None:
    path.write_text(
        json.dumps({"auths": {"ghcr.io": {"auth": auth}}}),
        encoding="utf-8",
    )


def _assert_sanitized(error: BaseException, secret: str) -> None:
    exposed = "\n".join(
        (
            str(error),
            repr(error),
            str(error.__cause__),
            str(error.__context__),
        )
    )
    assert secret not in exposed
    assert ENCODED_CREDENTIAL not in exposed


@pytest.mark.asyncio
async def test_valid_single_ghcr_auth_is_used_per_request(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = tmp_path / "config.json"
    _valid_config(config)
    authorizer = GhcrDockerConfigAuthorizer(config_path=config)

    with caplog.at_level(logging.DEBUG):
        first = await authorizer.authorization_for(REPOSITORY)

    assert first == EXPECTED_AUTHORIZATION
    assert CREDENTIAL not in repr(authorizer)
    assert ENCODED_CREDENTIAL not in repr(authorizer)
    assert caplog.records == []
    assert CREDENTIAL not in caplog.text
    assert ENCODED_CREDENTIAL not in caplog.text

    # No credential state: a projected Secret rotation is observed next call.
    _valid_config(config, auth=base64.b64encode(b"new-user:new-token").decode())
    second = await authorizer.authorization_for(REPOSITORY)
    assert second == f"Basic {base64.b64encode(b'new-user:new-token').decode()}"


@pytest.mark.asyncio
async def test_loader_sends_basic_and_never_retries_401_anonymously(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    _valid_config(config)
    authorizer = GhcrDockerConfigAuthorizer(config_path=config)
    digest = "sha256:" + "a" * 64
    ref = f"ghcr.io/{REPOSITORY}@{digest}"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401, content=b"private-package")

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        )

    loader = HttpxOciArtifactLoader(
        allowed_registries=("ghcr.io",),
        client_factory=factory,
        authorizer=authorizer,
    )
    with pytest.raises(_LoaderError) as caught:
        await loader.load_manifest(ref)

    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == EXPECTED_AUTHORIZATION
    assert str(caught.value) == "OCI registry request was not accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "secret"),
    [
        (b"{", "not-json"),
        (json.dumps({"auths": {}}).encode(), "empty-auths"),
        (
            json.dumps(
                {
                    "auths": {
                        "ghcr.io": {"auth": ENCODED_CREDENTIAL},
                        "docker.io": {"auth": ENCODED_CREDENTIAL},
                    }
                }
            ).encode(),
            "multiple-registries",
        ),
        (
            json.dumps({"auths": {"https://ghcr.io": {"auth": ENCODED_CREDENTIAL}}}).encode(),
            "wrong-key",
        ),
        (
            json.dumps({"auths": {"ghcr.io": {"username": "u", "password": "p"}}}).encode(),
            "password-fallback",
        ),
        (
            json.dumps(
                {"auths": {"ghcr.io": {"auth": "!!!not-base64!!!"}}}
            ).encode(),
            "invalid-base64",
        ),
        (
            json.dumps({"auths": {"ghcr.io": {"auth": "dXNlcg=="}}}).encode(),
            "missing-password",
        ),
        (
            json.dumps({"credsStore": "desktop", "auths": {}}).encode(),
            "credential-helper",
        ),
    ],
)
async def test_invalid_config_never_falls_back_or_leaks(
    tmp_path: Path,
    content: bytes,
    secret: str,
) -> None:
    config = tmp_path / "config.json"
    config.write_bytes(content)
    authorizer = GhcrDockerConfigAuthorizer(config_path=config)

    with pytest.raises(GhcrAuthConfigurationError) as caught:
        await authorizer.authorization_for(REPOSITORY)

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    _assert_sanitized(caught.value, secret)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path_factory",
    [
        pytest.param(lambda root: root / "missing.json", id="missing-file"),
        pytest.param(lambda root: root, id="directory"),
    ],
)
async def test_missing_or_unusable_file_fails_closed(
    tmp_path: Path,
    path_factory: Any,
) -> None:
    authorizer = GhcrDockerConfigAuthorizer(config_path=path_factory(tmp_path))
    with pytest.raises(GhcrAuthConfigurationError) as caught:
        await authorizer.authorization_for(REPOSITORY)
    assert str(tmp_path) not in str(caught.value)


@pytest.mark.asyncio
async def test_wrong_owner_is_rejected_before_reading_auth(tmp_path: Path) -> None:
    authorizer = GhcrDockerConfigAuthorizer(
        config_path=tmp_path / "does-not-matter.json"
    )
    for repository in (
        "another-org/plugins/demo_plugin/bundle",
        f"https://ghcr.io/{REPOSITORY}",
        "ne-tort",
    ):
        with pytest.raises(GhcrRepositoryError):
            await authorizer.authorization_for(repository)


@pytest.mark.asyncio
async def test_close_disables_later_use(tmp_path: Path) -> None:
    authorizer = GhcrDockerConfigAuthorizer(config_path=tmp_path / "unused.json")
    await authorizer.aclose()
    await authorizer.aclose()
    with pytest.raises(GhcrAuthorizerClosedError):
        await authorizer.authorization_for(REPOSITORY)


def _transport_factory(
    handler: Any,
) -> Any:
    def factory() -> httpx.AsyncClient:
        client = _proven404_client_factory()
        client._transport = httpx.MockTransport(handler)  # noqa: SLF001
        return client

    return factory


def _static_registry_handler(requests: list[httpx.Request], bundle: Any) -> Any:
    release_repository = f"{RELEASE_PREFIX.split('/', 1)[1]}/{PLUGIN_ID}/bundle"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if "/manifests/" in path:
            if (
                path.rsplit("/", 1)[1] == bundle.bundle_digest
                and path.startswith(f"/v2/{release_repository}")
            ):
                return httpx.Response(200, content=bundle.manifest_bytes)
            return httpx.Response(404)
        if "/blobs/" in path:
            data = bundle.blobs.get(path.rsplit("/", 1)[1])
            if data is not None:
                return httpx.Response(200, content=data)
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_static_evidence_uses_explicit_file_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.json"
    _valid_config(config_file)
    monkeypatch.setenv(PLUGIN_STATIC_GHCR_DOCKER_CONFIG_PATH_ENV, str(config_file))

    def absent_managed_config(_settings: Any) -> Any:
        raise PluginCompositionError("module_source_config", "GitHub App is absent")

    monkeypatch.setattr(
        module_sources,
        "managed_config_from_settings",
        absent_managed_config,
    )
    bundle = _build_bundle()
    requests: list[httpx.Request] = []
    monkeypatch.setattr(
        "prodavan.main_plugin_static._proven404_client_factory",
        _transport_factory(_static_registry_handler(requests, bundle)),
    )

    service, policy, lifecycle = _compose_static_evidence(_settings())
    assert policy is not None
    evidence = await service.verify_bundle(
        plugin_id=PLUGIN_ID,
        version=VERSION,
        bundle_digest=bundle.bundle_digest,
    )

    assert evidence.source == "release"
    assert requests
    assert all(
        request.headers.get("Authorization") == EXPECTED_AUTHORIZATION
        for request in requests
    )
    await lifecycle.shutdown()


def test_fallback_requires_explicit_path_and_answers_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "unused.json"
    _valid_config(config_file)
    monkeypatch.delenv(PLUGIN_STATIC_GHCR_DOCKER_CONFIG_PATH_ENV, raising=False)

    app = create_app(_settings())
    path = STATIC_EVIDENCE_READY_PATH.format(
        plugin_id=PLUGIN_ID,
        version=VERSION,
        bundle_digest="sha256:" + "a" * 64,
    )
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert client.get(path).json() == {"code": "EVIDENCE_UNAVAILABLE"}


@pytest.mark.asyncio
async def test_full_github_app_composition_stays_preferred(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.json"
    _valid_config(config_file)
    monkeypatch.setenv(PLUGIN_STATIC_GHCR_DOCKER_CONFIG_PATH_ENV, str(config_file))
    organization = SimpleNamespace(name="ne-tort", installation_id=42)
    source_config = SimpleNamespace(organization=organization)

    class _FakeGitHubClient:
        def __init__(self) -> None:
            self.close_calls = 0

        async def installation_token(self, installation_id: int, *, profile: str) -> str:
            assert (installation_id, profile) == (42, "registry_read")
            return "app-installation-token"

        async def aclose(self) -> None:
            self.close_calls += 1

    def _fail_fallback(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("docker config fallback must not be composed")

    monkeypatch.setattr(
        module_sources,
        "managed_config_from_settings",
        lambda _settings: source_config,
    )
    client = _FakeGitHubClient()
    monkeypatch.setattr(
        module_sources,
        "github_app_client_from_settings",
        lambda _settings, _config, **_kwargs: client,
    )
    monkeypatch.setattr(
        "prodavan.infrastructure.plugins.artifacts."
        "docker_config_auth.GhcrDockerConfigAuthorizer",
        _fail_fallback,
    )

    service, policy, lifecycle = _compose_static_evidence(_full_app_settings())
    assert service is not None and policy is not None
    await lifecycle.shutdown()
    assert client.close_calls == 1
