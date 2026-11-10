"""Typed runtime surface consumed by the canonical scoped runtime transports.

Proof contract (all identities are exact, never tenant selectors supplied by a
browser):

* pod requests echo company/project/plugin/instance, artifact/backend digests,
  grant/request/pod/job ids, action/target_ref/arguments_digest; the response
  adds a typed result, canonical-JSON result_digest and bounded bytes_received.
* read_status/read_logs bind target_ref to job_id; send_agent_message requires
  a non-empty arguments digest; read_artifact requires a non-empty target
  digest and no arguments digest. Exact result shapes are status={status,ready},
  logs={job_id,content_digest,byte_count}, message={job_id,arguments_digest,
  accepted}, artifact={job_id,artifact_digest,byte_count}. For logs/artifacts,
  bytes_received equals byte_count; for status/message it is canonical result
  size. result_digest is SHA-256 of sorted, compact UTF-8 result JSON.
* egress responses echo scoped identity and declaration (never body_base64 or
  secret values), adding status/byte counts, response_digest, resolved_host,
  redirect hops and explicit redirect_checked/tls_verified evidence. hops holds
  redirect-target hosts in order; resolved_host is the final TLS peer name.
* The canonical executor independently proves Deployment/backend-digest
  ownership. This process requires fresh Gateway introspection, fixed identity
  headers, and a platform-managed projection of the actual instance/backend
  digest. Header/body assertions alone are never proof of deployment identity.
  A deployment that cannot supply those trusted facts fails closed.

No endpoint accepts a shell, command, arbitrary Kubernetes URL, response body or
plaintext log. Egress policy and SecretStore are trusted server-side ports;
client declarations never widen them. Declaration/metadata/action evidence must
come from the authenticated platform-scoped transport. Module-local production
providers remain forbidden; unavailable sources and unwired actions return typed
errors rather than synthetic success.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import http.client
import httpx
import ipaddress
import json
import re
import socket
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar
from urllib.parse import unquote, urljoin, urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

_EMPTY_DIGEST = "sha256:" + "0" * 64
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_SECRET_REF = re.compile(
    r"^(?:vault://|file://|secret_ref://|secret://)[A-Za-z0-9][A-Za-z0-9._/-]{2,}$"
)
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_MAX_POD_JSON = 64 * 1024
_MAX_EGRESS_JSON = 2 * 1024 * 1024
_MAX_RUNTIME_RESPONSE_JSON = 64 * 1024
_MAX_REMOTE_REQUEST_BYTES = 16 * 1024
_MAX_REMOTE_RESPONSE_BYTES = 128 * 1024
_MAX_EGRESS_BYTES = 1024 * 1024
_MAX_ABSOLUTE_EGRESS_BYTES = 16 * 1024 * 1024
_MAX_REDIRECTS = 3
_MAX_DNS_ADDRESSES = 8
_MAX_SECRET_BYTES = 16 * 1024
_EGRESS_TIMEOUT = 10.0
_POLICY_TIMEOUT = 2.0
_CAPABILITY_TOKEN_HEADER = "X-Plugin-Capability-Token"
_INSTANCE_HEADER = "X-Plugin-Instance-ID"
_PLUGIN_HEADER = "X-Plugin-ID"
_ARTIFACT_HEADER = "X-Plugin-Artifact-Digest"
_REQUIRED_RUNTIME_HEADERS = (
    _CAPABILITY_TOKEN_HEADER,
    _INSTANCE_HEADER,
    _PLUGIN_HEADER,
    _ARTIFACT_HEADER,
)


class RuntimeScope(Protocol):
    company_id: str
    plugin_id: str
    project_id: str | None
    grant_id: str
    artifact_digest: str
    scopes: tuple[str, ...]


class SecretStore(Protocol):
    def get(self, secret_ref: str) -> str: ...


class EgressDeclarationProvider(Protocol):
    async def declaration(self, *, identity: Mapping[str, Any]) -> EgressGrantDeclaration | None: ...


class ArtifactMetadataProvider(Protocol):
    async def byte_count(self, *, identity: Mapping[str, Any]) -> int | None: ...


class PodActionProvider(Protocol):
    async def read_status(self, *, identity: Mapping[str, Any]) -> StatusResult | None: ...

    async def read_logs(self, *, identity: Mapping[str, Any]) -> LogsResult | None: ...

    async def send_agent_message(self, *, identity: Mapping[str, Any]) -> MessageResult | None: ...


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _ProofIdentity(_Model):
    company_id: str = Field(min_length=1, max_length=256)
    plugin_id: str = Field(min_length=1, max_length=256)
    plugin_instance_id: str = Field(min_length=1, max_length=256)
    artifact_digest: str = Field(min_length=71, max_length=71)
    backend_digest: str = Field(min_length=71, max_length=71)
    grant_id: str = Field(min_length=1, max_length=256)
    request_id: str = Field(min_length=1, max_length=256)


class PodActionDeclaration(_ProofIdentity):
    project_id: str = Field(min_length=1, max_length=256)
    pod_id: str = Field(min_length=1, max_length=256)
    job_id: str = Field(min_length=1, max_length=256)
    action: Literal["read_status", "read_logs", "send_agent_message", "read_artifact"]
    target_ref: str = Field(min_length=1, max_length=2048)
    arguments_digest: str = Field(min_length=71, max_length=71)
    max_result_bytes: StrictInt


class SecretReference(_Model):
    header: Literal["Authorization", "X-API-Key"]
    secret_ref: str = Field(min_length=4, max_length=2048)


class EgressRequestDeclaration(_ProofIdentity):
    subject_id: str = Field(min_length=1, max_length=512)
    host: str = Field(min_length=1, max_length=253)
    port: StrictInt
    path: str = Field(min_length=1, max_length=2048)
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
    body_digest: str = Field(min_length=71, max_length=71)
    body_base64: str = Field(max_length=1_500_000)
    max_request_bytes: StrictInt
    max_response_bytes: StrictInt
    secret_refs: list[SecretReference] = Field(default_factory=list, max_length=8)


class StatusResult(_Model):
    status: Literal["ready", "degraded", "unavailable"]
    ready: bool


class LogsResult(_Model):
    job_id: str = Field(min_length=1, max_length=256)
    content_digest: str = Field(min_length=71, max_length=71)
    byte_count: StrictInt = Field(ge=0, le=_MAX_EGRESS_BYTES)


class MessageResult(_Model):
    job_id: str = Field(min_length=1, max_length=256)
    arguments_digest: str = Field(min_length=71, max_length=71)
    accepted: Literal[True]


class ArtifactResult(_Model):
    job_id: str = Field(min_length=1, max_length=256)
    artifact_digest: str = Field(min_length=71, max_length=71)
    byte_count: StrictInt = Field(ge=0, le=_MAX_EGRESS_BYTES)


class PodActionResponse(PodActionDeclaration):
    result: StatusResult | LogsResult | MessageResult | ArtifactResult
    result_digest: str = Field(min_length=71, max_length=71)
    bytes_received: StrictInt = Field(ge=0, le=_MAX_EGRESS_BYTES)


class EgressResponse(_ProofIdentity):
    subject_id: str = Field(min_length=1, max_length=512)
    host: str = Field(min_length=1, max_length=253)
    port: Literal[443]
    path: str = Field(min_length=1, max_length=2048)
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
    body_digest: str = Field(min_length=71, max_length=71)
    status_code: StrictInt = Field(ge=100, le=599)
    bytes_sent: StrictInt = Field(ge=0, le=_MAX_EGRESS_BYTES)
    bytes_received: StrictInt = Field(ge=0, le=_MAX_EGRESS_BYTES)
    response_digest: str = Field(min_length=71, max_length=71)
    resolved_host: str = Field(min_length=1, max_length=253)
    hops: list[str] = Field(max_length=_MAX_REDIRECTS)
    redirect_checked: Literal[True]
    tls_verified: Literal[True]
    max_request_bytes: StrictInt = Field(gt=0, le=_MAX_EGRESS_BYTES)
    max_response_bytes: StrictInt = Field(gt=0, le=_MAX_EGRESS_BYTES)


@dataclass(frozen=True, slots=True)
class EgressRule:
    host: str
    path_prefix: str = "/"
    method: str = "GET"


@dataclass(frozen=True, slots=True)
class EgressGrantDeclaration:
    """Trusted grant snapshot; never constructed from client allowlists."""

    company_id: str
    plugin_id: str
    plugin_instance_id: str
    artifact_digest: str
    grant_id: str
    rules: tuple[EgressRule, ...]
    max_egress_bytes: int
    secret_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _HttpOutcome:
    status_code: int
    location: str | None
    content: bytes
    tls_verified: bool


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection to prevalidated IPs with original DNS name for SNI/SAN."""

    def __init__(
        self,
        host: str,
        addresses: Sequence[str],
        *,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(host, port=443, timeout=timeout, context=context)
        self._runtime_addresses = tuple(addresses)
        self.runtime_tls_verified = False

    def connect(self) -> None:
        last_error: OSError | None = None
        for address in self._runtime_addresses:
            raw_socket: socket.socket | None = None
            try:
                raw_socket = socket.create_connection((address, 443), timeout=self.timeout)
                raw_socket.settimeout(self.timeout)
                self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
                if not self.sock.getpeercert():
                    self.sock.close()
                    self.sock = None
                    raise OSError("TLS peer certificate missing")
                self.runtime_tls_verified = True
                return
            except OSError as exc:
                last_error = exc
                if self.sock is not None:
                    self.sock.close()
                    self.sock = None
                elif raw_socket is not None:
                    raw_socket.close()
        raise OSError("pinned HTTPS connection failed") from last_error


_ModelT = TypeVar("_ModelT", bound=BaseModel)
_ScopeProvider = Callable[[Request], Awaitable[RuntimeScope]]
_EgressProvider = Callable[[Mapping[str, Any]], Awaitable[EgressGrantDeclaration | None]]
_ArtifactProvider = Callable[[Mapping[str, Any]], Awaitable[int | None]]
_SecretStoreProvider = Callable[[], SecretStore | None]
_DeploymentIdentityProvider = Callable[[], Mapping[str, Any] | None]


def _deny(detail: str, *, status: int = 403) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def _safe_text(value: Any, *, max_length: int = 2048) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= max_length
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _digest(value: Any, *, allow_empty: bool = True) -> bool:
    return (
        isinstance(value, str)
        and _SHA256.fullmatch(value) is not None
        and (allow_empty or value != _EMPTY_DIGEST)
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _result_digest(result: BaseModel) -> str:
    return _digest_bytes(_canonical_json(result.model_dump()))


def _json_response(model: BaseModel, *, status_code: int = 200) -> Response:
    content = _canonical_json(model.model_dump())
    if len(content) > _MAX_RUNTIME_RESPONSE_JSON:
        raise _deny("runtime response byte limit exceeded", status=502)
    return Response(content=content, media_type="application/json", status_code=status_code)


def _check_json_budget(raw: bytes) -> None:
    depth = 0
    commas = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                in_string = False
            continue
        if byte == 34:
            in_string = True
        elif byte in (123, 91):
            depth += 1
            if depth > 32:
                raise _deny("runtime JSON depth limit exceeded", status=422)
        elif byte in (125, 93):
            depth -= 1
        elif byte == 44:
            commas += 1
            if commas > 1024:
                raise _deny("runtime JSON node limit exceeded", status=422)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


async def _typed_body(request: Request, model: type[_ModelT], limit: int) -> _ModelT:
    media_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise _deny("application/json required", status=415)
    raw_length = request.headers.get("Content-Length")
    if raw_length is not None:
        try:
            declared = int(raw_length)
        except (TypeError, ValueError):
            raise _deny("invalid Content-Length", status=400) from None
        if declared < 0:
            raise _deny("invalid Content-Length", status=400)
        if declared > limit:
            raise _deny("runtime request byte limit exceeded", status=413)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _deny("runtime request byte limit exceeded", status=413)
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw:
        raise _deny("typed JSON body required", status=422)
    _check_json_budget(raw)
    try:
        json.loads(raw, object_pairs_hook=_unique_json_object)
        return model.model_validate_json(raw)
    except (ValidationError, UnicodeError, ValueError):
        # Validation details can echo supplied secrets: return only a safe code.
        raise _deny("runtime request schema mismatch", status=422) from None


def _public_hostname(value: Any) -> str:
    if not _safe_text(value, max_length=253):
        raise _deny("egress host is not allowed")
    candidate = value.lower().rstrip(".")
    if (
        len(candidate) > 253
        or "." not in candidate
        or candidate.startswith("metadata.")
        or candidate.endswith((".internal", ".local", ".localhost"))
        or any(char in candidate for char in "/\\:@?#%[]")
    ):
        raise _deny("egress host is not allowed")
    labels = candidate.split(".")
    if any(_HOST_LABEL.fullmatch(label) is None for label in labels):
        raise _deny("egress host is not allowed")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise _deny("egress IP literals are not allowed")
    if all(re.fullmatch(r"0[xX][0-9a-fA-F]+|0[0-7]*|\d+", label) for label in labels):
        raise _deny("egress numeric hosts are not allowed")
    return candidate


def _public_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(value)
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    candidates = (address,) if mapped is None else (address, mapped)
    for candidate in candidates:
        if (
            candidate.is_private
            or candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_multicast
            or candidate.is_unspecified
            or candidate.is_reserved
            or not candidate.is_global
        ):
            raise _deny("egress resolved address is not public", status=502)
    return address


def _resolve_public_addresses(host: str) -> tuple[str, ...]:
    records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not records or len(records) > 64:
        raise _deny("egress DNS resolution failed", status=502)
    addresses: list[str] = []
    for record in records:
        address = str(_public_address(record[4][0]))
        if address not in addresses:
            addresses.append(address)
    if not addresses or len(addresses) > _MAX_DNS_ADDRESSES:
        raise _deny("egress DNS resolution is ambiguous", status=502)
    return tuple(addresses)


def _canonical_path(value: Any) -> tuple[str, str]:
    if not _safe_text(value, max_length=2048) or not value.startswith("/"):
        raise _deny("egress path is not allowed")
    parsed = urlsplit(value)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or value.startswith("//")
        or "\\" in value
        or re.search(r"%(?![0-9A-Fa-f]{2})", value)
    ):
        raise _deny("egress path is not allowed")
    try:
        decoded = unquote(parsed.path, encoding="utf-8", errors="strict")
    except (UnicodeError, ValueError):
        raise _deny("egress path is not allowed") from None
    if "\\" in decoded or any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        raise _deny("egress path is not allowed")
    segments: list[str] = []
    for segment in decoded.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)
    path = "/" + "/".join(segments)
    if any(ord(char) < 32 or ord(char) == 127 for char in parsed.query):
        raise _deny("egress query is not allowed")
    return path, parsed.query


