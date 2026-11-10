"""Managed source/shared-client composition contracts.

Written during the migration verification freeze; intentionally not executed here.
All doubles are inert: no database session, GitHub/GHCR request, cosign process,
installation token, runtime deployment, or grant write is performed.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
import pytest

from prodavan.api.v1 import plugin_sources as source_api
from prodavan.api.v1.plugin_sources import ModuleSourceResponse
from prodavan.application.plugins.wiring import module_sources
from prodavan.application.plugins.wiring.install import (
    PLUGIN_CI_OIDC_AUDIENCE_ENV,
    PLUGIN_CI_PUBLICATION_ENABLED_ENV,
    PluginCompositionError,
    install_plugin_ci_publication,
)
from prodavan.application.plugins.wiring.module_sources import (
    MODULE_SOURCE_COMPOSITION_STATE,
    MODULE_SOURCE_CONFIG_STATE,
    MODULE_SOURCE_GITHUB_APP_STATE,
    MODULE_SOURCE_LIFESPAN_NAME,
    build_plugin_ci_approval_reader,
    install_module_source_composition,
)
from prodavan.infrastructure.persistence.models import PluginPublicationApprovalRow
from prodavan.infrastructure.plugins.sources.github_app import GitHubAppClient
from prodavan.infrastructure.plugins.sources.models import ManagedOrganization

PRIVATE_KEY_REF = "vault://ai_keys/github-app-private-key"
INSTALLATION_CREDENTIAL_REF = "secret://agentscale/github-app-installation"
TRUSTED_WORKFLOW_REF = (
    "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@"
    + "a" * 40
)
CI_IDENTITY = (
    "https://github.com/ne-tort/agentscale/.github/workflows/"
    "plugin-artifacts.yml@refs/heads/main"
)


class _Secrets:
    def __init__(self) -> None:
        self.refs: list[str] = []

    async def read_secret(self, reference: str) -> bytes:
        self.refs.append(reference)
        # Construction must not resolve the key. No JWT is minted in these tests.
        return b"unit-test-not-a-private-key"


class _SessionFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        raise AssertionError("composition tests must not open a database session")


class _Lifespan:
    def __init__(self) -> None:
        self.resources: list[Any] = []

    def register(self, resource: Any) -> "_Lifespan":
        self.resources.append(resource)
        return self


class _Cosign:
    def verify(self, image_ref: str, identity: str, issuer: str) -> Any:
        raise AssertionError("composition tests must not invoke cosign")


class _NoIoClient(GitHubAppClient):
    def __init__(self, organization: ManagedOrganization) -> None:
        self._organizations = {organization.name.casefold(): organization}
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        plugin_module_source_enabled=True,
        plugin_module_source_github_app_id=12345,
        plugin_module_source_github_app_private_key_secret_ref=PRIVATE_KEY_REF,
        plugin_module_source_github_app_organization="managed-plugins",
        plugin_module_source_github_app_installation_id=42,
        plugin_module_source_github_app_template_owner="managed-plugins",
        plugin_module_source_github_app_template_name="private-plugin-template",
        plugin_module_source_github_app_template_agents_revision="a" * 40,
        plugin_module_source_github_app_template_security_revision="b" * 40,
        plugin_module_source_github_app_timeout_seconds=10.0,
        plugin_module_source_installation_credential_ref=INSTALLATION_CREDENTIAL_REF,
        plugin_module_source_trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
        plugin_module_source_default_branch="main",
        plugin_module_source_required_status_checks="plugin-verify,plugin-security",
        plugin_module_source_required_approving_review_count=1,
        plugin_cosign_identity=CI_IDENTITY,
        plugin_cosign_issuer="https://token.actions.githubusercontent.com",
    )


def _install(
    app: FastAPI,
    settings: SimpleNamespace,
    lifespan: _Lifespan,
    *,
    secrets: _Secrets | None = None,
) -> Any:
    return install_module_source_composition(
        app,
        settings,
        lifespan_manager=lifespan,
        overrides={"secret_provider": secrets or _Secrets()},
    )


def test_source_factory_is_installed_with_real_client_and_managed_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    lifespan = _Lifespan()
    session_factory = _SessionFactory()
    secrets = _Secrets()
    monkeypatch.setattr(source_api, "get_session_factory", lambda: session_factory)

    composition = _install(app, _settings(), lifespan, secrets=secrets)
    factory = getattr(app.state, source_api.SERVICE_FACTORY_STATE)
    service = factory(object())

    assert isinstance(composition.github_app, GitHubAppClient)
    assert app.state.module_source_github_app is composition.github_app
    assert app.state.module_source_managed_config is composition.config
    assert service._provider is composition.github_app
    assert service._config is composition.config
    assert service._repository._session_factory is session_factory
    assert composition.config.organization.installation_id == 42
    assert composition.config.organization.template.name == "private-plugin-template"
    assert composition.config.branch_protection.required_status_checks == (
        "plugin-verify",
        "plugin-security",
    )
    assert composition.config.trust.trusted_workflow_ref == TRUSTED_WORKFLOW_REF
    assert secrets.refs == []
    assert [resource.name for resource in lifespan.resources] == [
        MODULE_SOURCE_LIFESPAN_NAME
    ]
    assert _install(app, _settings(), lifespan) is composition
    assert len(lifespan.resources) == 1
    assert PluginPublicationApprovalRow.__tablename__ == "plugin_publication_approvals"
    asyncio.run(composition.github_app.aclose())


@pytest.mark.asyncio
async def test_production_secret_provider_adapts_existing_secret_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prodavan.infrastructure.secrets import store as secret_store_module

    class _Store:
        def __init__(self) -> None:
            self.refs: list[str] = []

        def get(self, reference: str) -> str:
            self.refs.append(reference)
            return "unit-test-key-material"

    store = _Store()
    monkeypatch.setattr(secret_store_module, "get_secret_store", lambda: store)
    composition = install_module_source_composition(
        FastAPI(),
        _settings(),
        lifespan_manager=_Lifespan(),
    )

    provider = composition.github_app._secret_provider
    assert isinstance(provider, module_sources.SecretStoreGitHubAppSecretProvider)
    assert store.refs == []
    assert await provider.read_secret(PRIVATE_KEY_REF) == "unit-test-key-material"
    assert store.refs == [PRIVATE_KEY_REF]
    await composition.github_app.aclose()


@pytest.mark.asyncio
async def test_source_and_ghcr_borrow_same_client_and_root_closes_it_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    lifespan = _Lifespan()
    session_factory = _SessionFactory()
    monkeypatch.setattr(source_api, "get_session_factory", lambda: session_factory)
    composition = _install(app, _settings(), lifespan)
    shared = composition.github_app
    close_calls = 0
    original_close = shared.aclose

    async def counted_close() -> None:
        nonlocal close_calls
        close_calls += 1
        await original_close()

    monkeypatch.setattr(shared, "aclose", counted_close)
    approval_reader = build_plugin_ci_approval_reader(session_factory)
    ci_service = install_plugin_ci_publication(
        app,
        _settings(),
        session_factory=session_factory,
        lifespan_manager=lifespan,
        managed_organization=composition.managed_organization,
        github_app=shared,
        source_config=composition.config,
        approval_reader=approval_reader,
        environ={
            PLUGIN_CI_PUBLICATION_ENABLED_ENV: "true",
            PLUGIN_CI_OIDC_AUDIENCE_ENV: "agentscale-plugin-ci",
        },
        overrides={"cosign": _Cosign()},
    )

    source_service = getattr(app.state, source_api.SERVICE_FACTORY_STATE)(object())
    authorizer = ci_service._loader._authorizer
    assert source_service._provider is shared
    assert authorizer._token_provider is shared
    assert ci_service._approvals._session_factory is session_factory
    assert len(lifespan.resources) == 2

    # CI owns its authorizer/store, not the borrowed client.
    await lifespan.resources[-1].shutdown()
    assert authorizer._closed is True
    assert close_calls == 0

    source_resource = lifespan.resources[0]
    await source_resource.shutdown()
    await source_resource.shutdown()
    assert close_calls == 1
    assert session_factory.calls == 0


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("plugin_module_source_github_app_id", None),
        ("plugin_module_source_github_app_installation_id", None),
        ("plugin_module_source_github_app_organization", "not an org"),
        ("plugin_module_source_github_app_template_name", "bad/template"),
        ("plugin_module_source_trusted_workflow_ref", "ne-tort/agentscale/workflow.yml@main"),
        ("plugin_module_source_required_status_checks", ""),
    ],
)
def test_missing_or_invalid_managed_configuration_fails_closed(
    field_name: str,
    value: Any,
) -> None:
    app = FastAPI()
    lifespan = _Lifespan()
    settings = _settings()
    setattr(settings, field_name, value)

    with pytest.raises(PluginCompositionError):
        _install(app, settings, lifespan)

    assert not hasattr(app.state, MODULE_SOURCE_COMPOSITION_STATE)
    assert not hasattr(app.state, MODULE_SOURCE_GITHUB_APP_STATE)
    assert not hasattr(app.state, MODULE_SOURCE_CONFIG_STATE)
    assert not hasattr(app.state, source_api.SERVICE_FACTORY_STATE)
    assert lifespan.resources == []


@pytest.mark.parametrize(
    "private_key_value",
    ["", "-----BEGIN PRIVATE KEY-----\nunit-secret\n-----END PRIVATE KEY-----"],
)
def test_missing_or_plaintext_private_key_is_sanitized_and_fail_closed(
    private_key_value: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    settings = _settings()
    settings.plugin_module_source_github_app_private_key_secret_ref = private_key_value

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(PluginCompositionError) as caught:
            _install(app, settings, _Lifespan())

    assert caught.value.step == "github_app_private_key"
    exposed = str(caught.value) + caplog.text
    assert PRIVATE_KEY_REF not in exposed
    assert "unit-secret" not in exposed
    assert "BEGIN PRIVATE KEY" not in exposed
    assert not hasattr(app.state, source_api.SERVICE_FACTORY_STATE)


def test_approval_reader_is_required_only_at_the_ci_composition_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_module = (
        "prodavan.infrastructure.persistence.repositories.plugins.publication_approvals"
    )
    monkeypatch.setitem(sys.modules, repository_module, None)

    # Source-only construction never imports the missing approval repository.
    app = FastAPI()
    composition = _install(app, _settings(), _Lifespan())
    assert callable(getattr(app.state, source_api.SERVICE_FACTORY_STATE))

    with pytest.raises(PluginCompositionError) as caught:
        build_plugin_ci_approval_reader(_SessionFactory())
    assert caught.value.step == "ci_approval"
    assert "ACTIVE grant fallback" in str(caught.value)
    asyncio.run(composition.github_app.aclose())


def test_failed_lifecycle_registration_rolls_back_state_and_owned_client() -> None:
    app = FastAPI()
    settings = _settings()
    config = module_sources.managed_config_from_settings(settings)
    client = _NoIoClient(config.organization)

    class _FailingLifespan:
        @staticmethod
        def register(resource: Any) -> None:
            raise RuntimeError("injected registration failure")

    with pytest.raises(PluginCompositionError) as caught:
        install_module_source_composition(
            app,
            settings,
            lifespan_manager=_FailingLifespan(),
            overrides={"source_config": config, "github_app": client},
        )

    assert caught.value.step == "module_source_lifecycle"
    assert client.close_calls == 1
    for key in (
        MODULE_SOURCE_COMPOSITION_STATE,
        MODULE_SOURCE_GITHUB_APP_STATE,
        MODULE_SOURCE_CONFIG_STATE,
        source_api.SERVICE_FACTORY_STATE,
    ):
        assert not hasattr(app.state, key)


def test_secret_refs_do_not_enter_public_response_or_composition_repr() -> None:
    app = FastAPI()
    composition = _install(app, _settings(), _Lifespan())

    assert "installation_credential_ref" not in ModuleSourceResponse.model_fields
    assert "private_key_secret_ref" not in ModuleSourceResponse.model_fields
    assert PRIVATE_KEY_REF not in repr(composition)
    assert INSTALLATION_CREDENTIAL_REF not in repr(composition)
    assert not hasattr(app.state, "plugin_platform")
    assert not hasattr(app.state, "plugin_gateway_runtime")
    asyncio.run(composition.github_app.aclose())
