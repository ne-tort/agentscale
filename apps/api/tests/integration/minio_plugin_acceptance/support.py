"""Sensitive-data-safe assertions and typed request helpers."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit
from uuid import uuid4
from xml.etree import ElementTree

import httpx

from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    ObjectPresignMethod,
    ObjectsPresignRequest,
    ObjectsPresignResponse,
)

from .resources import MinioAcceptanceEnvironment

SIGV4_FIELDS = frozenset({
    "X-Amz-Algorithm",
    "X-Amz-Credential",
    "X-Amz-Date",
    "X-Amz-Expires",
    "X-Amz-SignedHeaders",
    "X-Amz-Signature",
})


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AssertionError(code)


def envelope(
    environment: MinioAcceptanceEnvironment,
    *,
    company_id: str | None = None,
    plugin_id: str | None = None,
) -> CapabilityEnvelope:
    identity = environment.identity
    company = company_id or identity.company_id
    plugin = plugin_id or identity.plugin_id
    instance = f"inst-{identity.run_id}"
    return CapabilityEnvelope(
        request_id=f"req-{uuid4().hex}",
        company_id=company,
        subject_id=f"plugin:{plugin}:{company}:{instance}",
        plugin_instance_id=instance,
        artifact_digest=f"sha256:{uuid4().hex}{uuid4().hex}",
        grant_id=f"grant-{identity.run_id}",
    )


def presign_request(
    environment: MinioAcceptanceEnvironment,
    *,
    method: ObjectPresignMethod,
    ttl_seconds: int,
    object_key: str | None = None,
    bucket: str | None = None,
    company_id: str | None = None,
    plugin_id: str | None = None,
) -> ObjectsPresignRequest:
    identity = environment.identity
    return ObjectsPresignRequest(
        bucket=bucket or identity.bucket,
        object_key=object_key or identity.fixed_key,
        method=method,
        ttl_seconds=ttl_seconds,
        envelope=envelope(environment, company_id=company_id, plugin_id=plugin_id),
    )


def validate_signed_response(
    response: ObjectsPresignResponse,
    environment: MinioAcceptanceEnvironment,
    request: ObjectsPresignRequest,
) -> None:
    require(type(response) is ObjectsPresignResponse, "objects.presign returned the wrong type")
    require(response.ok is True, "objects.presign was not successful")
    require(response.bucket == request.bucket, "presign bucket mismatch")
    require(response.method is request.method, "presign method mismatch")
    require(response.ttl_seconds == request.ttl_seconds, "presign TTL mismatch")
    require(response.company_id == request.envelope.company_id, "presign company mismatch")
    require(response.plugin_id == request.envelope.subject_id.split(":")[1], "presign plugin mismatch")

    try:
        parsed = urlsplit(response.url)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        expected_endpoint = urlsplit(environment.config.endpoint)
    except ValueError:
        raise AssertionError("presign returned an invalid URL") from None
    require(
        parsed.scheme == expected_endpoint.scheme and parsed.netloc == expected_endpoint.netloc,
        "presign endpoint mismatch",
    )
    require(parsed.username is None and parsed.password is None and not parsed.fragment, "unsafe presign URL")
    require(set(query) == SIGV4_FIELDS, "presign must contain exactly six SigV4 fields")
    require(all(len(query[name]) == 1 for name in SIGV4_FIELDS), "repeated SigV4 field")
    require(query["X-Amz-Algorithm"][0] == "AWS4-HMAC-SHA256", "wrong SigV4 algorithm")
    require(query["X-Amz-Expires"][0] == str(request.ttl_seconds), "wrong SigV4 expiry")
    require(query["X-Amz-SignedHeaders"][0] == "host", "wrong signed headers")
    require(len(query["X-Amz-Signature"][0]) == 64, "wrong signature shape")
    require(
        all(character in "0123456789abcdef" for character in query["X-Amz-Signature"][0]),
        "wrong signature encoding",
    )
    credential = query["X-Amz-Credential"][0].split("/")
    require(len(credential) == 5, "wrong credential scope")
    require(credential[0] == environment.identity.presign_user, "wrong signing principal")
    require(credential[3:] == ["s3", "aws4_request"], "wrong SigV4 credential terminator")

    decoded_path = unquote(parsed.path)
    path_style = decoded_path == f"/{request.bucket}/{request.object_key}"
    virtual_hosted = (
        parsed.hostname is not None
        and parsed.hostname.startswith(f"{request.bucket}.")
        and decoded_path == f"/{request.object_key}"
    )
    require(path_style or virtual_hosted, "presign did not retain the exact bucket/key")


async def http_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    content: bytes | None = None,
) -> httpx.Response:
    """Execute a real operation without ever including its signed URL in failures."""
    try:
        return await client.request(method, url, content=content, follow_redirects=False)
    except Exception:
        raise AssertionError("presigned HTTP operation failed before a response") from None


def error_code(response: httpx.Response) -> str:
    if len(response.content) > 1024 * 1024:
        return ""
    try:
        root = ElementTree.fromstring(response.content)
    except (ElementTree.ParseError, ValueError):
        return ""
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "Code":
            return element.text or ""
    return ""


def require_http_denied(response: httpx.Response, *, codes: frozenset[str]) -> None:
    require(response.status_code == 403, "denied operation did not return HTTP 403")
    code = error_code(response)
    require(code not in {"NoSuchBucket", "NoSuchKey", "404", "NotFound"}, "missing resource is not denial evidence")
    require(code in codes, "denied operation returned an ambiguous error code")


def s3_status_and_code(value: Any) -> tuple[int | None, str]:
    response = value if isinstance(value, dict) else getattr(value, "response", None)
    if not isinstance(response, dict):
        return None, ""
    metadata = response.get("ResponseMetadata") or {}
    error = response.get("Error") or {}
    try:
        status = int(metadata.get("HTTPStatusCode"))
    except (TypeError, ValueError):
        status = None
    return status, str(error.get("Code") or "")


def require_s3_head_zero(response: Any) -> None:
    status, _ = s3_status_and_code(response)
    require(status == 200, "canary HEAD did not return HTTP 200")
    require(response.get("ContentLength") == 0, "canary object is not zero-byte")


def require_s3_access_denied(exception: BaseException) -> None:
    status, code = s3_status_and_code(exception)
    require(status == 403, "foreign operation did not return HTTP 403")
    require(code.lower() in {"accessdenied", "403"}, "foreign operation did not return AccessDenied")


def tampered_path(url: str, *, bucket: str, key: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(parsed._replace(path="/" + quote(f"{bucket}/{key}", safe="/")))


def signature_values(url: str) -> tuple[str, ...]:
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    return (url, *(values[0] for values in query.values() if values))


async def wait_for_signature_expiry(url: str) -> float:
    """Use the actual wall clock and signed timestamp; never install a fake clock."""
    query = parse_qs(urlsplit(url).query, keep_blank_values=True, strict_parsing=True)
    try:
        signed_at = datetime.strptime(query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        ttl = int(query["X-Amz-Expires"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        raise AssertionError("cannot determine signed expiry") from None
    expires_at = signed_at.timestamp() + ttl
    # Two seconds covers the one-second timestamp truncation and small clock skew.
    await asyncio.sleep(max(0.0, expires_at - time.time() + 2.0))
    require(time.time() >= expires_at, "controlled wait ended before actual signed expiry")
    return expires_at


class MemorySecretStore:
    """In-memory store used to prove that failed STS never persists a fallback."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.put_calls = 0

    def get(self, ref: str) -> str:
        if ref not in self.values:
            error = RuntimeError("SECRET_NOT_FOUND")
            error.code = "SECRET_NOT_FOUND"
            raise error
        return self.values[ref]

    def put(self, key: str, value: str) -> str:
        self.put_calls += 1
        ref = f"vault://ai_keys/{key}"
        self.values[ref] = value
        return ref


@dataclass(slots=True)
class LeakSentinel:
    values: set[str] = field(repr=False)

    @classmethod
    def for_environment(cls, environment: MinioAcceptanceEnvironment) -> "LeakSentinel":
        return cls(set(environment.sensitive_values))

    def add_url(self, url: str) -> None:
        self.values.update(signature_values(url))

    def assert_clean(self, text: str) -> None:
        if any(value and value in text for value in self.values):
            raise AssertionError("sensitive acceptance material appeared in captured logs")

    def assert_scoped_record_safe(self, store: MemorySecretStore) -> None:
        # This helper is for negative paths only: no STS/presign material may be stored.
        if store.put_calls or store.values:
            raise AssertionError("failed canary persisted credentials or a presign fallback")
