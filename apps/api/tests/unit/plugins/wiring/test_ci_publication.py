"""Wiring contracts for emergency-disabled plugin CI publication.

Tests are intentionally not executed during migration verification freeze.
Composition doubles perform no database session, network, subprocess, install,
deploy, or grant-writing operation. Publication uses the real service and SQL
approval repository with an inert session factory.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
import pytest

from prodavan.api.internal.plugin_artifacts import (
    PLUGIN_CI_PUBLICATION_SERVICE_STATE,
    get_plugin_artifact_publication_service,
)
from prodavan.api.internal.plugin_artifacts import router as plugin_artifacts_router
from prodavan.application.plugins.artifacts.oidc import (
    GITHUB_ACTIONS_JWKS_URL,
    GitHubActionsOidcValidator,
)
from prodavan.application.plugins.artifacts.publication import (
    DurablePluginInstanceScopeResolver,
)
from prodavan.application.plugins.artifacts.registry import PluginArtifactRegistry
from prodavan.application.plugins.capabilities.contracts import CapabilityName
from prodavan.application.plugins.artifacts.verification import (
    PLATFORM_VERSION,
    ArtifactVerifier,
    CompatibilityGate,
)
from prodavan.application.plugins.sources.service import ModuleSourceProvisioningConfig
from prodavan.application.plugins.sources.types import ModuleSourceTrustConfig
from prodavan.application.plugins.wiring.install import (
    PLUGIN_CI_OIDC_AUDIENCE_ENV,
    PLUGIN_CI_PUBLICATION_ENABLED_ENV,
    PluginCompositionError,
    _platform_overrides,
    install_plugin_ci_publication,
)
from prodavan.infrastructure.persistence.repositories.plugins.artifacts import (
    SqlArtifactRegistryStore,
)
from prodavan.infrastructure.persistence.repositories.plugins.publication_approvals import (
    SqlPluginPublicationApprovalRepository,
)
from prodavan.infrastructure.persistence.repositories.plugins.grants import (
    SqlCapabilityGrantRepository,
)
from prodavan.infrastructure.persistence.repositories.plugins.instances import (
    SqlPluginInstanceRepository,
)
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from prodavan.infrastructure.plugins.sources import (
    BranchProtectionPolicy,
    GitHubAppClient,
    GitHubInstallationTokenProfile,
    ManagedOrganization,
    RepositoryTemplate,
)


IDENTITY = (
    "https://github.com/ne-tort/agentscale/.github/workflows/"
    "plugin-artifacts.yml@refs/heads/main"
)
ISSUER = "https://token.actions.githubusercontent.com"
ORGANIZATION = "managed-plugins"
INSTALLATION_ID = 42


def _effective_routes(app: FastAPI) -> list[Any]:
    """Resolve FastAPI 0.141 lazy includes while preserving route objects."""
    routes: list[Any] = []

    def visit(candidates: Any) -> None:
        for route in candidates:
            effective_candidates = getattr(route, "effective_candidates", None)
            if callable(effective_candidates):
                visit(effective_candidates())
            else:
                routes.append(route)

    visit(app.routes)
    return routes


class _InertSessionFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        raise AssertionError("wiring tests must not open a database session")


class _ScopedResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, *, company_id: str, plugin_id: str) -> str:
        self.calls.append((company_id, plugin_id))
        return "inst_scoped_test"


class _InertCosign:
    def verify(self, image_ref: str, identity: str, issuer: str) -> Any:
        raise AssertionError("wiring tests must not invoke cosign")


class _InertScopedApi:
    # Custom API ports must explicitly declare scoped authentication; a mounted
    # ServiceAccount fallback is rejected by the production validator.
    scoped_identity = True

    async def request(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("wiring tests must not call the K8s API")


class _InertTokenProvider:
    async def get_token(self) -> str:
        raise AssertionError("wiring tests must not mint tokens")


class _InertActivator:
    async def wake(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("wiring tests must not wake backends")


async def _inert_store_io(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("wiring tests must not perform store IO")


class _Lifespan:
    def __init__(self) -> None:
        self.resources: list[Any] = []

    def register(self, resource: Any) -> "_Lifespan":
        self.resources.append(resource)
        return self


class _RegistryTokenProvider(GitHubAppClient):
    """No-I/O subclass used only where production requires GitHubAppClient."""

    def __init__(self, organization: ManagedOrganization) -> None:
        self.calls: list[tuple[int, str]] = []
        self._organizations = {organization.name.casefold(): organization}

    async def installation_token(self, installation_id: int, *, profile: str) -> str:
        self.calls.append((installation_id, profile))
        return "unit-registry-token"

    async def aclose(self) -> None:
        return None


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        plugin_platform_enabled=False,
        plugin_cosign_identity=IDENTITY,
        plugin_cosign_issuer=ISSUER,
    )


def _environment() -> dict[str, str]:
    return {
        PLUGIN_CI_PUBLICATION_ENABLED_ENV: "true",
        PLUGIN_CI_OIDC_AUDIENCE_ENV: "agentscale-plugin-ci",
    }


def _managed_organization() -> ManagedOrganization:
    return ManagedOrganization(
        name=ORGANIZATION,
        installation_id=INSTALLATION_ID,
        template=RepositoryTemplate(
            owner=ORGANIZATION,
            name="private-plugin-template",
            agents_revision="a" * 40,
            security_revision="b" * 40,
        ),
    )


def _source_config(
    organization: ManagedOrganization,
) -> ModuleSourceProvisioningConfig:
    return ModuleSourceProvisioningConfig(
        organization=organization,
        trust=ModuleSourceTrustConfig(
            installation_credential_ref=(
                "secret://agentscale/github-app-private-key"
            ),
            trusted_workflow_ref=(
                "ne-tort/agentscale/.github/workflows/"
                f"plugin-artifacts.yml@{'a' * 40}"
            ),
        ),
        branch_protection=BranchProtectionPolicy(
            required_status_checks=("plugin-verify",)
        ),
        default_branch="main",
    )


def _approval_reader(
    session_factory: _InertSessionFactory,
) -> SqlPluginPublicationApprovalRepository:
    return SqlPluginPublicationApprovalRepository(session_factory=session_factory)


def _build(
    app: FastAPI,
    session_factory: _InertSessionFactory,
    lifespan: _Lifespan,
    *,
    environ: dict[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> Any:
    requested = dict(overrides or {})
    managed = requested.get("managed_organization") or _managed_organization()
    dependencies: dict[str, Any] = {
        "managed_organization": managed,
        "github_app": _RegistryTokenProvider(managed),
        "source_config": _source_config(managed),
        "approval_reader": _approval_reader(session_factory),
        "cosign": _InertCosign(),
    }
    dependencies.update(requested)
    service = install_plugin_ci_publication(
        app,
        _settings(),
        session_factory=session_factory,
        lifespan_manager=lifespan,
        environ=_environment() if environ is None else environ,
        overrides=dependencies,
    )
    assert service is not None
    return service


def test_singleton_verifier_state_and_shared_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    session_factory = _InertSessionFactory()
    lifespan = _Lifespan()
    managed = _managed_organization()
    provider = _RegistryTokenProvider(managed)
    source_config = _source_config(managed)

    service = _build(
        app,
        session_factory,
        lifespan,
        overrides={
            "managed_organization": managed,
            "github_app": provider,
            "source_config": source_config,
        },
    )

    assert getattr(app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE) is service
    assert app.state.plugin_artifact_verifier is service._verifier
    assert app.state.plugin_artifact_registry is service._registry
    assert service._registry._verifier is service._verifier
    assert service._registry._verifier._seal is service._verifier._seal
    assert service._sources._session_factory is session_factory
    assert service._approvals._session_factory is session_factory
    assert service._registry._store._session_factory is session_factory
    assert isinstance(service._instance_scopes, DurablePluginInstanceScopeResolver)
    assert service._instance_scopes._grant_store._session_factory is session_factory
    assert service._instance_scopes._instance_store._session_factory is session_factory
    assert service._loader._authorizer is not None
    assert service._loader._allowed == frozenset({"ghcr.io"})
    assert isinstance(service._oidc, GitHubActionsOidcValidator)
    assert service._oidc._audience == "agentscale-plugin-ci"
    assert service._oidc._jwks_url == GITHUB_ACTIONS_JWKS_URL

    assert _build(app, session_factory, lifespan) is service
    assert len(lifespan.resources) == 1
    assert not hasattr(app.state, "plugin_platform")
    assert not hasattr(app.state, "plugin_admin_service")
    assert not hasattr(app.state, "plugin_gateway_runtime")
    assert not hasattr(app.state, "plugin_activator")
    assert not hasattr(service, "install")
    assert not hasattr(service, "deploy")
    assert not hasattr(service, "create_grant")
    assert session_factory.calls == 0


def test_active_grant_reader_is_not_a_temporary_publication_bypass() -> None:
    app = FastAPI()
    session_factory = _InertSessionFactory()

    service = _build(
        app,
        session_factory,
        _Lifespan(),
        overrides={"grant_reader": object()},
    )

    assert isinstance(
        service._approvals,
        SqlPluginPublicationApprovalRepository,
    )
    assert service._approvals._session_factory is session_factory
    assert not hasattr(service, "_grants")
    assert not hasattr(service, "create_grant")


def test_non_durable_scope_repository_fails_closed() -> None:
    app = FastAPI()
    session_factory = _InertSessionFactory()

    with pytest.raises(PluginCompositionError) as exc_info:
        _build(
            app,
            session_factory,
            _Lifespan(),
            overrides={"instance_scope_grant_store": object()},
        )

    assert exc_info.value.step == "ci_instance_scope"
    assert not hasattr(app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
    assert session_factory.calls == 0


def test_in_memory_scope_resolver_override_fails_closed() -> None:
    app = FastAPI()
    session_factory = _InertSessionFactory()

    with pytest.raises(PluginCompositionError) as exc_info:
        _build(
            app,
            session_factory,
            _Lifespan(),
            overrides={"instance_scope_resolver": _ScopedResolver()},
        )

    assert exc_info.value.step == "ci_instance_scope"
    assert not hasattr(app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
    assert session_factory.calls == 0


def test_attached_admin_scope_repositories_are_reused() -> None:
    app = FastAPI()
    session_factory = _InertSessionFactory()
    grants = SqlCapabilityGrantRepository(session_factory=session_factory)
    instances = SqlPluginInstanceRepository(session_factory=session_factory)
    app.state.plugin_admin_ports = SimpleNamespace(
        grant_store=grants,
        instance_store=instances,
    )

    service = _build(app, session_factory, _Lifespan())

    assert service._instance_scopes._grant_store is grants
    assert service._instance_scopes._instance_store is instances
    assert session_factory.calls == 0


def test_attached_platform_pair_is_reused_without_taking_ownership() -> None:
    app = FastAPI()
    session_factory = _InertSessionFactory()
    lifespan = _Lifespan()
    verifier = ArtifactVerifier(
        _InertCosign(),
        identity=IDENTITY,
        issuer=ISSUER,
        compatibility=CompatibilityGate(platform_version=PLATFORM_VERSION),
    )
    store = SqlArtifactRegistryStore(session_factory=session_factory)
    registry = PluginArtifactRegistry(store=store, verifier=verifier)
    app.state.plugin_artifact_verifier = verifier
    app.state.plugin_artifact_registry = registry

    service = _build(app, session_factory, lifespan)

    assert service._verifier is verifier
    assert service._registry is registry
    assert service._registry._verifier is verifier
    assert service._registry._store is store
    assert service._sources._session_factory is session_factory
    assert service._approvals._session_factory is session_factory
    assert len(lifespan.resources) == 1
    assert lifespan.resources[0]._artifact_store is None
    assert lifespan.resources[0]._registry_authorizer is service._loader._authorizer


def test_platform_overrides_keep_the_same_artifact_pair() -> None:
    session_factory = _InertSessionFactory()
    cosign = _InertCosign()
    verifier = ArtifactVerifier(
        cosign,
        identity=IDENTITY,
        issuer=ISSUER,
        compatibility=CompatibilityGate(platform_version=PLATFORM_VERSION),
    )
    store = SqlArtifactRegistryStore(session_factory=session_factory)
    registry = PluginArtifactRegistry(store=store, verifier=verifier)
    repos = SimpleNamespace(
        grant_store=SimpleNamespace(
            get_by_grant_id=_inert_store_io,
            get=_inert_store_io,
        ),
        grant_revocation=object(),
        quota=object(),
        audit=object(),
        artifact_store=store,
        instance_store=SimpleNamespace(get_by_id=_inert_store_io),
    )
    # Explicit production-shaped scoped runtime: real RuntimeConfig and the
    # scoped lifecycle API/token provider required by K8sPodExecutor and
    # K8sEgressTransport construction. Validation is intentionally not weakened.
    runtime = SimpleNamespace(
        cosign=cosign,
        config=RuntimeConfig(
            namespace="agentscale-dev",
            gateway_url="http://plugin-gateway.agentscale-dev.svc:8080",
        ),
        lifecycle_api=_InertScopedApi(),
        token_provider=_InertTokenProvider(),
        activator=_InertActivator(),
        proxy=SimpleNamespace(forward=_inert_store_io),
    )
    gateway = SimpleNamespace(auth=object(), proxy=object())

    # One shared delivery-claims store and a dispatcher that owns exactly it,
    # satisfying the final ownership identity check without real Redis.
    claims = object()
    overrides = _platform_overrides(
        repos,
        runtime,
        object(),
        gateway,
        object(),
        # The signer reads the opaque SecretStore reference from overrides
        # (settings stays unset by design in this composition-only test), and
        # validate() resolves it to a >=32-byte key through this store double.
        {
            "event_signing_secret_ref": "vault://composition/plugin-event-signing",
            "secret_store": SimpleNamespace(
                get=lambda ref: "composition-event-signing-key-32bytes!!"
            ),
            # ActivatingPluginEventHandler resolves the activator from ov, not
            # from runtime; all three ports stay inert composition doubles.
            "activator": runtime.activator,
            # One shared Redis wrapper is mandatory for delivery claims.
            "redis_client": SimpleNamespace(
                eval=_inert_store_io, get=_inert_store_io, set=_inert_store_io
            ),
            # Explicit test adapter override: the server-owned job_tasks
            # composition is out of scope for this artifact-pair contract test.
            "adapter_jobs": object(),
            # All nine capability adapters are explicit test doubles (same
            # pattern as the MinIO factory tests), so no MinIO/STS/docs/Kafka
            # production config is needed for this artifact-pair assertion.
            "capability_adapters": {
                name.value: object() for name in CapabilityName
            },
            # A supplied events adapter requires its real dispatcher override,
            # and the final check requires that dispatcher to own the same
            # shared delivery-claims store instance.
            "event_delivery_claims": claims,
            "event_dispatcher": SimpleNamespace(_delivery_claims=claims),
        },
        {"artifact_verifier": verifier, "artifact_registry": registry},
    )

    assert overrides["artifact_verifier"] is verifier
    assert overrides["artifact_registry"] is registry
    assert overrides["artifact_registry_store"] is store


@pytest.mark.parametrize(
    "missing_name",
    ["managed_organization", "github_app", "source_config"],
)
def test_missing_durable_or_registry_dependency_fails_closed(
    missing_name: str,
) -> None:
    app = FastAPI()
    session_factory = _InertSessionFactory()
    managed = _managed_organization()
    overrides: dict[str, Any] = {
        "managed_organization": managed,
        "github_app": _RegistryTokenProvider(managed),
        "source_config": _source_config(managed),
        "approval_reader": _approval_reader(session_factory),
        "cosign": _InertCosign(),
    }
    overrides[missing_name] = None

    with pytest.raises(PluginCompositionError) as exc_info:
        _build(app, session_factory, _Lifespan(), overrides=overrides)

    expected_step = {
        "managed_organization": "ci_registry_auth",
        "github_app": "ci_registry_auth",
        "source_config": "ci_source_config",
    }[missing_name]
    assert exc_info.value.step == expected_step
    assert not hasattr(app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
    assert not hasattr(app.state, "module_source_service_factory")
    assert session_factory.calls == 0


def test_non_sql_approval_reader_fails_closed() -> None:
    app = FastAPI()
    session_factory = _InertSessionFactory()

    with pytest.raises(PluginCompositionError) as exc_info:
        _build(
            app,
            session_factory,
            _Lifespan(),
            overrides={"approval_reader": object()},
        )

    assert exc_info.value.step == "ci_approval"
    assert not hasattr(app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
    assert not hasattr(app.state, "module_source_service_factory")


def test_disabled_ci_does_not_build_or_override_platform() -> None:
    app = FastAPI()
    session_factory = _InertSessionFactory()
    lifespan = _Lifespan()
    environ = _environment()
    environ[PLUGIN_CI_PUBLICATION_ENABLED_ENV] = "false"

    service = install_plugin_ci_publication(
        app,
        _settings(),
        session_factory=session_factory,
        lifespan_manager=lifespan,
        environ=environ,
    )

    assert service is None
    assert not hasattr(app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
    assert not hasattr(app.state, "plugin_platform")
    assert lifespan.resources == []
    assert session_factory.calls == 0


def test_internal_router_has_no_employee_or_keycloak_fallback() -> None:
    app = FastAPI()
    app.include_router(plugin_artifacts_router)
    route = next(
        item
        for item in _effective_routes(app)
        if getattr(item, "path", None) == "/internal/plugin-artifacts"
    )

    assert route.methods == {"POST"}
    assert len(route.dependant.dependencies) == 1
    dependency = route.dependant.dependencies[0]
    assert dependency.call is get_plugin_artifact_publication_service
    assert dependency.dependencies == []
    assert "get_agent_auth" not in str(route.dependant)
    assert "keycloak" not in str(route.dependant).lower()
    assert "employee" not in str(route.dependant).lower()


def test_main_mounts_internal_ci_route_and_service_without_global_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Import before enabling CI: main creates its module-level app on import.
    import prodavan.application.plugins.wiring.module_sources as source_composition_module
    import prodavan.main as main_module
    from prodavan.infrastructure.persistence import database

    managed = _managed_organization()
    provider = _RegistryTokenProvider(managed)
    source_config = _source_config(managed)
    real_source_install = source_composition_module.install_module_source_composition

    def install_source_with_dependencies(
        app: Any, settings: Any, **kwargs: Any
    ) -> Any:
        return real_source_install(
            app,
            settings,
            overrides={
                "source_config": source_config,
                "github_app": provider,
            },
            **kwargs,
        )

    monkeypatch.setattr(
        source_composition_module,
        "install_module_source_composition",
        install_source_with_dependencies,
    )
    for name, value in _environment().items():
        monkeypatch.setenv(name, value)
    session_factory = _InertSessionFactory()
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(
            app_version="wiring-test",
            api_v1_prefix="/api/v1",
            cors_origin_list=[],
            plugin_platform_enabled=False,
            plugin_cosign_identity=IDENTITY,
            plugin_cosign_issuer=ISSUER,
        ),
    )
    monkeypatch.setattr(database, "get_session_factory", lambda: session_factory)

    app = main_module.create_app()
    service = getattr(app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
    app_routes = _effective_routes(app)
    route = next(
        item
        for item in app_routes
        if getattr(item, "path", None) == "/internal/plugin-artifacts"
    )

    assert service._sources._session_factory is session_factory
    assert service._approvals._session_factory is session_factory
    assert service._registry._store._session_factory is session_factory
    assert service._instance_scopes._grant_store._session_factory is session_factory
    assert service._instance_scopes._instance_store._session_factory is session_factory
    assert service._registry._verifier is service._verifier
    assert callable(app.state.module_source_service_factory)
    assert route.methods == {"POST"}
    assert not hasattr(app.state, "plugin_platform")
    assert "/api/v1/internal/plugin-artifacts" not in {
        getattr(item, "path", None) for item in app_routes
    }
    assert session_factory.calls == 0


@pytest.mark.asyncio
async def test_github_app_installation_delegate_uses_typed_registry_profile() -> None:
    client = object.__new__(GitHubAppClient)
    calls: list[tuple[int, GitHubInstallationTokenProfile]] = []

    async def fake_private_token(
        installation_id: int, profile: GitHubInstallationTokenProfile
    ) -> str:
        calls.append((installation_id, profile))
        return "unit-registry-token"

    client._installation_token = fake_private_token  # type: ignore[method-assign]

    token = await GitHubAppClient.installation_token(
        client,
        INSTALLATION_ID,
        profile="registry_read",
    )
    assert token == "unit-registry-token"
    assert calls == [
        (INSTALLATION_ID, GitHubInstallationTokenProfile.REGISTRY_READ)
    ]

    with pytest.raises(ValueError):
        await GitHubAppClient.installation_token(
            client,
            INSTALLATION_ID,
            profile="not-a-profile",
        )
    assert calls == [
        (INSTALLATION_ID, GitHubInstallationTokenProfile.REGISTRY_READ)
    ]


def test_failed_lifecycle_registration_rolls_back_state_and_closes_owned_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    session_factory = _InertSessionFactory()
    closed: list[Any] = []
    provider = _RegistryTokenProvider(_managed_organization())

    class _FailingLifespan:
        @staticmethod
        def register(resource: Any) -> None:
            raise RuntimeError("injected registration failure")

    monkeypatch.setattr(
        SqlArtifactRegistryStore,
        "close",
        lambda store: closed.append(store),
    )

    with pytest.raises(PluginCompositionError) as exc_info:
        _build(
            app,
            session_factory,
            _FailingLifespan(),  # type: ignore[arg-type]
            overrides={"github_app": provider},
        )

    assert exc_info.value.step == "ci_lifecycle"
    assert len(closed) == 1
    assert not hasattr(app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
    assert not hasattr(app.state, "plugin_artifact_verifier")
    assert not hasattr(app.state, "plugin_artifact_registry")
    assert not hasattr(app.state, "module_source_service_factory")
    assert provider.calls == []


@pytest.mark.asyncio
async def test_owned_store_and_authorizer_are_closed_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    session_factory = _InertSessionFactory()
    lifespan = _Lifespan()
    service = _build(app, session_factory, lifespan)
    closed_stores: list[Any] = []
    closed_authorizers: list[Any] = []
    store = service._registry._store
    authorizer = service._loader._authorizer
    monkeypatch.setattr(store, "close", lambda: closed_stores.append(store))

    async def close_authorizer() -> None:
        closed_authorizers.append(authorizer)

    monkeypatch.setattr(authorizer, "aclose", close_authorizer)

    assert len(lifespan.resources) == 1
    await lifespan.resources[0].shutdown()

    assert closed_authorizers == [authorizer]
    assert closed_stores == [store]
