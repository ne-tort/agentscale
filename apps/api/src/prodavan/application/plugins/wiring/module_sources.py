"""Composition for managed module sources and their shared GitHub App client.

This trust contour is independent from the emergency-disabled plugin runtime and
from CI publication. It builds only source provisioning primitives: a managed
organization/template/branch policy, one SecretStore-backed GitHub App client,
and the request-service factory consumed by the already mounted source API.
The same borrowed client can subsequently be passed to CI/GHCR composition.

No inline private key or static installation token is accepted. Secret REFs are
never logged or included in composition errors.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Final, Mapping

from prodavan.api.v1.plugin_sources import (
    SERVICE_FACTORY_STATE as MODULE_SOURCE_SERVICE_FACTORY_STATE,
)
from prodavan.api.v1.plugin_sources import (
    install_module_source_service_factory,
    make_module_source_service_factory,
)
from prodavan.application.plugins.sources.service import ModuleSourceProvisioningConfig
from prodavan.application.plugins.sources.types import ModuleSourceTrustConfig
from prodavan.application.plugins.wiring.install import PluginCompositionError
from prodavan.infrastructure.plugins.sources.github_app import GitHubAppClient
from prodavan.infrastructure.plugins.sources.models import (
    BranchProtectionPolicy,
    ManagedOrganization,
    RepositoryTemplate,
    validate_branch,
    validate_secret_reference,
)

MODULE_SOURCE_COMPOSITION_STATE: Final[str] = "module_source_composition"
MODULE_SOURCE_GITHUB_APP_STATE: Final[str] = "module_source_github_app"
MODULE_SOURCE_CONFIG_STATE: Final[str] = "module_source_managed_config"
MODULE_SOURCE_LIFESPAN_NAME: Final[str] = "module-source-github-app"

_STATE_KEYS: Final[tuple[str, ...]] = (
    MODULE_SOURCE_COMPOSITION_STATE,
    MODULE_SOURCE_GITHUB_APP_STATE,
    MODULE_SOURCE_CONFIG_STATE,
    MODULE_SOURCE_SERVICE_FACTORY_STATE,
)


@dataclass(frozen=True, slots=True)
class ModuleSourceComposition:
    """Non-secret composition handles; REF-bearing values are hidden from repr."""

    github_app: GitHubAppClient = field(repr=False)
    config: ModuleSourceProvisioningConfig = field(repr=False)

    @property
    def managed_organization(self) -> ManagedOrganization:
        return self.config.organization


class SecretStoreGitHubAppSecretProvider:
    """Adapt the existing synchronous SecretStore to the async GitHub port."""

    __slots__ = ("_store",)

    def __init__(self, store: Any) -> None:
        if not callable(getattr(store, "get", None)):
            raise PluginCompositionError(
                "github_app_secret_provider",
                "the configured SecretStore does not expose get()",
            )
        self._store = store

    async def read_secret(self, reference: str) -> str:
        # The store performs blocking file/Vault I/O. Resolved key material is
        # returned only to GitHubAppClient and is never retained by this adapter.
        return await asyncio.to_thread(self._store.get, reference)


class _ModuleSourceGitHubLifecycle:
    """The sole owner of the process-shared GitHubAppClient."""

    __slots__ = ("_client", "_close_started")

    def __init__(self, client: GitHubAppClient) -> None:
        self._client = client
        self._close_started = False

    @property
    def name(self) -> str:
        return MODULE_SOURCE_LIFESPAN_NAME

    async def startup(self) -> None:
        # HTTP and secret resolution are lazy; startup performs no live calls.
        return None

    async def shutdown(self) -> None:
        if self._close_started:
            return
        self._close_started = True
        await self._client.aclose()

    async def health(self) -> None:
        return None


def module_source_enabled(settings: Any) -> bool:
    """Read the independent source flag without consulting runtime/CI flags."""

    value = getattr(settings, "plugin_module_source_enabled", False)
    if not isinstance(value, bool):
        raise PluginCompositionError(
            "module_source_config",
            "PLUGIN_MODULE_SOURCE_ENABLED must be a typed boolean",
        )
    return value


def _required_text(settings: Any, name: str, *, step: str) -> str:
    value = getattr(settings, name, None)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
    ):
        raise PluginCompositionError(step, f"{name.upper()} is required and must be canonical text")
    return value


def _positive_int(settings: Any, name: str, *, step: str) -> int:
    value = getattr(settings, name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PluginCompositionError(step, f"{name.upper()} must be a positive integer")
    return value


def _status_checks(settings: Any) -> tuple[str, ...]:
    raw = _required_text(
        settings,
        "plugin_module_source_required_status_checks",
        step="module_source_config",
    )
    checks = raw.split(",")
    if any(not check or check != check.strip() for check in checks):
        raise PluginCompositionError(
            "module_source_config",
            "PLUGIN_MODULE_SOURCE_REQUIRED_STATUS_CHECKS must be a comma-separated canonical list",
        )
    return tuple(checks)


def managed_config_from_settings(settings: Any) -> ModuleSourceProvisioningConfig:
    """Build and validate all trusted source configuration from typed settings."""

    try:
        organization = ManagedOrganization(
            name=_required_text(
                settings,
                "plugin_module_source_github_app_organization",
                step="module_source_config",
            ),
            installation_id=_positive_int(
                settings,
                "plugin_module_source_github_app_installation_id",
                step="module_source_config",
            ),
            template=RepositoryTemplate(
                owner=_required_text(
                    settings,
                    "plugin_module_source_github_app_template_owner",
                    step="module_source_config",
                ),
                name=_required_text(
                    settings,
                    "plugin_module_source_github_app_template_name",
                    step="module_source_config",
                ),
                agents_revision=_required_text(
                    settings,
                    "plugin_module_source_github_app_template_agents_revision",
                    step="module_source_config",
                ),
                security_revision=_required_text(
                    settings,
                    "plugin_module_source_github_app_template_security_revision",
                    step="module_source_config",
                ),
            ),
        )
        trust = ModuleSourceTrustConfig(
            installation_credential_ref=_required_text(
                settings,
                "plugin_module_source_installation_credential_ref",
                step="module_source_config",
            ),
            trusted_workflow_ref=_required_text(
                settings,
                "plugin_module_source_trusted_workflow_ref",
                step="module_source_config",
            ),
        )
        trust.validate()
        default_branch = _required_text(
            settings,
            "plugin_module_source_default_branch",
            step="module_source_config",
        )
        validate_branch(default_branch)
        review_count = getattr(
            settings,
            "plugin_module_source_required_approving_review_count",
            None,
        )
        branch_protection = BranchProtectionPolicy(
            required_status_checks=_status_checks(settings),
            required_approving_review_count=review_count,
        )
        return ModuleSourceProvisioningConfig(
            organization=organization,
            trust=trust,
            branch_protection=branch_protection,
            default_branch=default_branch,
        )
    except PluginCompositionError:
        raise
    except Exception:
        # Model errors can echo their input. Do not chain or log them: a bad
        # value may be a credential REF or accidentally supplied key material.
        raise PluginCompositionError(
            "module_source_config",
            "managed organization, repository template, workflow REF, credential REF, "
            "repository grammar, or branch protection is invalid",
        ) from None


def _default_secret_provider() -> SecretStoreGitHubAppSecretProvider:
    try:
        from prodavan.infrastructure.secrets.store import get_secret_store

        return SecretStoreGitHubAppSecretProvider(get_secret_store())
    except PluginCompositionError:
        raise
    except Exception:
        raise PluginCompositionError(
            "github_app_secret_provider",
            "the existing SecretStore could not be composed for GitHub App authentication",
        ) from None


def github_app_client_from_settings(
    settings: Any,
    config: ModuleSourceProvisioningConfig,
    *,
    secret_provider: Any = None,
    transport: Any = None,
    clock: Any = None,
) -> GitHubAppClient:
    """Construct the real client with a private-key REF, never plaintext."""

    app_id = _positive_int(
        settings,
        "plugin_module_source_github_app_id",
        step="github_app_client",
    )
    private_key_secret_ref = _required_text(
        settings,
        "plugin_module_source_github_app_private_key_secret_ref",
        step="github_app_private_key",
    )
    try:
        validate_secret_reference(private_key_secret_ref)
    except Exception:
        raise PluginCompositionError(
            "github_app_private_key",
            "PLUGIN_MODULE_SOURCE_GITHUB_APP_PRIVATE_KEY_SECRET_REF must be a secret REF, "
            "not inline private-key material",
        ) from None
    provider = secret_provider or _default_secret_provider()
    if not callable(getattr(provider, "read_secret", None)):
        raise PluginCompositionError(
            "github_app_secret_provider",
            "secret_provider must implement async read_secret()",
        )
    kwargs: dict[str, Any] = {
        "app_id": app_id,
        "private_key_secret_ref": private_key_secret_ref,
        "secret_provider": provider,
        "organizations": (config.organization,),
        "timeout_seconds": getattr(
            settings,
            "plugin_module_source_github_app_timeout_seconds",
            10.0,
        ),
    }
    if transport is not None:
        kwargs["transport"] = transport
    if clock is not None:
        kwargs["clock"] = clock
    try:
        return GitHubAppClient(**kwargs)
    except Exception:
        raise PluginCompositionError(
            "github_app_client",
            "GitHubAppClient configuration failed closed; verify typed app id, "
            "managed organization and secret-provider settings",
        ) from None


def build_plugin_ci_approval_reader(session_factory: Any) -> Any:
    """Build the durable approval reader only when CI publication is enabled."""

    try:
        from prodavan.infrastructure.persistence.repositories.plugins.publication_approvals import (
            SqlPluginPublicationApprovalRepository,
        )
    except (ImportError, ModuleNotFoundError, AttributeError):
        raise PluginCompositionError(
            "ci_approval",
            "enabled CI publication requires the durable PublicationApproval repository; "
            "ACTIVE grant fallback is forbidden",
        ) from None
    try:
        return SqlPluginPublicationApprovalRepository(session_factory=session_factory)
    except Exception:
        raise PluginCompositionError(
            "ci_approval",
            "enabled CI publication could not construct its shared-session approval reader",
        ) from None


def _restore_state(state: Any, before: Mapping[str, tuple[bool, Any]]) -> None:
    for key, (existed, value) in reversed(tuple(before.items())):
        if existed:
            setattr(state, key, value)
        elif hasattr(state, key):
            delattr(state, key)


def _close_unregistered_client(client: GitHubAppClient) -> None:
    """Best-effort async cleanup when synchronous composition fails."""

    try:
        result = client.aclose()
        if not inspect.isawaitable(result):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(result)
        else:
            loop.create_task(result)
    except Exception:
        # Preserve the sanitized composition failure and never log key material.
        return


def install_module_source_composition(
    app: Any,
    settings: Any,
    *,
    lifespan_manager: Any,
    overrides: Mapping[str, Any] | None = None,
) -> ModuleSourceComposition:
    """Install source factory and one lifecycle-owned client, transactionally."""

    state = getattr(app, "state", None)
    if state is None:
        raise PluginCompositionError("module_source_state", "app has no .state")
    existing = getattr(state, MODULE_SOURCE_COMPOSITION_STATE, None)
    if existing is not None:
        if not isinstance(existing, ModuleSourceComposition):
            raise PluginCompositionError(
                "module_source_state",
                f"app.state.{MODULE_SOURCE_COMPOSITION_STATE} has an unexpected type",
            )
        install_module_source_service_factory(
            app,
            make_module_source_service_factory(
                provider=existing.github_app,
                config=existing.config,
            ),
        )
        return existing
    if not callable(getattr(lifespan_manager, "register", None)):
        raise PluginCompositionError(
            "module_source_lifecycle",
            "source composition requires the application lifespan manager",
        )

    ov = dict(overrides or {})
    config = ov.get("source_config") or managed_config_from_settings(settings)
    if not isinstance(config, ModuleSourceProvisioningConfig):
        raise PluginCompositionError(
            "module_source_config",
            "source_config must be ModuleSourceProvisioningConfig",
        )
    config.trust.validate()
    validate_branch(config.default_branch)
    shared_client = ov.get("github_app") or github_app_client_from_settings(
        settings,
        config,
        secret_provider=ov.get("secret_provider"),
        transport=ov.get("transport"),
        clock=ov.get("clock"),
    )
    if not isinstance(shared_client, GitHubAppClient):
        raise PluginCompositionError(
            "github_app_client",
            "the shared provider must be the real GitHubAppClient",
        )
    try:
        provider_organization = shared_client._organization(config.organization.name)
    except Exception:
        raise PluginCompositionError(
            "github_app_client",
            "the shared client does not allow the configured managed organization",
        ) from None
    if provider_organization != config.organization:
        raise PluginCompositionError(
            "github_app_client",
            "the shared client organization/installation/template binding differs from source config",
        )

    before = {
        key: (hasattr(state, key), getattr(state, key, None))
        for key in _STATE_KEYS
    }
    composition = ModuleSourceComposition(github_app=shared_client, config=config)
    registered = False
    try:
        setattr(state, MODULE_SOURCE_GITHUB_APP_STATE, shared_client)
        setattr(state, MODULE_SOURCE_CONFIG_STATE, config)
        setattr(state, MODULE_SOURCE_COMPOSITION_STATE, composition)
        install_module_source_service_factory(
            app,
            make_module_source_service_factory(
                provider=shared_client,
                config=config,
            ),
        )
        lifespan_manager.register(_ModuleSourceGitHubLifecycle(shared_client))
        registered = True
        return composition
    except PluginCompositionError:
        if not registered:
            _restore_state(state, before)
            _close_unregistered_client(shared_client)
        raise
    except Exception:
        if not registered:
            _restore_state(state, before)
            _close_unregistered_client(shared_client)
        raise PluginCompositionError(
            "module_source_lifecycle",
            "source composition failed; app.state was restored and the owned client was released",
        ) from None


__all__ = [
    "MODULE_SOURCE_COMPOSITION_STATE",
    "MODULE_SOURCE_CONFIG_STATE",
    "MODULE_SOURCE_GITHUB_APP_STATE",
    "MODULE_SOURCE_LIFESPAN_NAME",
    "ModuleSourceComposition",
    "SecretStoreGitHubAppSecretProvider",
    "build_plugin_ci_approval_reader",
    "github_app_client_from_settings",
    "install_module_source_composition",
    "managed_config_from_settings",
    "module_source_enabled",
]
