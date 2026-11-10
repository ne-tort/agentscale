"""Private OCI registry authentication adapters."""

from .ghcr_auth import (
    GHCR_HOST,
    GHCR_ORIGIN,
    REGISTRY_READ_PROFILE,
    GhcrAuthConfigurationError,
    GhcrAuthError,
    GhcrAuthorizerClosedError,
    GhcrInstallationTokenProvider,
    GhcrRegistryAuthorizer,
    GhcrRepositoryError,
    GhcrTokenError,
    GhcrTokenProfile,
)

__all__ = [
    "GHCR_HOST",
    "GHCR_ORIGIN",
    "REGISTRY_READ_PROFILE",
    "GhcrAuthConfigurationError",
    "GhcrAuthError",
    "GhcrAuthorizerClosedError",
    "GhcrInstallationTokenProvider",
    "GhcrRegistryAuthorizer",
    "GhcrRepositoryError",
    "GhcrTokenError",
    "GhcrTokenProfile",
]