def _request_target(path: str, query: str) -> str:
    from urllib.parse import quote

    encoded = quote(path, safe="/-._~!$&'()*+,;=:@")
    return encoded + ("?" + query if query else "")


def _path_matches(prefix: str, path: str) -> bool:
    if prefix == "/":
        return True
    cleaned = prefix.rstrip("/")
    return path == cleaned or path.startswith(cleaned + "/")


def _validate_policy(policy: Any, request: EgressRequestDeclaration) -> EgressGrantDeclaration:
    if not isinstance(policy, EgressGrantDeclaration):
        raise _deny("egress declaration unavailable", status=503)
    if (
        policy.company_id != request.company_id
        or policy.plugin_id != request.plugin_id
        or policy.plugin_instance_id != request.plugin_instance_id
        or policy.artifact_digest != request.artifact_digest
        or policy.grant_id != request.grant_id
        or isinstance(policy.max_egress_bytes, bool)
        or not isinstance(policy.max_egress_bytes, int)
        or not 1 <= policy.max_egress_bytes <= _MAX_ABSOLUTE_EGRESS_BYTES
        or not isinstance(policy.rules, tuple)
        or not policy.rules
        or len(policy.rules) > 256
        or not isinstance(policy.secret_refs, tuple)
        or len(policy.secret_refs) > 32
        or any(not isinstance(ref, str) for ref in policy.secret_refs)
        or len(set(policy.secret_refs)) != len(policy.secret_refs)
        or any(_SECRET_REF.fullmatch(ref) is None for ref in policy.secret_refs)
    ):
        raise _deny("egress grant identity mismatch")
    for rule in policy.rules:
        if not isinstance(rule, EgressRule):
            raise _deny("egress declaration invalid", status=503)
        if (
            _public_hostname(rule.host) != rule.host
            or not isinstance(rule.method, str)
            or rule.method not in _METHODS
        ):
            raise _deny("egress declaration invalid", status=503)
        prefix, query = _canonical_path(rule.path_prefix)
        if query or prefix != rule.path_prefix:
            raise _deny("egress declaration invalid", status=503)
    return policy


