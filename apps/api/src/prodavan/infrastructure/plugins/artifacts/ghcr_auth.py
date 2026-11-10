"""Fail-closed GHCR authentication backed by GitHub App installation tokens.

The authorizer deliberately owns no token cache, refresh task or expiry clock.
Every request delegates to the GitHub App provider, whose existing installation
token path supplies per-profile singleflight, server TTL and refresh skew.

Provider integration seam
-------------------------
The provider patch must expose this public method on ``GitHubAppClient`` and
keep its implementation as a thin delegate to the existing private token path::

    async def installation_token(self, installation_id: int, *, profile: str) -> str:
        typed_profile = GitHubInstallationTokenProfile(profile)
        return await self._installation_token(installation_id, typed_profile)

``GhcrRegistryAuthorizer`` calls it only with ``profile="registry_read"``.
The corresponding ``GitHubInstallationTokenProfile.REGISTRY_READ`` value must
request exactly ``{"packages": "read", "metadata": "read"}``. No broader
provisioning/authoring credential is accepted or requested here.

The application ``RegistryAuthorizerPort`` passes only a repository path. URL
security is therefore intentionally compositional: this adapter is exclusively
for ``HttpxOciArtifactLoader`` with its default ``allowed_registries=("ghcr.io",)``.
The loader constructs HTTPS URLs itself, rejects other registry hosts and keeps
``trust_env=False``/``follow_redirects=False``. Those settings must not be
broadened when wiring this authorizer.
"""

from __future__ import annotations

import base64
from typing import Final, Literal, Protocol, TypeAlias

from prodavan.application.plugins.artifacts.oci import parse_oci_ref
from prodavan.application.plugins.artifacts.publication import RegistryAuthorizerPort
from prodavan.infrastructure.plugins.sources.models import validate_owner

GHCR_HOST: Final = "ghcr.io"
GHCR_ORIGIN: Final = f"https://{GHCR_HOST}"
GhcrTokenProfile: TypeAlias = Literal["registry_read"]
REGISTRY_READ_PROFILE: Final[GhcrTokenProfile] = "registry_read"

_ZERO_DIGEST: Final = "sha256:" + "0" * 64
_MAX_TOKEN_LENGTH: Final = 8192


class GhcrInstallationTokenProvider(Protocol):
    """Narrow public seam over the GitHub App client token lifecycle.

    Implementations must route every call through their common installation
    token singleflight/TTL cache. This protocol does not permit a static token,
    an inline private key or a second cache in the GHCR adapter.
    """

    async def installation_token(
        self,
        installation_id: int,
        *,
        profile: GhcrTokenProfile,
    ) -> str: ...


class GhcrAuthError(RuntimeError):
    """Base sanitized GHCR authentication failure."""


class GhcrAuthConfigurationError(GhcrAuthError):
    """Authentication is absent or the trusted integration is invalid."""


class GhcrRepositoryError(GhcrAuthError):
    """A repository is malformed or outside the configured organization."""


class GhcrTokenError(GhcrAuthError):
    """A registry-read token could not be acquired without exposing details."""


class GhcrAuthorizerClosedError(GhcrAuthError):
    """An operation was attempted after authorizer shutdown."""


class GhcrRegistryAuthorizer(RegistryAuthorizerPort):
    """Produce per-request GHCR Basic authorization from an installation token.

    ``organization`` and ``installation_id`` are trusted, non-secret source
    configuration. The organization binding prevents a caller-selected OCI path
    from turning this adapter into a cross-organization credential deputy.

    The provider is borrowed, not owned: ``aclose`` disables this authorizer but
    does not close the shared GitHub App client or cancel its shared token mint.
    The composition root remains responsible for closing that provider.
    """

    def __init__(
        self,
        *,
        organization: str,
        installation_id: int,
        token_provider: GhcrInstallationTokenProvider | None = None,
    ) -> None:
        if token_provider is None or not callable(
            getattr(token_provider, "installation_token", None)
        ):
            raise GhcrAuthConfigurationError(
                "GHCR registry_read token provider is not configured"
            )
        if (
            isinstance(installation_id, bool)
            or not isinstance(installation_id, int)
            or installation_id <= 0
        ):
            raise GhcrAuthConfigurationError(
                "GHCR installation_id must be a positive integer"
            )
        try:
            validated_organization = validate_owner(organization)
        except (TypeError, ValueError):
            validated_organization = None
        if validated_organization is None:
            raise GhcrAuthConfigurationError(
                "GHCR organization must be a canonical GitHub organization"
            )

        self._organization = validated_organization.casefold()
        self._installation_id = installation_id
        self._token_provider = token_provider
        self._closed = False

    async def __aenter__(self) -> GhcrRegistryAuthorizer:
        self._ensure_open()
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        await self.aclose()

    async def authorization_for(self, repository: str) -> str:
        """Return only an Authorization value; never return anonymous access.

        GHCR expects HTTP Basic with the fixed username ``x-access-token`` and
        the installation token as password. Base64 is an encoding for the Basic
        scheme, not persistence or logging; the value exists only for this call.
        """

        self._ensure_open()
        self._validate_repository(repository)

        token: object = None
        try:
            token = await self._token_provider.installation_token(
                self._installation_id,
                profile=REGISTRY_READ_PROFILE,
            )
        except Exception:
            # Do not chain provider exceptions: their messages, HTTP requests or
            # tracebacks can contain credentials, private keys or upstream bodies.
            token = None

        self._ensure_open()
        if not self._is_safe_token(token):
            # Raised outside the except block so even direct exception inspection
            # cannot recover the provider error through ``__context__``.
            raise GhcrTokenError(
                "GHCR registry_read token could not be acquired safely"
            )

        basic_credentials = f"x-access-token:{token}".encode("ascii")
        encoded = base64.b64encode(basic_credentials).decode("ascii")
        return f"Basic {encoded}"

    async def aclose(self) -> None:
        """Disable future use without shutting down the borrowed provider."""

        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise GhcrAuthorizerClosedError("GHCR registry authorizer is closed")

    def _validate_repository(self, repository: object) -> None:
        parsed = None
        if isinstance(repository, str) and repository:
            try:
                # Reuse the application OCI grammar rather than accepting URLs,
                # hosts, tags, digest suffixes, backslashes or encoded paths.
                parsed = parse_oci_ref(f"{GHCR_HOST}/{repository}@{_ZERO_DIGEST}")
            except (TypeError, ValueError):
                parsed = None
        if (
            parsed is None
            or parsed.registry != GHCR_HOST
            or parsed.repository != repository
        ):
            raise GhcrRepositoryError("GHCR repository path is invalid")

        owner, separator, _ = repository.partition("/")
        if not separator or owner.casefold() != self._organization:
            raise GhcrRepositoryError(
                "GHCR repository is outside the configured organization"
            )

    @staticmethod
    def _is_safe_token(token: object) -> bool:
        return (
            isinstance(token, str)
            and 0 < len(token) <= _MAX_TOKEN_LENGTH
            and all(33 <= ord(character) <= 126 for character in token)
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
