"""Tenant-scoped content-addressed bytes for digest-only capability contracts.

Raw payload bytes never belong in logs, errors, metrics, or typed audit responses.
The Redis value is base64 so the existing decode_responses=True manager can
round-trip arbitrary bytes; the digest is always SHA-256 of the original bytes.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


DEFAULT_MAX_PAYLOAD_BYTES = 1024 * 1024
DEFAULT_PAYLOAD_TTL_SECONDS = 300
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_PAYLOAD_TTL_SECONDS = 24 * 60 * 60
_KEY_PREFIX = "plugin:capability:payload:v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_NAMESPACE_DOMAIN = b"agentscale/plugin-capability-payload/v1\x00"


class DigestPayloadError(RuntimeError):
    """Safe typed failure; messages never contain payload or tenant input."""

    code = "digest_payload_error"

    def __init__(self, code: str | None = None) -> None:
        self.code = code or type(self).code
        super().__init__(self.code)


class DigestPayloadValidationError(DigestPayloadError):
    code = "digest_payload_validation_failed"


class DigestPayloadTooLargeError(DigestPayloadValidationError):
    code = "digest_payload_oversize"


class DigestPayloadIntegrityError(DigestPayloadError):
    code = "digest_payload_integrity_failed"


class DigestPayloadBackendError(DigestPayloadError):
    code = "digest_payload_backend_failed"


class DigestPayloadClosedError(DigestPayloadError):
    code = "digest_payload_store_closed"


class DigestPayloadResolverNotConfiguredError(DigestPayloadError):
    code = "network_payload_resolver_not_configured"


@dataclass(frozen=True, slots=True)
class DigestPayloadStoreConfig:
    """Hard bounds for one payload and its retry lifetime."""

    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    ttl_seconds: int = DEFAULT_PAYLOAD_TTL_SECONDS

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_payload_bytes, int)
            or isinstance(self.max_payload_bytes, bool)
            or not 1 <= self.max_payload_bytes <= MAX_PAYLOAD_BYTES
        ):
            raise DigestPayloadValidationError("digest_payload_max_bytes_invalid")
        if (
            not isinstance(self.ttl_seconds, int)
            or isinstance(self.ttl_seconds, bool)
            or not 1 <= self.ttl_seconds <= MAX_PAYLOAD_TTL_SECONDS
        ):
            raise DigestPayloadValidationError("digest_payload_ttl_invalid")


@runtime_checkable
class BodyDigestResolver(Protocol):
    """The narrow contract consumed by the network capability adapter."""

    async def resolve_body_digest(
        self,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        digest: str,
    ) -> bytes | None: ...


class DigestPayloadStore:
    """Redis-backed content-addressed payloads scoped to one plugin instance.

    put uses only atomic SET NX EX. A lost NX race is accepted only after
    reading and revalidating the existing value. get returns None for a
    missing key, but malformed digests, oversize values, and digest mismatches
    are typed failures. release removes a retry payload after revalidation.
    The shared Redis manager/client is not owned or closed by this store.
    """

    def __init__(
        self,
        *,
        client: Any,
        config: DigestPayloadStoreConfig | None = None,
    ) -> None:
        if client is None or not all(
            callable(getattr(client, method, None)) for method in ("get", "set", "delete")
        ):
            raise DigestPayloadValidationError("digest_payload_redis_client_required")
        if config is not None and not isinstance(config, DigestPayloadStoreConfig):
            raise DigestPayloadValidationError("digest_payload_config_invalid")
        self._client: Any = client
        self._config = config or DigestPayloadStoreConfig()
        self._closed = False

    @property
    def config(self) -> DigestPayloadStoreConfig:
        return self._config

    @property
    def closed(self) -> bool:
        return self._closed

    @staticmethod
    def _digest(payload: bytes) -> str:
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _identifier(value: str) -> bytes:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value.encode("utf-8")) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise DigestPayloadValidationError("digest_payload_namespace_invalid")
        return value.encode("utf-8")

    @staticmethod
    def _validated_digest(digest: str) -> str:
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise DigestPayloadValidationError("digest_payload_digest_invalid")
        return digest

    def _key(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        digest: str,
    ) -> str:
        checked_digest = self._validated_digest(digest)
        parts = (
            self._identifier(company_id),
            self._identifier(plugin_id),
            self._identifier(plugin_instance_id),
        )
        canonical_namespace = b"".join(
            len(part).to_bytes(4, "big") + part for part in parts
        )
        namespace = hashlib.sha256(
            _NAMESPACE_DOMAIN + canonical_namespace
        ).hexdigest()
        return f"{_KEY_PREFIX}:{namespace}:{checked_digest.removeprefix('sha256:')}"

    def _ensure_open(self) -> None:
        if self._closed or self._client is None:
            raise DigestPayloadClosedError()

    def _decode(self, value: Any) -> bytes:
        if isinstance(value, str):
            try:
                encoded = value.encode("ascii")
            except UnicodeEncodeError:
                raise DigestPayloadIntegrityError("digest_payload_encoding_invalid") from None
        elif isinstance(value, bytes):
            encoded = value
        else:
            raise DigestPayloadIntegrityError("digest_payload_encoding_invalid")
        # Reject an invalid/oversized Redis value before base64 allocation.
        if len(encoded) > 4 * ((self._config.max_payload_bytes + 2) // 3):
            raise DigestPayloadTooLargeError("digest_payload_stored_oversize")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise DigestPayloadIntegrityError("digest_payload_encoding_invalid") from None
        if len(payload) > self._config.max_payload_bytes:
            raise DigestPayloadTooLargeError("digest_payload_stored_oversize")
        return payload

    async def _read_key(self, key: str, digest: str) -> bytes | None:
        self._ensure_open()
        try:
            value = await self._client.get(key)
        except Exception:
            raise DigestPayloadBackendError("digest_payload_get_failed") from None
        if value is None:
            return None
        payload = self._decode(value)
        actual_digest = self._digest(payload)
        if not hmac.compare_digest(actual_digest, digest):
            raise DigestPayloadIntegrityError()
        return payload

    async def put(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        payload: bytes,
    ) -> str:
        """Store exact bytes and return their computed canonical SHA-256 digest."""
        self._ensure_open()
        if not isinstance(payload, bytes):
            raise DigestPayloadValidationError("digest_payload_bytes_required")
        if len(payload) > self._config.max_payload_bytes:
            raise DigestPayloadTooLargeError()
        digest = self._digest(payload)
        key = self._key(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            digest=digest,
        )
        encoded = base64.b64encode(payload).decode("ascii")
        # A concurrent expiry between NX and GET may require one bounded retry.
        for _attempt in range(2):
            self._ensure_open()
            try:
                result = await self._client.set(
                    key,
                    encoded,
                    nx=True,
                    ex=self._config.ttl_seconds,
                )
            except Exception:
                raise DigestPayloadBackendError("digest_payload_put_failed") from None
            if result is True or (type(result) is int and result == 1):
                return digest
            if result is not None and result is not False and not (
                type(result) is int and result == 0
            ):
                raise DigestPayloadBackendError("digest_payload_set_nx_not_confirmed")
            existing = await self._read_key(key, digest)
            if existing is not None:
                return digest
        raise DigestPayloadBackendError("digest_payload_write_race")

    async def get(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        digest: str,
    ) -> bytes | None:
        """Return exact bytes, or None only when the scoped key is absent."""
        self._ensure_open()
        key = self._key(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            digest=digest,
        )
        return await self._read_key(key, digest)

    async def exists(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        digest: str,
    ) -> bool:
        """Existence includes digest revalidation; corrupted data never exists."""
        return await self.get(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            digest=digest,
        ) is not None

    async def release(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        digest: str,
    ) -> bool:
        """Delete a validated value so a failed attempt can be retried safely."""
        key = self._key(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            digest=digest,
        )
        if await self._read_key(key, digest) is None:
            return False
        self._ensure_open()
        try:
            result = await self._client.delete(key)
        except Exception:
            raise DigestPayloadBackendError("digest_payload_release_failed") from None
        if isinstance(result, bool):
            return result
        if type(result) is int and result in (0, 1):
            return bool(result)
        raise DigestPayloadBackendError("digest_payload_delete_not_confirmed")

    async def aclose(self) -> None:
        """Close this handle once; the lifespan-owned Redis client stays shared."""
        if self._closed:
            return
        self._closed = True
        self._client = None


class DigestPayloadResolver:
    """Adapt the typed store to the network adapter positional contract."""

    __slots__ = ("_store",)

    def __init__(self, *, store: DigestPayloadStore) -> None:
        if not isinstance(store, DigestPayloadStore):
            raise DigestPayloadResolverNotConfiguredError()
        self._store = store

    async def resolve_body_digest(
        self,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        digest: str,
    ) -> bytes | None:
        # Missing is deliberately None. The network adapter must turn a missing
        # non-empty digest into a typed denial, never substitute b"".
        return await self._store.get(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            digest=digest,
        )


@dataclass(frozen=True, slots=True)
class DigestPayloadResolverFactory:
    """Publish one store while allowing each network composition to get a resolver."""

    store: DigestPayloadStore

    def __post_init__(self) -> None:
        if not isinstance(self.store, DigestPayloadStore):
            raise DigestPayloadResolverNotConfiguredError()

    def __call__(self) -> DigestPayloadResolver:
        return DigestPayloadResolver(store=self.store)

    def create(self) -> DigestPayloadResolver:
        return self()


def require_body_digest_resolver(
    resolver: BodyDigestResolver | None,
) -> BodyDigestResolver:
    """Fail closed at network composition instead of accepting an empty body."""
    if resolver is None or not callable(getattr(resolver, "resolve_body_digest", None)):
        raise DigestPayloadResolverNotConfiguredError()
    return resolver


__all__ = [
    "BodyDigestResolver",
    "DEFAULT_MAX_PAYLOAD_BYTES",
    "DEFAULT_PAYLOAD_TTL_SECONDS",
    "DigestPayloadBackendError",
    "DigestPayloadClosedError",
    "DigestPayloadError",
    "DigestPayloadIntegrityError",
    "DigestPayloadResolver",
    "DigestPayloadResolverFactory",
    "DigestPayloadResolverNotConfiguredError",
    "DigestPayloadStore",
    "DigestPayloadStoreConfig",
    "DigestPayloadTooLargeError",
    "DigestPayloadValidationError",
    "MAX_PAYLOAD_BYTES",
    "MAX_PAYLOAD_TTL_SECONDS",
    "require_body_digest_resolver",
]