def _route_allowed(
    policy: EgressGrantDeclaration, *, host: str, path: str, method: str
) -> bool:
    return any(
        rule.host == host and rule.method == method and _path_matches(rule.path_prefix, path)
        for rule in policy.rules
    )


def _assert_public_url(url: str) -> tuple[str, str, str]:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or parsed.username or parsed.password or parsed.fragment:
        raise _deny("egress redirect is not HTTPS", status=502)
    try:
        port = parsed.port
        host = parsed.hostname
    except ValueError:
        raise _deny("egress redirect authority invalid", status=502) from None
    if port not in (None, 443) or host is None:
        raise _deny("egress redirect port is not 443", status=502)
    raw_path = parsed.path or "/"
    if parsed.query:
        raw_path += "?" + parsed.query
    path, query = _canonical_path(raw_path)
    return _public_hostname(host), path, query


def _tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _perform_https_request(
    *,
    host: str,
    addresses: Sequence[str],
    target: str,
    method: str,
    body: bytes,
    headers: Mapping[str, str],
    response_cap: int,
    timeout: float,
) -> _HttpOutcome:
    connection = _PinnedHTTPSConnection(
        host,
        addresses,
        timeout=timeout,
        context=_tls_context(),
    )
    try:
        connection.request(
            method,
            target,
            body=body,
            headers={
                "Accept": "application/json, application/octet-stream;q=0.9",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "User-Agent": "hello-plugin-runtime/1",
                **headers,
            },
        )
        response = connection.getresponse()
        if not connection.runtime_tls_verified:
            raise _deny("egress TLS evidence missing", status=502)
        location = response.getheader("Location")
        if response.status in _REDIRECTS:
            # Closing instead of draining avoids uncounted redirect-body traffic.
            return _HttpOutcome(
                status_code=response.status,
                location=location,
                content=b"",
                tls_verified=True,
            )
        if response.length is not None and response.length > response_cap:
            raise _deny("egress response byte limit exceeded", status=502)
        content = bytearray()
        while len(content) <= response_cap:
            chunk = response.read(min(64 * 1024, response_cap + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > response_cap:
            raise _deny("egress response byte limit exceeded", status=502)
        return _HttpOutcome(
            status_code=response.status,
            location=location,
            content=bytes(content),
            tls_verified=True,
        )
    finally:
        connection.close()


async def _resolve_secrets(
    store_provider: _SecretStoreProvider,
    references: Sequence[SecretReference],
    allowed_refs: frozenset[str],
) -> dict[str, str]:
    if not references:
        return {}
    store = store_provider()
    if store is None or not callable(getattr(store, "get", None)):
        raise _deny("egress SecretStore unavailable", status=503)
    seen: set[str] = set()
    resolved: dict[str, str] = {}
    total = 0
    for reference in references:
        if (
            reference.header in seen
            or _SECRET_REF.fullmatch(reference.secret_ref) is None
            or reference.secret_ref not in allowed_refs
        ):
            raise _deny("egress secret reference invalid")
        seen.add(reference.header)
        try:
            value = await asyncio.wait_for(
                asyncio.to_thread(store.get, reference.secret_ref), timeout=_POLICY_TIMEOUT
            )
        except Exception:
            raise _deny("egress secret resolution failed", status=503) from None
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\r" in value
            or "\n" in value
            or len(value.encode("utf-8")) > _MAX_SECRET_BYTES
        ):
            raise _deny("egress secret value invalid", status=503)
        total += len(value.encode("utf-8"))
        if total > _MAX_SECRET_BYTES:
            raise _deny("egress secret byte limit exceeded", status=413)
        resolved[reference.header] = value
    return resolved


async def _call_with_deadline(function: Callable[[], _HttpOutcome], deadline: float) -> _HttpOutcome:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _deny("egress deadline exceeded", status=502)
    try:
        return await asyncio.wait_for(asyncio.to_thread(function), timeout=remaining)
    except HTTPException:
        raise
    except Exception:
        raise _deny("egress HTTPS request failed", status=502) from None


async def _execute_egress(
    request: EgressRequestDeclaration,
    policy: EgressGrantDeclaration,
    secret_headers: Mapping[str, str],
) -> tuple[str, list[str], int, bytes]:
    try:
        raw_body = base64.b64decode(request.body_base64.encode("ascii"), validate=True)
    except (UnicodeError, binascii.Error, ValueError):
        raise _deny("egress body_base64 invalid", status=422) from None
    if len(raw_body) > _MAX_EGRESS_BYTES or len(raw_body) > request.max_request_bytes:
        raise _deny("egress request body byte limit exceeded", status=413)
    actual_digest = _digest_bytes(raw_body)
    expected_digest = request.body_digest
    if expected_digest == _EMPTY_DIGEST:
        if raw_body:
            raise _deny("egress body digest mismatch")
    elif not raw_body or not hmac.compare_digest(actual_digest, expected_digest):
        raise _deny("egress body digest mismatch")
    remaining_quota = policy.max_egress_bytes - len(raw_body)
    response_cap = min(request.max_response_bytes, _MAX_EGRESS_BYTES, remaining_quota)
    request_cap = min(request.max_request_bytes, _MAX_EGRESS_BYTES, policy.max_egress_bytes)
    if request_cap < len(raw_body) or response_cap < 1:
        raise _deny("egress grant byte quota exceeded", status=413)

    host = _public_hostname(request.host)
    path, query = _canonical_path(request.path)
    method = request.method
    if request.port != 443 or not _route_allowed(policy, host=host, path=path, method=method):
        raise _deny("egress route is not declared")
    current_url = "https://" + host + _request_target(path, query)
    original_host = host
    hops: list[str] = []
    deadline = time.monotonic() + _EGRESS_TIMEOUT

    for redirect_count in range(_MAX_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _deny("egress deadline exceeded", status=502)
        try:
            addresses = await asyncio.wait_for(
                asyncio.to_thread(_resolve_public_addresses, host), timeout=remaining
            )
        except HTTPException:
            raise
        except Exception:
            raise _deny("egress DNS resolution failed", status=502) from None
        target = _request_target(path, query)
        # Credentials are origin-bound, even when another host is allowlisted.
        headers = dict(secret_headers) if host == original_host else {}
        outcome = await _call_with_deadline(
            lambda: _perform_https_request(
                host=host,
                addresses=addresses,
                target=target,
                method=method,
                body=raw_body if redirect_count == 0 else b"",
                headers=headers,
                response_cap=response_cap,
                timeout=min(3.0, max(0.1, deadline - time.monotonic())),
            ),
            deadline,
        )
        if not outcome.tls_verified:
            raise _deny("egress TLS evidence missing", status=502)
        if outcome.status_code not in _REDIRECTS:
            if len(outcome.content) > response_cap:
                raise _deny("egress response byte limit exceeded", status=502)
            return host, hops, outcome.status_code, outcome.content
        if raw_body:
            # Replaying/dropping a digest-bound body would falsify byte evidence.
            raise _deny("egress redirect with a request body is denied")
        if redirect_count >= _MAX_REDIRECTS or not outcome.location:
            raise _deny("egress redirect limit or Location invalid", status=502)
        next_url = urljoin(current_url, outcome.location)
        host, path, query = _assert_public_url(next_url)
        if outcome.status_code == 303 or (
            outcome.status_code in {301, 302} and method == "POST"
        ):
            method = "GET" if method != "HEAD" else "HEAD"
        if not _route_allowed(policy, host=host, path=path, method=method):
            raise _deny("egress redirect route is not declared", status=502)
        hops.append(host)
        current_url = "https://" + host + _request_target(path, query)
    raise _deny("egress redirect limit exceeded", status=502)



class RemoteRuntimeProviderError(RuntimeError):
    """Typed remote provider failure; never carries upstream response text."""

    def __init__(self, status_code: int, code: str) -> None:
        if status_code not in {400, 401, 403, 404, 409, 413, 415, 422, 502, 503}:
            raise ValueError("unsupported remote provider status")
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", code) is None:
            raise ValueError("unsafe remote provider code")
        self.status_code = status_code
        self.code = code
        super().__init__(code)


class _RemoteProviderRequest(_Model):
    backend_digest: str = Field(min_length=71, max_length=71)
    request_id: str = Field(min_length=1, max_length=256)


class _RemotePodRequest(_RemoteProviderRequest):
    job_id: str = Field(min_length=1, max_length=256)
    target_ref: str = Field(min_length=1, max_length=2048)
    arguments_digest: str = Field(min_length=71, max_length=71)
    max_result_bytes: StrictInt = Field(gt=0, le=_MAX_ABSOLUTE_EGRESS_BYTES)


class _RemoteIdentity(_Model):
    company_id: str = Field(min_length=1, max_length=256)
    plugin_id: str = Field(min_length=1, max_length=256)
    plugin_instance_id: str = Field(min_length=1, max_length=256)
    artifact_digest: str = Field(min_length=71, max_length=71)
    backend_digest: str = Field(min_length=71, max_length=71)
    grant_id: str = Field(min_length=1, max_length=256)
    request_id: str = Field(min_length=1, max_length=256)


class _RemoteEgressRule(_Model):
    host: str = Field(min_length=1, max_length=253)
    path_prefix: str = Field(min_length=1, max_length=2048)
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]


class _RemoteEgressDeclaration(_RemoteIdentity):
    rules: list[_RemoteEgressRule] = Field(min_length=1, max_length=256)
    max_egress_bytes: StrictInt = Field(gt=0, le=_MAX_ABSOLUTE_EGRESS_BYTES)
    secret_refs: list[str] = Field(default_factory=list, max_length=32)


class _RemotePodIdentity(_RemoteIdentity):
    project_id: str = Field(min_length=1, max_length=256)
    pod_id: str = Field(min_length=1, max_length=256)
    job_id: str = Field(min_length=1, max_length=256)
    target_ref: str = Field(min_length=1, max_length=2048)
    arguments_digest: str = Field(min_length=71, max_length=71)
    max_result_bytes: StrictInt = Field(gt=0, le=_MAX_ABSOLUTE_EGRESS_BYTES)


class _RemoteArtifactMetadata(_RemotePodIdentity):
    action: Literal["read_artifact"] = "read_artifact"
    byte_count: StrictInt = Field(ge=0, le=_MAX_ABSOLUTE_EGRESS_BYTES)


class _RemoteStatusResult(_Model):
    status: Literal["ready", "degraded", "unavailable"]
    ready: bool


class _RemoteLogsResult(_Model):
    job_id: str = Field(min_length=1, max_length=256)
    content_digest: str = Field(min_length=71, max_length=71)
    byte_count: StrictInt = Field(ge=0, le=_MAX_ABSOLUTE_EGRESS_BYTES)


class _RemoteMessageResult(_Model):
    job_id: str = Field(min_length=1, max_length=256)
    arguments_digest: str = Field(min_length=71, max_length=71)
    accepted: Literal[True]


class _RemoteStatus(_RemotePodIdentity):
    action: Literal["read_status"] = "read_status"
    result: _RemoteStatusResult


class _RemoteLogs(_RemotePodIdentity):
    action: Literal["read_logs"] = "read_logs"
    result: _RemoteLogsResult


class _RemoteMessage(_RemotePodIdentity):
    action: Literal["send_agent_message"] = "send_agent_message"
    result: _RemoteMessageResult


def _canonical_provider_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _unique_remote_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_SCHEMA_INVALID")
        result[key] = item
    return result


class RemoteRuntimeProviderClient:
    """Strict client for the protected platform provider gateway surface.

    A token comes from the current request only.  No claim/token/result is
    cached, redirects are never followed, proxies/environment are ignored, and
    every response must match a closed schema with the exact request scope.
    """

    def __init__(
        self,
        *,
        gateway_url: str,
        token_provider: Callable[[], str],
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._base_url = gateway_url
        self._token_provider = token_provider
        self._client_factory = client_factory or self._default_client
        if not callable(self._client_factory):
            raise ValueError("remote provider client factory required")

    @staticmethod
    def _default_client(**options: Any) -> Any:
        return httpx.AsyncClient(**options)

    @staticmethod
    def _base(gateway_url: str) -> str:
        if not isinstance(gateway_url, str) or not gateway_url or len(gateway_url) > 512:
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_BASE_INVALID")
        candidate = gateway_url.rstrip("/")
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_BASE_INVALID") from None
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or candidate.startswith("//")
            or "\\" in candidate
            or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
        ):
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_BASE_INVALID")
        return candidate

    def _fresh_token(self) -> str:
        try:
            return self._token_provider()
        except Exception:
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_TOKEN_UNAVAILABLE") from None

    @staticmethod
    def _remote_error(status_code: int) -> RemoteRuntimeProviderError:
        if status_code == 401:
            return RemoteRuntimeProviderError(401, "REMOTE_PROVIDER_UNAUTHORIZED")
        if status_code == 403:
            return RemoteRuntimeProviderError(403, "REMOTE_PROVIDER_FORBIDDEN")
        if status_code == 404:
            return RemoteRuntimeProviderError(404, "REMOTE_PROVIDER_NOT_FOUND")
        if status_code == 413:
            return RemoteRuntimeProviderError(413, "REMOTE_PROVIDER_BYTE_LIMIT_EXCEEDED")
        if status_code in {415, 422}:
            return RemoteRuntimeProviderError(422, "REMOTE_PROVIDER_SCHEMA_MISMATCH")
        return RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_UNAVAILABLE")

    @staticmethod
    def _assert_scope(payload: BaseModel, identity: Mapping[str, Any]) -> None:
        dumped = payload.model_dump()
        required = {
            "company_id",
            "plugin_id",
            "plugin_instance_id",
            "artifact_digest",
            "backend_digest",
            "grant_id",
            "request_id",
        }
        fields = set(required)
        if isinstance(payload, (_RemotePodIdentity, _RemoteArtifactMetadata, _RemoteStatus, _RemoteLogs, _RemoteMessage)):
            fields |= {
                "project_id",
                "pod_id",
                "job_id",
                "target_ref",
                "arguments_digest",
                "max_result_bytes",
            }
        for name in fields:
            if identity.get(name) != dumped.get(name):
                raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_SCOPE_MISMATCH")

    async def _call(
        self,
        *,
        path: str,
        request: _Model,
        response_model: type[_ModelT],
        identity: Mapping[str, Any],
    ) -> _Model:
        base = self._base(self._base_url)
        try:
            token = self._fresh_token()
        except Exception:
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_TOKEN_UNAVAILABLE") from None
        if (
            not isinstance(token, str)
            or not token
            or token != token.strip()
            or len(token) > 16_384
            or any(not 33 <= ord(character) <= 126 for character in token)
        ):
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_TOKEN_INVALID")
        try:
            raw_request = _canonical_provider_json(request.model_dump(mode="json"))
        except (TypeError, ValueError):
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_REQUEST_INVALID") from None
        if len(raw_request) > _MAX_REMOTE_REQUEST_BYTES:
            raise RemoteRuntimeProviderError(422, "REMOTE_PROVIDER_REQUEST_TOO_LARGE")
        raw_response = b""
        try:
            async with self._client_factory(
                timeout=httpx.Timeout(2.0, connect=1.0),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    base + path,
                    content=raw_request,
                    headers={
                        "Authorization": "Bearer " + token,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Connection": "close",
                    },
                ) as response:
                    status_code = response.status_code
                    if status_code < 200 or status_code >= 300:
                        raise self._remote_error(status_code)
                    media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
                    declared = response.headers.get("Content-Length")
                    if media_type != "application/json" or encoding not in {"", "identity"}:
                        raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_RESPONSE_INVALID")
                    if declared is not None and (
                        not declared.isascii()
                        or not declared.isdigit()
                        or int(declared, 10) > _MAX_REMOTE_RESPONSE_BYTES
                    ):
                        raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_RESPONSE_INVALID")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > _MAX_REMOTE_RESPONSE_BYTES:
                            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_RESPONSE_TOO_LARGE")
                        chunks.append(chunk)
                    raw_response = b"".join(chunks)
        except RemoteRuntimeProviderError:
            raise
        except Exception:
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_UNAVAILABLE") from None
        if not raw_response:
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_RESPONSE_INVALID")
        try:
            _check_json_budget(raw_response)
            payload = json.loads(raw_response, object_pairs_hook=_unique_remote_object)
            result = response_model.model_validate(payload)
        except HTTPException:
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_SCHEMA_INVALID") from None
        except (ValidationError, UnicodeError, ValueError, TypeError):
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_SCHEMA_INVALID") from None
        self._assert_scope(result, identity)
        return result

    async def declaration(self, *, identity: Mapping[str, Any]) -> EgressGrantDeclaration:
        request = _RemoteProviderRequest.model_validate({
            "backend_digest": identity.get("backend_digest"),
            "request_id": identity.get("request_id"),
        })
        result = await self._call(
            path="/capabilities/v1/runtime/declarations/egress",
            request=request,
            response_model=_RemoteEgressDeclaration,
            identity=identity,
        )
        if result.secret_refs:
            # Remote declarations cannot enable local plaintext resolution. Empty
            # allowlist keeps SecretStore/DNS fallback closed before any such IO.
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_SECRET_REFS_FORBIDDEN")
        try:
            return EgressGrantDeclaration(
                company_id=result.company_id,
                plugin_id=result.plugin_id,
                plugin_instance_id=result.plugin_instance_id,
                artifact_digest=result.artifact_digest,
                grant_id=result.grant_id,
                rules=tuple(
                    EgressRule(
                        host=rule.host,
                        path_prefix=rule.path_prefix,
                        method=rule.method,
                    )
                    for rule in result.rules
                ),
                max_egress_bytes=result.max_egress_bytes,
                secret_refs=tuple(result.secret_refs),
            )
        except (TypeError, ValueError):
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_DECLARATION_INVALID") from None

    async def byte_count(self, *, identity: Mapping[str, Any]) -> int:
        result = await self._call(
            path="/capabilities/v1/runtime/artifacts/metadata",
            request=_RemotePodRequest.model_validate({
                "backend_digest": identity.get("backend_digest"),
                "request_id": identity.get("request_id"),
                "job_id": identity.get("job_id"),
                "target_ref": identity.get("target_ref"),
                "arguments_digest": identity.get("arguments_digest"),
                "max_result_bytes": identity.get("max_result_bytes"),
            }),
            response_model=_RemoteArtifactMetadata,
            identity=identity,
        )
        if result.action != "read_artifact":
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_ACTION_MISMATCH")
        return result.byte_count

    async def _pod(self, *, path: str, identity: Mapping[str, Any]) -> _Model:
        request = _RemotePodRequest.model_validate({
            "backend_digest": identity.get("backend_digest"),
            "request_id": identity.get("request_id"),
            "job_id": identity.get("job_id"),
            "target_ref": identity.get("target_ref"),
            "arguments_digest": identity.get("arguments_digest"),
            "max_result_bytes": identity.get("max_result_bytes"),
        })
        response_model = {
            "/capabilities/v1/runtime/pods/status": _RemoteStatus,
            "/capabilities/v1/runtime/pods/logs": _RemoteLogs,
            "/capabilities/v1/runtime/pods/messages": _RemoteMessage,
        }.get(path)
        if response_model is None:
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_PATH_INVALID")
        return await self._call(
            path=path,
            request=request,
            response_model=response_model,
            identity=identity,
        )

    async def read_status(self, *, identity: Mapping[str, Any]) -> StatusResult:
        result = await self._pod(
            path="/capabilities/v1/runtime/pods/status", identity=identity
        )
        if not isinstance(result, _RemoteStatus):
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_SCHEMA_INVALID")
        return StatusResult(status=result.result.status, ready=result.result.ready)

    async def read_logs(self, *, identity: Mapping[str, Any]) -> LogsResult:
        result = await self._pod(
            path="/capabilities/v1/runtime/pods/logs", identity=identity
        )
        if not isinstance(result, _RemoteLogs):
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_SCHEMA_INVALID")
        return LogsResult(
            job_id=result.result.job_id,
            content_digest=result.result.content_digest,
            byte_count=result.result.byte_count,
        )

    async def send_agent_message(self, *, identity: Mapping[str, Any]) -> MessageResult:
        result = await self._pod(
            path="/capabilities/v1/runtime/pods/messages", identity=identity
        )
        if not isinstance(result, _RemoteMessage):
            raise RemoteRuntimeProviderError(503, "REMOTE_PROVIDER_SCHEMA_INVALID")
        return MessageResult(
            job_id=result.result.job_id,
            arguments_digest=result.result.arguments_digest,
            accepted=result.result.accepted,
        )


def install_runtime_api(
    app: FastAPI,
    *,
    scope_provider: _ScopeProvider,
    egress_declaration_provider: _EgressProvider,
    artifact_metadata_provider: _ArtifactProvider,
    secret_store_provider: _SecretStoreProvider,
    deployment_identity_provider: _DeploymentIdentityProvider,
    pod_action_provider: PodActionProvider,
) -> APIRouter:
    """Install fixed routes over platform evidence ports or explicit test doubles.

    A module-local registration is not production trust. The hello app uses
    RemoteRuntimeProviderClient so grant, tenant, instance and digest are
    revalidated by the platform on every operation.
    """

    router = APIRouter(prefix="/v1")
    egress_slots = asyncio.Semaphore(4)

    async def authorize(
        http_request: Request,
        declaration: _ProofIdentity,
        *,
        capability: str,
    ) -> RuntimeScope:
        for name in _REQUIRED_RUNTIME_HEADERS:
            value = http_request.headers.get(name)
            if not _safe_text(value, max_length=16_384 if name == _CAPABILITY_TOKEN_HEADER else 256):
                raise _deny(f"{name} required", status=401)
        # The scope provider performs fresh Gateway introspection. No claim,
        # header or introspection response is cached between operations.
        scope = await scope_provider(http_request)
        if capability not in scope.scopes:
            raise _deny("runtime capability not granted")
        try:
            deployment = deployment_identity_provider()
        except Exception:
            raise _deny("runtime deployment identity unavailable", status=503) from None
        if not isinstance(deployment, Mapping) or set(deployment) != {
            "plugin_instance_id", "backend_digest"
        }:
            raise _deny("runtime deployment identity unavailable", status=503)
        actual_instance = deployment.get("plugin_instance_id")
        actual_backend_digest = deployment.get("backend_digest")
        if (
            not _safe_text(actual_instance, max_length=256)
            or not _digest(actual_backend_digest, allow_empty=False)
        ):
            raise _deny("runtime deployment identity invalid", status=503)
        if (
            declaration.company_id != scope.company_id
            or declaration.plugin_id != scope.plugin_id
            or declaration.artifact_digest != scope.artifact_digest
            or declaration.grant_id != scope.grant_id
            or declaration.plugin_instance_id != actual_instance
            or declaration.backend_digest != actual_backend_digest
            or http_request.headers.get(_PLUGIN_HEADER) != declaration.plugin_id
            or http_request.headers.get(_INSTANCE_HEADER) != declaration.plugin_instance_id
            or http_request.headers.get(_ARTIFACT_HEADER) != declaration.artifact_digest
            or not _digest(declaration.artifact_digest, allow_empty=False)
            or not _digest(declaration.backend_digest, allow_empty=False)
        ):
            raise _deny("runtime identity or digest mismatch")
        for name, value in declaration.model_dump().items():
            if name != "body_base64" and isinstance(value, str) and not _safe_text(value):
                raise _deny("runtime declaration contains unsafe text", status=422)
        return scope

    async def provided_pod_result(
        expected_action: str, declaration: PodActionDeclaration
    ) -> BaseModel:
        method = getattr(pod_action_provider, expected_action, None)
        if not callable(method):
            raise _deny("pod action provider unavailable", status=503)
        try:
            result = await asyncio.wait_for(
                method(identity=declaration.model_dump()), timeout=_POLICY_TIMEOUT
            )
        except RemoteRuntimeProviderError as exc:
            raise _deny(exc.code.lower(), status=exc.status_code) from None
        except Exception:
            raise _deny("pod action provider unavailable", status=503) from None
        if result is None:
            raise _deny("pod action result not found", status=404)
        return result

    async def pod_endpoint(http_request: Request, expected_action: str) -> Response:
        declaration = await _typed_body(http_request, PodActionDeclaration, _MAX_POD_JSON)
        scope = await authorize(http_request, declaration, capability="pods")
        if (
            declaration.action != expected_action
            or declaration.project_id != scope.project_id
            or declaration.pod_id != declaration.plugin_instance_id
            or not _digest(declaration.arguments_digest)
        ):
            raise _deny("pod action or project binding mismatch")
        if declaration.max_result_bytes < 1:
            raise _deny("pod result byte limit invalid")
        result_cap = min(declaration.max_result_bytes, _MAX_EGRESS_BYTES)
        if expected_action in {"read_status", "read_logs"}:
            if declaration.target_ref != declaration.job_id or declaration.arguments_digest != _EMPTY_DIGEST:
                raise _deny("pod status/log digest binding required")
        if expected_action == "send_agent_message":
            if declaration.target_ref != declaration.job_id or not _digest(
                declaration.arguments_digest, allow_empty=False
            ):
                raise _deny("pod message digest binding required")
        if expected_action in {"read_status", "read_logs", "send_agent_message"}:
            result = await provided_pod_result(expected_action, declaration)
            expected_type = {
                "read_status": StatusResult,
                "read_logs": LogsResult,
                "send_agent_message": MessageResult,
            }[expected_action]
            if not isinstance(result, expected_type):
                raise _deny("pod action provider result invalid", status=502)
            if (
                isinstance(result, (LogsResult, MessageResult))
                and result.job_id != declaration.job_id
            ) or (
                isinstance(result, MessageResult)
                and result.arguments_digest != declaration.arguments_digest
            ) or (
                isinstance(result, LogsResult)
                and not _digest(result.content_digest)
            ):
                raise _deny("pod action provider binding mismatch", status=502)
            byte_count = (
                result.byte_count
                if isinstance(result, LogsResult)
                else len(_canonical_json(result.model_dump()))
            )
        elif expected_action == "read_artifact":
            if not _digest(declaration.target_ref, allow_empty=False) or declaration.arguments_digest != _EMPTY_DIGEST:
                raise _deny("pod artifact digest binding required")
            try:
                byte_count = await asyncio.wait_for(
                    artifact_metadata_provider(declaration.model_dump()),
                    timeout=_POLICY_TIMEOUT,
                )
            except RemoteRuntimeProviderError as exc:
                raise _deny(exc.code.lower(), status=exc.status_code) from None
            except Exception:
                raise _deny("pod artifact metadata unavailable", status=503) from None
            if byte_count is None:
                raise _deny("pod artifact not found", status=404)
            if (
                isinstance(byte_count, bool)
                or not isinstance(byte_count, int)
                or not 0 <= byte_count <= _MAX_EGRESS_BYTES
                or byte_count > result_cap
            ):
                raise _deny("pod artifact byte limit exceeded", status=413)
            result = ArtifactResult(
                job_id=declaration.job_id,
                artifact_digest=declaration.target_ref,
                byte_count=byte_count,
            )
        else:
            # Route/action matching above makes this unreachable without a new
            # explicitly typed operation; never fall back to synthetic success.
            raise _deny("pod action is not supported", status=404)
        if byte_count > result_cap:
            raise _deny("pod result byte limit exceeded", status=413)
        response = PodActionResponse(
            **declaration.model_dump(),
            result=result,
            result_digest=_result_digest(result),
            bytes_received=byte_count,
        )
        return _json_response(response)

    @router.post("/status", response_model=PodActionResponse)
    async def status(request: Request) -> Response:
        return await pod_endpoint(request, "read_status")

    @router.post("/logs", response_model=PodActionResponse)
    async def logs(request: Request) -> Response:
        return await pod_endpoint(request, "read_logs")

    @router.post("/messages", response_model=PodActionResponse)
    async def messages(request: Request) -> Response:
        return await pod_endpoint(request, "send_agent_message")

    @router.post("/artifacts", response_model=PodActionResponse)
    async def artifacts(request: Request) -> Response:
        return await pod_endpoint(request, "read_artifact")

    @router.post("/egress", response_model=EgressResponse)
    async def egress(http_request: Request) -> Response:
        declaration = await _typed_body(http_request, EgressRequestDeclaration, _MAX_EGRESS_JSON)
        await authorize(http_request, declaration, capability="network")
        if (
            declaration.port != 443
            or declaration.method not in _METHODS
            or not _digest(declaration.body_digest)
            or not 1 <= declaration.max_request_bytes <= _MAX_ABSOLUTE_EGRESS_BYTES
            or not 1 <= declaration.max_response_bytes <= _MAX_ABSOLUTE_EGRESS_BYTES
        ):
            raise _deny("egress declaration or byte cap invalid")
        identity = declaration.model_dump(exclude={"body_base64", "secret_refs"})
        try:
            policy = await asyncio.wait_for(
                egress_declaration_provider(identity), timeout=_POLICY_TIMEOUT
            )
        except RemoteRuntimeProviderError as exc:
            raise _deny(exc.code.lower(), status=exc.status_code) from None
        except Exception:
            raise _deny("egress grant declaration unavailable", status=503) from None
        policy = _validate_policy(policy, declaration)
        secret_headers = await _resolve_secrets(
            secret_store_provider, declaration.secret_refs, frozenset(policy.secret_refs)
        )
        try:
            await asyncio.wait_for(egress_slots.acquire(), timeout=_EGRESS_TIMEOUT)
        except Exception:
            raise _deny("egress concurrency limit reached", status=503) from None
        try:
            resolved_host, hops, status_code, content = await _execute_egress(
                declaration, policy, secret_headers
            )
        finally:
            egress_slots.release()
        body_size = len(base64.b64decode(declaration.body_base64.encode("ascii"), validate=True))
        if body_size + len(content) > policy.max_egress_bytes:
            raise _deny("egress grant byte quota exceeded", status=413)
        response = EgressResponse(
            company_id=declaration.company_id,
            plugin_id=declaration.plugin_id,
            plugin_instance_id=declaration.plugin_instance_id,
            artifact_digest=declaration.artifact_digest,
            backend_digest=declaration.backend_digest,
            grant_id=declaration.grant_id,
            request_id=declaration.request_id,
            subject_id=declaration.subject_id,
            host=declaration.host,
            port=443,
            path=declaration.path,
            method=declaration.method,
            body_digest=declaration.body_digest,
            status_code=status_code,
            bytes_sent=body_size,
            bytes_received=len(content),
            response_digest=_digest_bytes(content),
            resolved_host=resolved_host,
            hops=hops,
            redirect_checked=True,
            tls_verified=True,
            max_request_bytes=min(
                declaration.max_request_bytes, _MAX_EGRESS_BYTES, policy.max_egress_bytes
            ),
            max_response_bytes=min(
                declaration.max_response_bytes,
                _MAX_EGRESS_BYTES,
                policy.max_egress_bytes - body_size,
            ),
        )
        return _json_response(response)

    app.include_router(router)
    return router


__all__ = [
    "ArtifactMetadataProvider",
    "EgressDeclarationProvider",
    "RemoteRuntimeProviderClient",
    "RemoteRuntimeProviderError",
    "EgressGrantDeclaration",
    "EgressRequestDeclaration",
    "EgressResponse",
    "EgressRule",
    "PodActionDeclaration",
    "PodActionProvider",
    "PodActionResponse",
    "RuntimeScope",
    "SecretReference",
    "SecretStore",
    "install_runtime_api",
]
