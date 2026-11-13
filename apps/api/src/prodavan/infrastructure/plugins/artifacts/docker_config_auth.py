"""Fail-closed GHCR auth from an explicitly mounted Docker config JSON.

This is a narrow fallback for plugin-static development composition when the
full GitHub App contour is absent. The existing ``auth`` value is returned as
an HTTP Basic header; ``username``/``password`` fields and credential helpers
are never used as substitutes. The file is read for every authorization call,
so credentials are neither cached by this adapter nor written to logs/env.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Final

from prodavan.application.plugins.artifacts.oci import parse_oci_ref
from prodavan.application.plugins.artifacts.publication import RegistryAuthorizerPort
from prodavan.infrastructure.plugins.artifacts.ghcr_auth import (
    GHCR_HOST,
    GhcrAuthConfigurationError,
    GhcrAuthorizerClosedError,
    GhcrRepositoryError,
)

GHCR_DOCKER_CONFIG_ORGANIZATION: Final = "ne-tort"
_ZERO_DIGEST: Final = "sha256:" + "0" * 64
_MAX_CONFIG_BYTES: Final = 64 * 1024
_MAX_CREDENTIAL_BYTES: Final = 8192
_CONFIG_ERROR: Final = "GHCR docker config authorization is invalid or unavailable"


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


class GhcrDockerConfigAuthorizer(RegistryAuthorizerPort):
    """Read only the single ``ghcr.io`` Basic credential from a mounted file."""

    __slots__ = ("_closed", "_path")

    def __init__(self, *, config_path: str | Path) -> None:
        if not isinstance(config_path, (str, Path)):
            raise GhcrAuthConfigurationError(_CONFIG_ERROR)
        raw_path = str(config_path)
        path = Path(raw_path)
        if (
            not raw_path
            or raw_path != raw_path.strip()
            or "\x00" in raw_path
            or not path.is_absolute()
        ):
            raise GhcrAuthConfigurationError(_CONFIG_ERROR)
        self._path = path
        self._closed = False

    async def __aenter__(self) -> GhcrDockerConfigAuthorizer:
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
        """Return a Basic header only after repository and config validation."""
        self._ensure_open()
        self._validate_repository(repository)
        try:
            # Secret-file I/O and parsing stay off the event loop. No credential
            # is retained on self; a rotated projected Secret is read next call.
            authorization = await asyncio.to_thread(self._read_authorization)
        except Exception:
            # Parsing, OS and base64 errors can contain paths or file contents.
            # Discard both the error and its chain before raising a fixed error.
            authorization = None
        self._ensure_open()
        if authorization is None:
            raise GhcrAuthConfigurationError(_CONFIG_ERROR) from None
        return authorization

    async def aclose(self) -> None:
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise GhcrAuthorizerClosedError("GHCR registry authorizer is closed")

    def _read_authorization(self) -> str:
        with self._path.open("rb") as stream:
            payload = stream.read(_MAX_CONFIG_BYTES + 1)
        if not payload or len(payload) > _MAX_CONFIG_BYTES:
            raise ValueError("invalid docker config size")
        document = json.loads(
            payload,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
        if not isinstance(document, dict) or set(document) != {"auths"}:
            raise ValueError("docker config must contain only auths")
        auths = document["auths"]
        if not isinstance(auths, dict) or len(auths) != 1:
            raise ValueError("docker config must contain exactly one registry")
        registry, entry = next(iter(auths.items()))
        if registry != GHCR_HOST or not isinstance(entry, dict) or set(entry) != {"auth"}:
            raise ValueError("only the ghcr.io auth entry is accepted")
        encoded = entry["auth"]
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("docker auth must be a non-empty string")
        decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
        username, separator, password = decoded.partition(b":")
        if (
            not separator
            or not username
            or not password
            or len(decoded) > _MAX_CREDENTIAL_BYTES
            or any(byte < 32 or byte == 127 for byte in decoded)
        ):
            raise ValueError("docker auth is not valid Basic credentials")
        # Preserve the existing credential exactly; do not construct auth from
        # username/password fields and do not decode/re-encode it into state.
        return f"Basic {encoded}"

    @staticmethod
    def _validate_repository(repository: object) -> None:
        parsed = None
        if isinstance(repository, str) and repository:
            try:
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
        if not separator or owner != GHCR_DOCKER_CONFIG_ORGANIZATION:
            raise GhcrRepositoryError(
                "GHCR repository is outside the ne-tort organization"
            )


__all__ = ["GHCR_DOCKER_CONFIG_ORGANIZATION", "GhcrDockerConfigAuthorizer"]
