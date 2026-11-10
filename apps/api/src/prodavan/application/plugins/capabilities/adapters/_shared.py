"""Shared fail-closed primitives for production capability adapters.

Only metadata and canonical SHA-256 digests cross this layer. Backend exception
text is never propagated: it can contain DSNs, URLs, credentials or payload.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Awaitable, Iterable
from typing import Any, TypeVar

from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    TypedResponse,
)
from prodavan.application.plugins.capabilities.quotas import QuotaUsage



class CapabilityAdapterError(RuntimeError):
    """Sanitized adapter failure; ``code`` is safe for diagnostics."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CapabilityConfigurationError(CapabilityAdapterError):
    """A required real backend or security setting is absent."""


class CapabilityAccessDenied(CapabilityAdapterError):
    """The request attempted to escape its server-bound tenant scope."""


class CapabilityBackendError(CapabilityAdapterError):
    """External IO failed or returned an invalid/untrustworthy result."""


T = TypeVar("T")


def usage(
    *,
    rows: int = 0,
    bytes: int = 0,
    keys: int = 0,
    object_bytes: int = 0,
    egress_bytes: int = 0,
) -> QuotaUsage:
    return QuotaUsage(
        ops=1,
        rows=rows,
        bytes=bytes,
        keys=keys,
        object_bytes=object_bytes,
        egress_bytes=egress_bytes,
    )


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise CapabilityBackendError("response_not_canonicalizable") from None


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_text(value: str) -> str:
    return digest_bytes(value.encode("utf-8"))


def canonical_digest(value: Any) -> str:
    return digest_bytes(canonical_json(value))


def request_envelope(request: Any, expected_type: type) -> CapabilityEnvelope:
    if not isinstance(request, expected_type):
        raise CapabilityAccessDenied("typed_request_required")
    envelope = getattr(request, "envelope", None)
    if not isinstance(envelope, CapabilityEnvelope):
        raise CapabilityAccessDenied("server_envelope_required")
    return envelope


def plugin_id(envelope: CapabilityEnvelope) -> str:
    """Recover plugin_id from the server-minted subject and cross-check it."""
    parts = envelope.subject_id.split(":")
    if (
        len(parts) != 4
        or parts[0] != "plugin"
        or not parts[1]
        or parts[2] != envelope.company_id
        or parts[3] != envelope.plugin_instance_id
    ):
        raise CapabilityAccessDenied("plugin_subject_binding_invalid")
    return parts[1]


def require_same(actual: Any, expected: Any, code: str) -> None:
    if actual != expected:
        raise CapabilityAccessDenied(code)


def require_safe_text(value: Any, *, code: str, max_bytes: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > max_bytes
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise CapabilityAccessDenied(code)
    return value


async def await_io(value: T | Awaitable[T], *, timeout: float, code: str) -> T:
    """Await external IO with a hard deadline and sanitize every failure."""
    try:
        async with asyncio.timeout(timeout):
            if inspect.isawaitable(value):
                return await value
            return value
    except CapabilityAdapterError:
        raise
    except (asyncio.TimeoutError, TimeoutError):
        raise CapabilityBackendError(f"{code}_timeout") from None
    except Exception:
        raise CapabilityBackendError(code) from None


async def thread_io(call: Any, /, *args: Any, timeout: float, code: str, **kwargs: Any) -> Any:
    """Run blocking driver IO off the event loop with the same error boundary."""
    try:
        async with asyncio.timeout(timeout):
            return await asyncio.to_thread(call, *args, **kwargs)
    except CapabilityAdapterError:
        raise
    except (asyncio.TimeoutError, TimeoutError):
        raise CapabilityBackendError(f"{code}_timeout") from None
    except Exception:
        raise CapabilityBackendError(code) from None


class CloseOnce:
    """Close only explicitly owned resources, once, without closing shared managers."""

    def __init__(self, *, timeout_seconds: float = 5.0, owned_resources: Iterable[Any] = ()) -> None:
        if timeout_seconds <= 0:
            raise CapabilityConfigurationError("invalid_io_timeout")
        self._timeout_seconds = float(timeout_seconds)
        self._owned_resources = tuple(owned_resources)
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise CapabilityBackendError("adapter_closed")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        seen: set[int] = set()
        first_error: CapabilityAdapterError | None = None
        for resource in self._owned_resources:
            if id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "aclose", None) or getattr(resource, "close", None)
            if not callable(close):
                continue
            try:
                await await_io(close(), timeout=self._timeout_seconds, code="adapter_close_failed")
            except CapabilityAdapterError as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


def response_digest(response: TypedResponse) -> str:
    """Digest only the safe typed projection, never a backend payload object."""
    return canonical_digest({
        "request_id": response.request_id,
        "ok": response.ok,
        "type": type(response).__name__,
    })
