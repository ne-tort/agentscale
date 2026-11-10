"""Offline-testable live acceptance client for plugin v4.

The module deliberately contains no Typer or Kubernetes imports. cli.py is
only a thin adapter; tests can therefore exercise the HTTP contract with
httpx.MockTransport without importing the backend or touching a cluster.

Credentials come from environment variables, never command-line arguments:
PRODAVAN_PLUGIN_API_BASE_URL plus exactly one of
PRODAVAN_PLUGIN_BEARER_TOKEN / PRODAVAN_PLUGIN_SERVICE_TOKEN.
Both token forms are sent as an HTTP Bearer credential. The service token name
only documents its provenance; it does not change the wire contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, TextIO
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import httpx

from prodavan_ops.paths import repo_root

API_BASE_URL_ENV = "PRODAVAN_PLUGIN_API_BASE_URL"
BEARER_TOKEN_ENV = "PRODAVAN_PLUGIN_BEARER_TOKEN"
SERVICE_TOKEN_ENV = "PRODAVAN_PLUGIN_SERVICE_TOKEN"

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_PAYLOAD_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
RETRY_DELAY_SECONDS = 0.25

EXIT_CONFIG = 2
EXIT_PAYLOAD = 3
EXIT_HTTP = 4
EXIT_TRANSPORT = 5
EXIT_CONTRACT = 6
EXIT_EVIDENCE_IO = 7
EXIT_CAPABILITY_DENIED = 8

_REDACTED = "[REDACTED]"
_REDACTED_DSN = "[REDACTED_DSN]"
_SAFE_COMPANY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_BEARER_VALUE = re.compile(r"(?i)\bBearer[ \t]+[^ \t\r\n]+")
_JWT_VALUE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_DSN_VALUE = re.compile(
    r"(?i)\b(?:postgres(?:ql)?(?:\+[a-z0-9_]+)?|mysql(?:\+[a-z0-9_]+)?|"
    r"mongodb(?:\+srv)?|redis|rediss|amqp|asyncpg)://[^\s\"'<>]+"
)
_URL_USERINFO = re.compile(r"(?i)\b(https?://)[^\s/@:]+:[^\s/@]+@")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|signature|credential|session_token|"
    r"access_key|access_key_id|secret_access_key)=([^&\s]+)"
)
_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-plugin-capability-token",
        "x-service-token",
        "x-amz-security-token",
    }
)
_SENSITIVE_KEY_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "cookie",
    "credential",
    "private_key",
    "access_key",
    "dsn",
)
_SENSITIVE_URL_KEYS = (
    "database_url",
    "db_url",
    "redis_url",
    "dsn_url",
    "presigned_url",
    "signed_url",
)
_QUOTA_KEYS = (
    "ops_per_minute",
    "max_rows",
    "max_bytes",
    "max_keys",
    "max_object_bytes",
    "max_egress_bytes",
)


class PluginAcceptanceError(RuntimeError):
    """Expected fail-closed error with a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        exit_code: int,
        *,
        details: Any = None,
        data: Any = None,
        evidence_status: str = "failed",
    ) -> None:
        super().__init__(redact_text(message))
        self.code = code
        self.exit_code = exit_code
        self.details = details
        self.data = data
        self.evidence_status = evidence_status
        # Transport failures carry their sanitized attempts across the raising
        # boundary; otherwise timeout/4xx/5xx evidence would lose HTTP metadata.
        self.http_attempts: list[dict[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": redact_text(str(self)),
        }
        if self.details is not None:
            result["details"] = redact_data(self.details)
        return result


@dataclass(frozen=True, slots=True)
class ClientConfig:
    base_url: str
    token: str = field(repr=False)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    insecure: bool = False
    retry: bool = False


@dataclass(frozen=True, slots=True)
class CapabilityRoute:
    method: str
    path: str


# Closed operation registry: there is intentionally no arbitrary method/path
# option. Payload schemas remain server-authoritative, while this registry
# prevents the CLI from becoming a generic dispatch or agent-surface proxy.
CAPABILITY_ROUTES: dict[str, CapabilityRoute] = {
    "health": CapabilityRoute("GET", "/capabilities/v1/health"),
    "cache.get": CapabilityRoute("POST", "/capabilities/v1/cache/get"),
    "cache.set": CapabilityRoute("POST", "/capabilities/v1/cache/set"),
    "cache.delete": CapabilityRoute("POST", "/capabilities/v1/cache/delete"),
    "cache.incr": CapabilityRoute("POST", "/capabilities/v1/cache/incr"),
    "cache.publish": CapabilityRoute("POST", "/capabilities/v1/cache/publish"),
    "objects.credentials": CapabilityRoute("POST", "/capabilities/v1/objects/credentials"),
    "objects.scoped_credentials": CapabilityRoute("POST", "/capabilities/v1/objects/credentials"),
    "objects.get": CapabilityRoute("GET", "/capabilities/v1/objects"),
    "objects.list": CapabilityRoute("POST", "/capabilities/v1/objects"),
    "objects.delete": CapabilityRoute("DELETE", "/capabilities/v1/objects/{key}"),
    "docs.query": CapabilityRoute("POST", "/capabilities/v1/docs/query"),
    "events.publish": CapabilityRoute("POST", "/capabilities/v1/events/publish"),
    "jobs.submit": CapabilityRoute("POST", "/capabilities/v1/jobs/submit"),
    "jobs.get": CapabilityRoute("GET", "/capabilities/v1/jobs/{job_id}"),
    "jobs.cancel": CapabilityRoute("POST", "/capabilities/v1/jobs/{job_id}/cancel"),
    "pods.invoke": CapabilityRoute("POST", "/capabilities/v1/pods/invoke"),
    "mcp.register": CapabilityRoute("POST", "/capabilities/v1/mcp/register"),
    "secrets.read": CapabilityRoute("POST", "/capabilities/v1/secrets/read"),
    "network.request": CapabilityRoute("POST", "/capabilities/v1/network/request"),
    "network.egress": CapabilityRoute("POST", "/capabilities/v1/network/request"),
}


@dataclass(slots=True)
class CommandResult:
    action: str
    ok: bool
    exit_code: int
    request_id: str
    started_at: str
    finished_at: str
    latency_ms: float
    evidence_status: str
    base_url: str = ""
    method: str = ""
    path: str = ""
    request_headers: dict[str, str] = field(default_factory=dict)
    payload: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)
    data: Any = None
    error: PluginAcceptanceError | None = None
    sensitive_values: tuple[str, ...] = field(default_factory=tuple, repr=False)

    @property
    def http_status(self) -> int | None:
        for attempt in reversed(self.attempts):
            response = attempt.get("response")
            if isinstance(response, dict) and isinstance(response.get("status"), int):
                return response["status"]
        return None

    def console_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "status": "ok" if self.ok else "error",
            "operation": self.action,
            "request_id": self.request_id,
            "evidence_status": self.evidence_status,
            "http_status": self.http_status,
        }
        if self.data is not None:
            document["data"] = redact_data(self.data)
        if self.error is not None:
            document["error"] = self.error.as_dict()
        return redact_data(document, sensitive_values=self.sensitive_values)

    def evidence_document(self) -> dict[str, Any]:
        response = None
        if self.attempts:
            response = self.attempts[-1].get("response")
        document: dict[str, Any] = {
            "schema_version": "plugin-acceptance/v1",
            "operation": self.action,
            "status": "ok" if self.ok else "error",
            "ok": self.ok,
            "exit_code": self.exit_code,
            "evidence_status": self.evidence_status,
            "request_id": self.request_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "latency_ms": self.latency_ms,
            "http_status": self.http_status,
            "request": {
                "method": self.method,
                "url": f"{self.base_url}{self.path}" if self.base_url and self.path else None,
                "path": self.path or None,
                "headers": sanitize_headers(self.request_headers),
                "payload": self.payload,
            },
            "response": response,
            "attempts": self.attempts,
            "data": redact_data(self.data),
            "error": self.error.as_dict() if self.error is not None else None,
        }
        return redact_data(document, sensitive_values=self.sensitive_values)


