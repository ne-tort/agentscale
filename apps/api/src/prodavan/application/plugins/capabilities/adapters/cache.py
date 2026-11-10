"""Redis-backed typed cache capability with mandatory tenant key rewriting."""
from __future__ import annotations

from typing import Any

from prodavan.application.plugins.capabilities.contracts import (
    CacheDeleteRequest, CacheDeleteResponse, CacheGetRequest, CacheGetResponse,
    CacheIncrRequest, CacheIncrResponse, CachePublishRequest, CachePublishResponse,
    CacheSetRequest, CacheSetResponse, empty_digest,
)
from ._shared import (
    CloseOnce, await_io, canonical_digest, digest_text, plugin_id,
    request_envelope, require_safe_text, usage, CapabilityAccessDenied,
    CapabilityBackendError, CapabilityConfigurationError,
)

_INCR_WITH_TTL = """
local value = redis.call('INCRBY', KEYS[1], ARGV[1])
if value == tonumber(ARGV[1]) then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return value
"""


class RedisCacheAdapter(CloseOnce):
    """Real Redis IO; no silent disabled or in-memory fallback."""

    def __init__(
        self,
        *,
        client: Any,
        ttl_seconds: int = 300,
        max_value_bytes: int = 65536,
        max_key_bytes: int = 384,
        timeout_seconds: float = 2.0,
        owns_client: bool = False,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            owned_resources=(client,) if owns_client else (),
        )
        if client is None:
            raise CapabilityConfigurationError("redis_client_required")
        for method in ("get", "set", "delete", "eval", "publish"):
            if not callable(getattr(client, method, None)):
                raise CapabilityConfigurationError("redis_client_contract_invalid")
        if not 1 <= ttl_seconds <= 3600:
            raise CapabilityConfigurationError("redis_ttl_invalid")
        if not 1 <= max_value_bytes <= 1024 * 1024:
            raise CapabilityConfigurationError("redis_value_cap_invalid")
        self._client = client
        self._ttl = ttl_seconds
        self._max_value_bytes = max_value_bytes
        self._max_key_bytes = max_key_bytes

    def _key(self, envelope: Any, logical_key: str) -> str:
        key = require_safe_text(
            logical_key, code="cache_key_invalid", max_bytes=self._max_key_bytes
        )
        if key.startswith("tenant:"):
            raise CapabilityAccessDenied("cache_key_must_be_logical")
        return (
            f"tenant:{envelope.company_id}:plugin:{plugin_id(envelope)}:"
            f"{envelope.plugin_instance_id}:key:{key}"
        )

    def _channel(self, envelope: Any, logical_channel: str) -> str:
        channel = require_safe_text(
            logical_channel, code="cache_channel_invalid", max_bytes=self._max_key_bytes
        )
        if channel.startswith("tenant:"):
            raise CapabilityAccessDenied("cache_channel_must_be_logical")
        return (
            f"tenant:{envelope.company_id}:plugin:{plugin_id(envelope)}:"
            f"{envelope.plugin_instance_id}:channel:{channel}"
        )

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self._ensure_open()
        return await await_io(
            getattr(self._client, method)(*args, **kwargs),
            timeout=self._timeout_seconds,
            code=f"redis_{method}_failed",
        )

    def quota_usage(self, request: Any) -> Any:
        if isinstance(request, CacheSetRequest):
            return usage(keys=1, bytes=len(request.value.encode("utf-8")))
        if isinstance(request, CacheGetRequest):
            return usage(keys=1, bytes=self._max_value_bytes)
        if isinstance(request, CachePublishRequest):
            return usage(keys=1, bytes=len(request.message_digest.encode("utf-8")))
        if isinstance(request, (CacheDeleteRequest, CacheIncrRequest)):
            return usage(keys=1)
        raise CapabilityAccessDenied("typed_request_required")

    async def get(self, request: CacheGetRequest) -> CacheGetResponse:
        envelope = request_envelope(request, CacheGetRequest)
        value = await self._call("get", self._key(envelope, request.key))
        if value is not None and not isinstance(value, (str, bytes)):
            raise CapabilityBackendError("redis_get_invalid_result")
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                raise CapabilityBackendError("redis_value_not_utf8") from None
        found = value is not None
        value_digest = digest_text(value) if found else empty_digest()
        return CacheGetResponse(
            request_id=envelope.request_id, ok=True, digest=value_digest,
            found=found, value_digest=value_digest,
        )

    async def set(self, request: CacheSetRequest) -> CacheSetResponse:
        envelope = request_envelope(request, CacheSetRequest)
        size = len(request.value.encode("utf-8"))
        if size > self._max_value_bytes:
            raise CapabilityAccessDenied("cache_value_too_large")
        stored = await self._call(
            "set", self._key(envelope, request.key), request.value, ex=self._ttl
        )
        if stored is not True and stored != 1 and str(stored).upper() != "OK":
            raise CapabilityBackendError("redis_set_not_confirmed")
        return CacheSetResponse(
            request_id=envelope.request_id, ok=True,
            digest=digest_text(request.value), stored_bytes=size,
        )

    async def delete(self, request: CacheDeleteRequest) -> CacheDeleteResponse:
        envelope = request_envelope(request, CacheDeleteRequest)
        key = self._key(envelope, request.key)
        deleted = int(await self._call("delete", key))
        return CacheDeleteResponse(
            request_id=envelope.request_id, ok=True,
            digest=canonical_digest({"key": key, "deleted": deleted > 0}),
            deleted=deleted > 0,
        )

    async def incr(self, request: CacheIncrRequest) -> CacheIncrResponse:
        envelope = request_envelope(request, CacheIncrRequest)
        key = self._key(envelope, request.key)
        value = int(await self._call(
            "eval", _INCR_WITH_TTL, 1, key, request.amount, self._ttl
        ))
        return CacheIncrResponse(
            request_id=envelope.request_id, ok=True,
            digest=canonical_digest({"key": key, "value": value}), value=value,
        )

    async def publish(self, request: CachePublishRequest) -> CachePublishResponse:
        envelope = request_envelope(request, CachePublishRequest)
        channel = self._channel(envelope, request.channel)
        subscribers = int(await self._call("publish", channel, request.message_digest))
        return CachePublishResponse(
            request_id=envelope.request_id, ok=True,
            digest=canonical_digest({"channel": channel, "message_digest": request.message_digest}),
            subscribers=subscribers,
        )


__all__ = ["RedisCacheAdapter"]