def redact_text(value: str, *, sensitive_values: tuple[str, ...] = ()) -> str:
    """Redact credentials in diagnostics, including exact configured secrets."""

    text = str(value)
    for secret in sorted({item for item in sensitive_values if item}, key=len, reverse=True):
        text = text.replace(secret, _REDACTED)
    text = _BEARER_VALUE.sub("Bearer [REDACTED]", text)
    text = _DSN_VALUE.sub(_REDACTED_DSN, text)
    text = _URL_USERINFO.sub(r"\1[REDACTED]@", text)
    text = _SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return _JWT_VALUE.sub(_REDACTED, text)


def _sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in _SENSITIVE_URL_KEYS:
        return True
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def redact_data(value: Any, *, sensitive_values: tuple[str, ...] = ()) -> Any:
    """Recursively redact JSON-like values without changing safe evidence."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            safe_key = redact_text(key, sensitive_values=sensitive_values)
            result[safe_key] = (
                _REDACTED
                if _sensitive_key(key)
                else redact_data(item, sensitive_values=sensitive_values)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [redact_data(item, sensitive_values=sensitive_values) for item in value]
    if isinstance(value, str):
        return redact_text(value, sensitive_values=sensitive_values)
    return value


def sanitize_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    """Drop credential headers and sanitize every remaining value."""

    sanitized: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).lower()
        if name in _SENSITIVE_HEADERS or _sensitive_key(name):
            continue
        sanitized[name] = redact_text(str(raw_value))
    return dict(sorted(sanitized.items()))


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalise_base_url(raw: str) -> str:
    value = raw.strip()
    if not value or _CONTROL_CHARS.search(value):
        raise PluginAcceptanceError(
            "invalid_api_base_url",
            f"{API_BASE_URL_ENV} must be a non-empty HTTP(S) URL without control characters",
            EXIT_CONFIG,
        )
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise PluginAcceptanceError(
            "invalid_api_base_url", f"{API_BASE_URL_ENV} is not a valid URL", EXIT_CONFIG
        ) from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PluginAcceptanceError(
            "invalid_api_base_url",
            f"{API_BASE_URL_ENV} must be an HTTP(S) origin without userinfo, query or fragment",
            EXIT_CONFIG,
        )
    path = parsed.path.rstrip("/")
    # Accept the repository's usual .../api/v1 spelling, but keep the two
    # plugin surfaces unambiguous: admin lives below /api/v1, capabilities at
    # the root. Other path prefixes are preserved for reverse proxies.
    if path.endswith("/api/v1"):
        path = path[: -len("/api/v1")]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", "")).rstrip("/")


def _read_token(environ: Mapping[str, str]) -> str:
    bearer = environ.get(BEARER_TOKEN_ENV, "").strip()
    service = environ.get(SERVICE_TOKEN_ENV, "").strip()
    if bearer and service:
        raise PluginAcceptanceError(
            "ambiguous_plugin_credentials",
            f"set exactly one of {BEARER_TOKEN_ENV} or {SERVICE_TOKEN_ENV}, not both",
            EXIT_CONFIG,
        )
    token = bearer or service
    if not token:
        raise PluginAcceptanceError(
            "missing_plugin_credentials",
            f"set exactly one of {BEARER_TOKEN_ENV} or {SERVICE_TOKEN_ENV}; tokens are not accepted in argv",
            EXIT_CONFIG,
        )
    if token.lower().startswith("bearer ") or _CONTROL_CHARS.search(token) or len(token) > 16_384:
        raise PluginAcceptanceError(
            "invalid_plugin_credentials",
            "plugin token must contain only the raw Bearer credential without whitespace/control characters",
            EXIT_CONFIG,
        )
    if any(char.isspace() for char in token):
        raise PluginAcceptanceError(
            "invalid_plugin_credentials",
            "plugin token must not contain whitespace",
            EXIT_CONFIG,
        )
    return token


def load_client_config(
    environ: Mapping[str, str],
    *,
    insecure: bool = False,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    retry: bool = False,
) -> ClientConfig:
    raw_base = environ.get(API_BASE_URL_ENV, "").strip()
    if not raw_base:
        raise PluginAcceptanceError(
            "missing_api_base_url", f"set {API_BASE_URL_ENV} in the environment", EXIT_CONFIG
        )
    if not 0.1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise PluginAcceptanceError(
            "invalid_timeout",
            f"timeout must be between 0.1 and {MAX_TIMEOUT_SECONDS:g} seconds",
            EXIT_CONFIG,
        )
    return ClientConfig(
        base_url=_normalise_base_url(raw_base),
        token=_read_token(environ),
        timeout_seconds=timeout_seconds,
        insecure=insecure,
        retry=retry,
    )


def _validate_company_id(company_id: str | None) -> str:
    value = (company_id or "").strip()
    if not _SAFE_COMPANY_ID.fullmatch(value):
        raise PluginAcceptanceError(
            "invalid_company_id",
            "company_id must match ^[a-z0-9][a-z0-9_-]{0,63}$",
            EXIT_CONFIG,
        )
    return value


def _validate_path_id(value: str, *, field_name: str) -> str:
    if not value or len(value) > 512 or _CONTROL_CHARS.search(value):
        raise PluginAcceptanceError(
            "invalid_path_parameter",
            f"{field_name} must be a non-empty bounded string without control characters",
            EXIT_PAYLOAD,
        )
    return value


def _object_pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _load_payload(path: Path, max_bytes: int) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    if not 1 <= max_bytes <= MAX_PAYLOAD_BYTES:
        raise PluginAcceptanceError(
            "invalid_payload_limit",
            f"max payload size must be between 1 and {MAX_PAYLOAD_BYTES} bytes",
            EXIT_CONFIG,
        )
    try:
        if not path.is_file():
            raise PluginAcceptanceError(
                "payload_not_a_file", "payload path must name a regular file", EXIT_PAYLOAD
            )
        declared = path.stat().st_size
        if declared > max_bytes:
            raise PluginAcceptanceError(
                "payload_too_large",
                f"payload exceeds the configured {max_bytes} byte limit",
                EXIT_PAYLOAD,
                details={"size_bytes": declared, "max_bytes": max_bytes},
            )
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
    except PluginAcceptanceError:
        raise
    except OSError as exc:
        raise PluginAcceptanceError(
            "payload_read_failed", f"cannot read payload file: {redact_text(str(exc))}", EXIT_PAYLOAD
        ) from exc
    if len(raw) > max_bytes:
        raise PluginAcceptanceError(
            "payload_too_large",
            f"payload exceeds the configured {max_bytes} byte limit",
            EXIT_PAYLOAD,
            details={"size_bytes": len(raw), "max_bytes": max_bytes},
        )
    try:
        text = raw.decode("utf-8-sig")
        parsed = json.loads(
            text,
            object_pairs_hook=_object_pairs_no_duplicates,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise PluginAcceptanceError(
            "malformed_payload_json", "payload is not valid bounded UTF-8 JSON", EXIT_PAYLOAD
        ) from exc
    if not isinstance(parsed, dict):
        raise PluginAcceptanceError(
            "payload_must_be_object", "typed capability payload must be a JSON object", EXIT_PAYLOAD
        )
    metadata = {
        "file": str(path),
        "size_bytes": len(raw),
        "max_bytes": max_bytes,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    return raw, parsed, metadata


def _prepare_request(
    *,
    action: str,
    config: ClientConfig,
    company_id: str | None,
    operation: str | None,
    payload_file: Path | None,
    job_id: str | None,
    max_payload_bytes: int,
    request_id: str,
) -> tuple[str, str, bytes | None, dict[str, str], dict[str, Any] | None]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {config.token}",
        "User-Agent": "prodavan-ops-plugin-acceptance/1",
        "X-Request-ID": request_id,
    }
    if action in {"status", "capabilities"}:
        company = _validate_company_id(company_id)
        return (
            "GET",
            f"/api/v1/admin/plugins/?company_id={quote(company, safe='')}",
            None,
            headers,
            None,
        )
    if action != "invoke":
        raise PluginAcceptanceError(
            "invalid_action", "action must be one of: status, capabilities, invoke", EXIT_CONFIG
        )
    if not operation or operation not in CAPABILITY_ROUTES:
        raise PluginAcceptanceError(
            "unknown_capability_operation",
            "operation must be selected from the closed typed capability registry",
            EXIT_CONFIG,
            details={"allowed_operations": sorted(CAPABILITY_ROUTES)},
        )
    if payload_file is None:
        raise PluginAcceptanceError(
            "missing_payload_file", "plugin invoke requires --payload with a bounded JSON object", EXIT_PAYLOAD
        )
    raw, parsed, metadata = _load_payload(payload_file, max_payload_bytes)
    route = CAPABILITY_ROUTES[operation]
    path = route.path
    if operation == "health" and parsed:
        raise PluginAcceptanceError(
            "health_payload_must_be_empty", "health accepts only an empty JSON object", EXIT_PAYLOAD
        )
    if operation == "objects.delete":
        key = parsed.get("key")
        if not isinstance(key, str):
            raise PluginAcceptanceError(
                "missing_object_key", "objects.delete payload must contain string key for the typed path", EXIT_PAYLOAD
            )
        path = path.replace("{key}", quote(_validate_path_id(key, field_name="key"), safe=""))
    elif "{job_id}" in path:
        if job_id is None:
            raise PluginAcceptanceError(
                "missing_job_id", f"{operation} requires --job-id", EXIT_PAYLOAD
            )
        path = path.replace("{job_id}", quote(_validate_path_id(job_id, field_name="job_id"), safe=""))
    elif job_id is not None:
        raise PluginAcceptanceError(
            "unexpected_job_id", "--job-id is valid only for jobs.get and jobs.cancel", EXIT_CONFIG
        )
    headers["Content-Type"] = "application/json; charset=utf-8"
    return route.method, path, raw, headers, metadata


def _response_body_preview(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    return {
        "body_text": redact_text(text[:8192]),
        "body_text_truncated": len(text) > 8192,
    }


def _read_bounded_response(response: httpx.Response) -> tuple[bytes, bool]:
    content_length = response.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_RESPONSE_BYTES:
        return b"", True
    body = bytearray()
    for chunk in response.iter_bytes():
        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
            return bytes(body), True
        body.extend(chunk)
    return bytes(body), False


def _parse_response(raw: bytes) -> Any:
    text = raw.decode("utf-8-sig")
    return json.loads(
        text,
        object_pairs_hook=_object_pairs_no_duplicates,
        parse_constant=_reject_json_constant,
    )


def _retryable_http(status: int) -> bool:
    return status in {502, 503, 504}


def _perform_http(
    *,
    client: httpx.Client,
    config: ClientConfig,
    method: str,
    path: str,
    body: bytes | None,
    headers: dict[str, str],
    sleep_fn: Any,
) -> tuple[Any, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    url = f"{config.base_url}{path}"
    max_attempts = 2 if config.retry else 1
    for number in range(1, max_attempts + 1):
        started_at = _iso_now()
        started = time.monotonic()
        response_record: dict[str, Any] | None = None
        failure: PluginAcceptanceError | None = None
        parsed: Any = None
        try:
            with client.stream(method, url, headers=headers, content=body) as response:
                raw, too_large = _read_bounded_response(response)
                response_record = {
                    "status": response.status_code,
                    "headers": sanitize_headers(response.headers),
                }
                if raw:
                    try:
                        parsed = _parse_response(raw)
                        response_record["body"] = redact_data(parsed)
                    except (UnicodeDecodeError, ValueError, RecursionError):
                        response_record.update(_response_body_preview(raw))
                        response_record["malformed_json"] = True
                else:
                    response_record["body"] = None
                if too_large:
                    failure = PluginAcceptanceError(
                        "response_too_large",
                        f"response exceeds the configured {MAX_RESPONSE_BYTES} byte limit",
                        EXIT_CONTRACT,
                    )
                elif not 200 <= response.status_code < 300:
                    failure = PluginAcceptanceError(
                        "http_status",
                        f"plugin acceptance request returned HTTP {response.status_code}",
                        EXIT_HTTP,
                        details={"status": response.status_code},
                    )
                elif response_record.get("malformed_json") or not isinstance(parsed, dict):
                    failure = PluginAcceptanceError(
                        "malformed_response_json",
                        "successful plugin response must be a bounded JSON object",
                        EXIT_CONTRACT,
                    )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            failure = PluginAcceptanceError(
                "transport_timeout" if isinstance(exc, httpx.TimeoutException) else "transport_error",
                f"plugin HTTP transport failed: {type(exc).__name__}: {redact_text(str(exc))}",
                EXIT_TRANSPORT,
            )
        except httpx.HTTPError as exc:
            failure = PluginAcceptanceError(
                "http_protocol_error",
                f"plugin HTTP protocol failed: {type(exc).__name__}: {redact_text(str(exc))}",
                EXIT_TRANSPORT,
            )
        finished_at = _iso_now()
        attempt: dict[str, Any] = {
            "number": number,
            "started_at": started_at,
            "finished_at": finished_at,
            "latency_ms": round((time.monotonic() - started) * 1000, 3),
            "response": response_record,
            "error": failure.as_dict() if failure is not None else None,
        }
        attempts.append(attempt)
        if failure is None:
            return parsed, attempts
        retryable = failure.code in {"transport_timeout", "transport_error"} or (
            failure.code == "http_status" and _retryable_http(failure.details["status"])
        )
        if not config.retry or number >= max_attempts or not retryable:
            failure.data = response_record
            failure.http_attempts = attempts
            raise failure
        sleep_fn(RETRY_DELAY_SECONDS)
    raise AssertionError("unreachable bounded retry loop")


def _validate_admin_response(data: Any, company_id: str | None) -> dict[str, Any]:
    company = _validate_company_id(company_id)
    if not isinstance(data, dict) or data.get("company_id") != company:
        raise PluginAcceptanceError(
            "admin_response_contract",
            "admin response company_id is missing or does not match the requested company",
            EXIT_CONTRACT,
            data=redact_data(data),
        )
    if not isinstance(data.get("items"), list) or not isinstance(data.get("enabled_modules"), list):
        raise PluginAcceptanceError(
            "admin_response_contract",
            "admin response must contain items and enabled_modules arrays",
            EXIT_CONTRACT,
            data=redact_data(data),
        )
    if not all(isinstance(value, str) for value in data["enabled_modules"]):
        raise PluginAcceptanceError(
            "admin_response_contract",
            "enabled_modules must contain only plugin id strings",
            EXIT_CONTRACT,
            data=redact_data(data),
        )
    for index, item in enumerate(data["items"]):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("plugin_id"), str)
            or not item["plugin_id"]
            or not isinstance(item.get("grant_id"), str)
            or not item["grant_id"]
            or not isinstance(item.get("grant_status"), str)
            or not isinstance(item.get("runtime_status"), str)
            or not isinstance(item.get("enabled"), bool)
            or not isinstance(item.get("capabilities"), list)
            or not all(isinstance(value, str) for value in item["capabilities"])
        ):
            raise PluginAcceptanceError(
                "admin_response_contract",
                "installed module is missing typed grant/runtime status fields",
                EXIT_CONTRACT,
                details={"item_index": index},
                data=redact_data(data),
            )
    return data


def _validate_capabilities(data: Any, company_id: str | None) -> dict[str, Any]:
    admin = _validate_admin_response(data, company_id)
    if not admin["items"]:
        raise PluginAcceptanceError(
            "grants_evidence_unavailable",
            "no installed modules are present; effective grants/quotas acceptance is not claimed",
            EXIT_CONTRACT,
            details={
                "evidence": {
                    "status": "incomplete",
                    "grants": "unavailable",
                    "quotas": "unavailable",
                }
            },
            data=redact_data(admin),
            evidence_status="incomplete",
        )
    missing_quotas: list[dict[str, Any]] = []
    for index, item in enumerate(admin["items"]):
        if not isinstance(item, dict):
            raise PluginAcceptanceError(
                "grants_evidence_invalid",
                "every installed module must be an object with effective grant fields",
                EXIT_CONTRACT,
                data=redact_data(admin),
            )
        if (
            not isinstance(item.get("plugin_id"), str)
            or not isinstance(item.get("grant_id"), str)
            or not isinstance(item.get("grant_status"), str)
            or not isinstance(item.get("capabilities"), list)
            or not all(isinstance(value, str) for value in item["capabilities"])
        ):
            raise PluginAcceptanceError(
                "grants_evidence_invalid",
                "installed module is missing typed effective grant evidence",
                EXIT_CONTRACT,
                details={"item_index": index},
                data=redact_data(admin),
            )
        quotas = item.get("quotas")
        if quotas is None:
            missing_quotas.append(
                {
                    "item_index": index,
                    "plugin_id": item["plugin_id"],
                    "grant_id": item["grant_id"],
                    "missing_fields": ["quotas"],
                }
            )
            continue
        if not isinstance(quotas, dict) or any(
            key not in quotas
            or not isinstance(quotas[key], int)
            or isinstance(quotas[key], bool)
            or quotas[key] < 1
            for key in _QUOTA_KEYS
        ):
            raise PluginAcceptanceError(
                "quotas_evidence_invalid",
                "effective quotas must contain all six positive integer limits; defaults are not evidence",
                EXIT_CONTRACT,
                details={"item_index": index, "required_quota_keys": list(_QUOTA_KEYS)},
                data=redact_data(admin),
            )
    if missing_quotas:
        raise PluginAcceptanceError(
            "quotas_evidence_unavailable",
            "effective quotas are absent from the admin response; no quota acceptance is claimed",
            EXIT_CONTRACT,
            details={
                "evidence": {
                    "status": "incomplete",
                    "grants": "available",
                    "quotas": "unavailable",
                },
                "missing": missing_quotas,
                "required_backend_contract": "read-only effective grants/quotas evidence",
            },
            data=redact_data(admin),
            evidence_status="incomplete",
        )
    return admin


def _validate_invoke(data: Any, operation: str | None) -> Any:
    if operation == "health":
        required = ("plugin_id", "company_id", "grant_id", "scopes", "exp")
        if any(key not in data for key in required):
            raise PluginAcceptanceError(
                "capability_response_contract",
                "plugin health response is missing non-secret PluginClaims evidence",
                EXIT_CONTRACT,
                data=redact_data(data),
            )
        return data
    decision = data.get("decision")
    if decision not in {"allow", "deny"} or not isinstance(data.get("data"), dict):
        raise PluginAcceptanceError(
            "capability_response_contract",
            "typed capability response must contain decision and data",
            EXIT_CONTRACT,
            data=redact_data(data),
        )
    if decision == "deny":
        raise PluginAcceptanceError(
            "capability_denied",
            "plugin capability was denied; HTTP 2xx is not treated as acceptance",
            EXIT_CAPABILITY_DENIED,
            data=redact_data(data),
        )
    return data


def execute_action(
    action: str,
    *,
    environ: Mapping[str, str] | None = None,
    company_id: str | None = None,
    operation: str | None = None,
    payload_file: Path | None = None,
    job_id: str | None = None,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    insecure: bool = False,
    retry: bool = False,
    transport: httpx.BaseTransport | None = None,
    client_factory: Any = None,
    sleep_fn: Any = time.sleep,
    stderr: TextIO | None = None,
) -> CommandResult:
    """Execute one bounded acceptance action and return a sanitised verdict.

    Expected failures are values, not tracebacks. This lets plugin evidence
    persist failed/incomplete evidence before returning the same non-zero exit.
    """

    env = os.environ if environ is None else environ
    error_stream = sys.stderr if stderr is None else stderr
    request_id = str(uuid4())
    started_at = _iso_now()
    started = time.monotonic()
    base_url = ""
    method = ""
    path = ""
    request_headers: dict[str, str] = {}
    payload_metadata: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = []
    data: Any = None
    failure: PluginAcceptanceError | None = None
    sensitive_values: tuple[str, ...] = ()
    if insecure:
        print(
            "WARNING: --insecure disables TLS certificate verification; "
            "use only with an explicitly trusted self-signed dev endpoint.",
            file=error_stream,
        )
    try:
        config = load_client_config(
            env,
            insecure=insecure,
            timeout_seconds=timeout_seconds,
            retry=retry,
        )
        base_url = config.base_url
        sensitive_values = (config.token,)
        method, path, body, request_headers, payload_metadata = _prepare_request(
            action=action,
            config=config,
            company_id=company_id,
            operation=operation,
            payload_file=payload_file,
            job_id=job_id,
            max_payload_bytes=max_payload_bytes,
            request_id=request_id,
        )
        timeout = httpx.Timeout(config.timeout_seconds)
        factory = httpx.Client if client_factory is None else client_factory
        with factory(
            timeout=timeout,
            verify=not config.insecure,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            data, attempts = _perform_http(
                client=client,
                config=config,
                method=method,
                path=path,
                body=body,
                headers=request_headers,
                sleep_fn=sleep_fn,
            )
        if action == "status":
            data = _validate_admin_response(data, company_id)
        elif action == "capabilities":
            data = _validate_capabilities(data, company_id)
        else:
            data = _validate_invoke(data, operation)
        data = redact_data(data)
    except PluginAcceptanceError as exc:
        failure = exc
        data = exc.data
        if exc.http_attempts is not None:
            attempts = exc.http_attempts
    finished_at = _iso_now()
    return CommandResult(
        action=action,
        ok=failure is None,
        exit_code=0 if failure is None else failure.exit_code,
        request_id=request_id,
        started_at=started_at,
        finished_at=finished_at,
        latency_ms=round((time.monotonic() - started) * 1000, 3),
        evidence_status="complete" if failure is None else failure.evidence_status,
        base_url=base_url,
        method=method,
        path=path,
        request_headers=sanitize_headers(request_headers),
        payload=redact_data(payload_metadata, sensitive_values=sensitive_values),
        attempts=redact_data(attempts, sensitive_values=sensitive_values),
        data=redact_data(data, sensitive_values=sensitive_values),
        error=failure,
        sensitive_values=sensitive_values,
    )


def _validate_evidence_path(path: Path) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=False)
        docs = (repo_root() / "docs").resolve(strict=False)
    except OSError as exc:
        raise PluginAcceptanceError(
            "invalid_evidence_path", f"cannot resolve evidence path: {redact_text(str(exc))}", EXIT_EVIDENCE_IO
        ) from exc
    if resolved == docs or resolved.is_relative_to(docs):
        raise PluginAcceptanceError(
            "evidence_in_repo_docs_forbidden",
            "plugin evidence must not be written into repository docs; choose an external evidence file",
            EXIT_EVIDENCE_IO,
        )
    if path.is_symlink() or resolved.exists() and not resolved.is_file():
        raise PluginAcceptanceError(
            "invalid_evidence_path", "evidence output must be a regular non-symlink file", EXIT_EVIDENCE_IO
        )
    if not resolved.parent.is_dir():
        raise PluginAcceptanceError(
            "invalid_evidence_path", "evidence output parent directory must already exist", EXIT_EVIDENCE_IO
        )
    return resolved


def write_evidence(result: CommandResult, path: Path) -> Path:
    """Atomically write exactly one JSON evidence file (never repository docs)."""

    destination = _validate_evidence_path(path)
    serialised = json.dumps(
        result.evidence_document(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=str(destination.parent), prefix=f".{destination.name}.", suffix=".tmp"
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(serialised)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
        temporary_name = ""
    except OSError as exc:
        raise PluginAcceptanceError(
            "evidence_write_failed",
            f"cannot write evidence JSON: {redact_text(str(exc))}",
            EXIT_EVIDENCE_IO,
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
    return destination


__all__ = [
    "API_BASE_URL_ENV",
    "BEARER_TOKEN_ENV",
    "CAPABILITY_ROUTES",
    "DEFAULT_MAX_PAYLOAD_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "EXIT_CAPABILITY_DENIED",
    "EXIT_CONFIG",
    "EXIT_CONTRACT",
    "EXIT_EVIDENCE_IO",
    "EXIT_HTTP",
    "EXIT_PAYLOAD",
    "EXIT_TRANSPORT",
    "MAX_PAYLOAD_BYTES",
    "MAX_RESPONSE_BYTES",
    "MAX_TIMEOUT_SECONDS",
    "SERVICE_TOKEN_ENV",
    "ClientConfig",
    "CommandResult",
    "PluginAcceptanceError",
    "execute_action",
    "load_client_config",
    "redact_data",
    "redact_text",
    "sanitize_headers",
    "write_evidence",
]
