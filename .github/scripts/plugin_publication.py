#!/usr/bin/env python3
"""Fail-closed GitHub Actions CI -> platform plugin publication.

Protected environment configuration supplies the platform endpoint and the
provisioned ModuleSource mapping. This script never accepts source_id, caller
capabilities, approval, or scan/CSP boolean assertions as CLI input. A platform
POST is attempted only after release verification, a real pinned-scanner report,
and a real static-origin response have all been validated for the release
artifact. GitHub OIDC tokens are transported, never logged or decoded locally;
the backend remains the cryptographic and source-binding authority.

Importing this module performs no process, network, or registry operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

OIDC_AUDIENCE = "agentscale-plugin-ci"
PUBLICATION_PATH = "/internal/plugin-artifacts"
CONFIG_ENV = "PLUGIN_PLATFORM_PUBLICATION_CONFIG"
DEV_HTTP_ALLOWLIST_ENV = "PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_SCAN_REPORT_BYTES = 32 * 1024 * 1024
MAX_EVIDENCE_FILE_BYTES = 16 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 15.0
SCAN_TIMEOUT_SECONDS = 900
SCANNER_IMAGE_RE = re.compile(
    r"^aquasec/trivy:[0-9]+\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{64}$"
)
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SOURCE_ID_RE = re.compile(r"^\S{1,80}$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,95}$")
OCI_REF_RE = re.compile(
    r"^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]+)?"
    r"/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"@sha256:[0-9a-f]{64}$"
)
SEMVER_RE = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
CANONICAL_REPOSITORY = "ne-tort/agentscale"
TRUSTED_WORKFLOW_PREFIX = (
    "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@"
)
PROTECTED_REF_RE = re.compile(r"^refs/heads/[A-Za-z0-9._/-]{1,255}$")
TRUSTED_WORKFLOW_RE = re.compile(
    r"^ne-tort/agentscale/[.]github/workflows/plugin-artifacts[.]yml@"
    r"(?:[0-9a-f]{40}|refs/heads/[A-Za-z0-9._/-]{1,255})$"
)
RELEASE_EVENTS = frozenset({"push", "workflow_dispatch", "workflow_call"})


def _trusted_workflow_matches(
    workflow: Any, *, source_ref: str, repository: str
) -> bool:
    """Accept a reusable full SHA, or canonical direct protected branch identity."""
    if not isinstance(workflow, str) or TRUSTED_WORKFLOW_RE.fullmatch(workflow) is None:
        return False
    workflow_ref = workflow.rsplit("@", 1)[1]
    return SHA_RE.fullmatch(workflow_ref) is not None or (
        repository == CANONICAL_REPOSITORY
        and PROTECTED_REF_RE.fullmatch(source_ref) is not None
        and workflow_ref == source_ref
    )


REQUIRED_RELEASE_CHECKS = frozenset(
    {
        "digest_pinned_refs",
        "cosign_verify:oci-artifact",
        "cosign_verify:backend-image",
        "canonical_manifest_config_and_blob",
        "backend_bundle_link",
        "attestation:backend:sbom",
        "attestation:backend:provenance",
        "attestation:bundle:sbom",
        "attestation:bundle:provenance",
        "gate:semver",
        "gate:protocol",
        "gate:capability_allowlist",
        "gate:platform_version",
        "gate:capability_grant",
        "gate:min_platform_version",
    }
)
REQUIRED_CSP_DIRECTIVES = frozenset(
    {"default-src", "base-uri", "object-src", "frame-ancestors"}
)
UNTRUSTED_EVIDENCE_KEYS = frozenset({"scan_verified", "csp_verified"})
RELEASE_EVIDENCE_SCHEMA_VERSION = 1
RELEASE_EVIDENCE_MAX_BYTES = 1024 * 1024


class PublicationBlocked(Exception):
    """Safe typed failure; details never contain tokens, URLs, or response bodies."""

    def __init__(self, code: str, *, status: str = "BLOCKED") -> None:
        if CODE_RE.fullmatch(code) is None:
            code = "PUBLICATION_FAILED"
        self.code = code
        self.status = status
        super().__init__(code)


class TransportFailure(Exception):
    """Internal transport failure; callers map it to a safe typed code."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class UrllibTransport:
    """No environment proxies, no redirects, fixed timeout and bounded reads."""

    trust_env = False

    def __init__(self, *, timeout: float = HTTP_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout
        self._opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None = None,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> HttpResponse:
        req = request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with self._opener.open(req, timeout=self._timeout) as response:
                return HttpResponse(
                    status=response.status,
                    headers=_normalise_response_headers(response.headers.items()),
                    body=_read_response_bounded(response, max_bytes),
                )
        except error.HTTPError as exc:
            try:
                return HttpResponse(
                    status=exc.code,
                    headers=_normalise_response_headers(exc.headers.items()),
                    body=_read_response_bounded(exc, max_bytes),
                )
            except Exception as exc:
                raise TransportFailure() from exc
        except PublicationBlocked:
            raise
        except Exception as exc:
            raise TransportFailure() from exc


def _read_response_bounded(response: Any, maximum: int) -> bytes:
    raw_length = response.headers.get("Content-Length") if response.headers else None
    if raw_length is not None:
        try:
            if int(raw_length) > maximum:
                raise TransportFailure()
        except ValueError:
            raise TransportFailure()
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise TransportFailure()
    return data


def _normalise_response_headers(items: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_value in items:
        name = str(raw_name).lower()
        value = str(raw_value).strip()
        if name in result:
            result[name] = result[name] + "," + value
        else:
            result[name] = value
    return result


def _send(
    transport: Any,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    body: bytes | None = None,
    unavailable_code: str,
) -> HttpResponse:
    try:
        response = transport.send(
            method,
            url,
            headers=headers,
            body=body,
            max_bytes=MAX_RESPONSE_BYTES,
        )
    except PublicationBlocked:
        raise
    except Exception as exc:
        raise PublicationBlocked(unavailable_code) from exc
    if 300 <= response.status <= 399:
        raise PublicationBlocked("REDIRECT_REJECTED")
    return response


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json_loads(data: bytes | str, *, code: str) -> Any:
    try:
        return json.loads(
            data,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationBlocked(code) from exc


def _read_bounded(path: str, *, maximum: int, code: str) -> bytes:
    try:
        candidate = Path(path)
        if candidate.is_symlink() or not candidate.is_file():
            raise PublicationBlocked(code)
        with candidate.open("rb") as handle:
            data = handle.read(maximum + 1)
        if len(data) > maximum:
            raise PublicationBlocked(code)
        return data
    except PublicationBlocked:
        raise
    except OSError as exc:
        raise PublicationBlocked(code) from exc


def _read_json(path: str, *, maximum: int, code: str) -> Any:
    return _json_loads(_read_bounded(path, maximum=maximum, code=code), code=code)


def _file_digest(path: str) -> str:
    return "sha256:" + hashlib.sha256(
        _read_bounded(path, maximum=MAX_EVIDENCE_FILE_BYTES, code="EVIDENCE_FILE_INVALID")
    ).hexdigest()


def _validate_quarantine_refs(
    *, bundle_ref: str, bundle_digest: str, backend_ref: str, backend_digest: str
) -> None:
    """Digest-pinned quarantine pair; namespace mirror of _validate_release_refs."""
    if (
        OCI_REF_RE.fullmatch(bundle_ref) is None
        or OCI_REF_RE.fullmatch(backend_ref) is None
        or DIGEST_RE.fullmatch(bundle_digest) is None
        or DIGEST_RE.fullmatch(backend_digest) is None
        or not bundle_ref.endswith("@" + bundle_digest)
        or not backend_ref.endswith("@" + backend_digest)
        or bundle_ref == backend_ref
        or bundle_digest == backend_digest
        or "/plugins/" in bundle_ref
        or "/plugins/" in backend_ref
        or "/plugins-quarantine/" not in bundle_ref
        or "/plugins-quarantine/" not in backend_ref
    ):
        raise PublicationBlocked("RELEASE_REFS_INVALID")
    backend_repository = backend_ref.rsplit("@", 1)[0]
    bundle_repository = bundle_ref.rsplit("@", 1)[0]
    if not backend_repository.endswith("/backend") or bundle_repository != (
        backend_repository[:-8] + "/bundle"
    ):
        raise PublicationBlocked("RELEASE_REFS_INVALID")


def build_release_evidence_document(
    *,
    source: SourceBinding,
    context: GitHubContext,
    plugin_id: str,
    version: str,
    quarantine_bundle_ref: str,
    quarantine_bundle_digest: str,
    quarantine_backend_ref: str,
    quarantine_backend_digest: str,
    release_bundle_ref: str,
    release_bundle_digest: str,
    release_backend_ref: str,
    release_backend_digest: str,
    scanner_image: str,
    scan_report_path: str,
    scan_report_sha256: str,
    csp_url: str,
    csp_status: int,
    csp_headers: Mapping[str, str],
) -> dict[str, Any]:
    """Canonical bounded evidence snapshot; single source of truth for keys."""
    origin = source.static_origin
    if origin is None:
        raise PublicationBlocked("CSP_EVIDENCE_REQUIRED")
    headers: dict[str, str] = {}
    for name in list(origin.security_headers) + ["x-agentscale-bundle-digest"]:
        value = csp_headers.get(name)
        if not isinstance(value, str) or not value:
            raise PublicationBlocked("CSP_SECURITY_HEADERS_FAILED")
        headers[name] = value
    return {
        "schema_version": RELEASE_EVIDENCE_SCHEMA_VERSION,
        "plugin_id": plugin_id,
        "version": version,
        "repository": context.repository,
        "commit_sha": context.commit_sha,
        "builder_identity": context.builder_identity,
        "source_id": source.source_id,
        "company_id": source.company_id,
        "scanner_image": scanner_image,
        "quarantine": {
            "bundle_ref": quarantine_bundle_ref,
            "bundle_digest": quarantine_bundle_digest,
            "backend_ref": quarantine_backend_ref,
            "backend_digest": quarantine_backend_digest,
        },
        "release": {
            "bundle_ref": release_bundle_ref,
            "bundle_digest": release_bundle_digest,
            "backend_ref": release_backend_ref,
            "backend_digest": release_backend_digest,
        },
        "scan_report": {"path": scan_report_path, "sha256": scan_report_sha256},
        "csp": {
            "url": csp_url,
            "status": csp_status,
            "bundle_digest": quarantine_bundle_digest,
            "headers": headers,
        },
    }


def _validate_release_evidence(
    document: Any,
    *,
    config: PublicationConfig,
    source: SourceBinding,
    context: GitHubContext,
    plugin_id: str,
    version: str,
    bundle_ref: str,
    bundle_digest: str,
    backend_ref: str,
    backend_digest: str,
    scan_report_sha256: str,
) -> None:
    """Strict revalidation of pre-promotion evidence against current inputs."""
    _reject_self_asserted_evidence(document, code="RELEASE_EVIDENCE_UNTRUSTED")
    expected_keys = {
        "schema_version",
        "plugin_id",
        "version",
        "repository",
        "commit_sha",
        "builder_identity",
        "source_id",
        "company_id",
        "scanner_image",
        "quarantine",
        "release",
        "scan_report",
        "csp",
    }
    if not isinstance(document, dict) or set(document) != expected_keys:
        raise PublicationBlocked("RELEASE_EVIDENCE_INVALID")
    if (
        document["schema_version"] != RELEASE_EVIDENCE_SCHEMA_VERSION
        or isinstance(document["schema_version"], bool)
        or document["plugin_id"] != plugin_id
        or document["version"] != version
        or document["repository"] != context.repository
        or document["commit_sha"] != context.commit_sha
        or document["builder_identity"] != context.builder_identity
        or document["source_id"] != source.source_id
        or document["company_id"] != source.company_id
        or document["scanner_image"] != config.scanner_image
    ):
        raise PublicationBlocked("RELEASE_EVIDENCE_INVALID")
    pair_keys = {"bundle_ref", "bundle_digest", "backend_ref", "backend_digest"}
    quarantine = document["quarantine"]
    release = document["release"]
    for pair in (quarantine, release):
        if not isinstance(pair, dict) or set(pair) != pair_keys:
            raise PublicationBlocked("RELEASE_EVIDENCE_INVALID")
    try:
        _validate_quarantine_refs(
            bundle_ref=quarantine["bundle_ref"],
            bundle_digest=quarantine["bundle_digest"],
            backend_ref=quarantine["backend_ref"],
            backend_digest=quarantine["backend_digest"],
        )
        _validate_release_refs(
            bundle_ref=release["bundle_ref"],
            bundle_digest=release["bundle_digest"],
            backend_ref=release["backend_ref"],
            backend_digest=release["backend_digest"],
        )
    except PublicationBlocked as exc:
        raise PublicationBlocked("RELEASE_EVIDENCE_INVALID") from exc
    if (
        release["bundle_ref"] != bundle_ref
        or release["bundle_digest"] != bundle_digest
        or release["backend_ref"] != backend_ref
        or release["backend_digest"] != backend_digest
        # Promotion is a repository copy: immutable digests are identical.
        or quarantine["bundle_digest"] != release["bundle_digest"]
        or quarantine["backend_digest"] != release["backend_digest"]
    ):
        raise PublicationBlocked("RELEASE_EVIDENCE_INVALID")
    scan_report = document["scan_report"]
    if (
        not isinstance(scan_report, dict)
        or set(scan_report) != {"path", "sha256"}
        or not isinstance(scan_report["path"], str)
        or not scan_report["path"]
        or scan_report["sha256"] != scan_report_sha256
    ):
        raise PublicationBlocked("RELEASE_EVIDENCE_INVALID")
    origin = source.static_origin
    if origin is None:
        raise PublicationBlocked("CSP_EVIDENCE_REQUIRED")
    csp = document["csp"]
    if not isinstance(csp, dict) or set(csp) != {"url", "status", "bundle_digest", "headers"}:
        raise PublicationBlocked("RELEASE_EVIDENCE_INVALID")
    expected_header_names = set(origin.security_headers) | {"x-agentscale-bundle-digest"}
    if (
        csp["bundle_digest"] != bundle_digest
        or csp["url"]
        != origin.url_for(plugin_id=plugin_id, version=version, bundle_digest=bundle_digest)
        or csp["status"] != 200
        or isinstance(csp["status"], bool)
        or not isinstance(csp["headers"], dict)
        or set(csp["headers"]) != expected_header_names
        or any(
            not isinstance(value, str) or not value
            for value in csp["headers"].values()
        )
    ):
        raise PublicationBlocked("RELEASE_EVIDENCE_INVALID")
    response = HttpResponse(status=200, headers=dict(csp["headers"]), body=b"")
    validate_csp_response(response, source=source, bundle_digest=bundle_digest)


def _exact_keys(value: Any, expected: set[str], *, code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise PublicationBlocked(code)
    return value


def _safe_text(value: Any, *, maximum: int, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(char) < 32 for char in value)
    ):
        raise PublicationBlocked(code)
    return value


def _split_url(raw: str, *, code: str) -> parse.SplitResult:
    try:
        parsed = parse.urlsplit(raw)
        parsed.port
    except ValueError as exc:
        raise PublicationBlocked(code) from exc
    return parsed


def _validate_https_url(raw: str, *, code: str, allow_query: bool = False) -> parse.SplitResult:
    parsed = _split_url(raw, code=code)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "#" in raw
        or (not allow_query and "?" in raw)
        or any(ord(char) < 32 for char in raw)
    ):
        raise PublicationBlocked(code)
    return parsed


def _normalise_base_url(raw: str, dev_allowlist: frozenset[str]) -> str:
    candidate = _safe_text(raw, maximum=2048, code="ENDPOINT_INVALID").rstrip("/")
    parsed = _split_url(candidate, code="ENDPOINT_INVALID")
    common_invalid = (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "?" in candidate
        or "#" in candidate
        or any(part in {".", ".."} for part in parsed.path.split("/"))
        or parsed.path.endswith(PUBLICATION_PATH)
    )
    if common_invalid:
        raise PublicationBlocked("ENDPOINT_INVALID")
    if parsed.scheme == "http":
        if candidate not in dev_allowlist:
            raise PublicationBlocked("HTTPS_REQUIRED")
    elif parsed.scheme != "https":
        raise PublicationBlocked("HTTPS_REQUIRED")
    return candidate


def _parse_dev_allowlist(raw: str) -> frozenset[str]:
    if not raw:
        return frozenset()
    document = _json_loads(raw, code="DEV_ALLOWLIST_INVALID")
    if not isinstance(document, list) or len(document) > 32:
        raise PublicationBlocked("DEV_ALLOWLIST_INVALID")
    result: set[str] = set()
    for value in document:
        item = _safe_text(value, maximum=2048, code="DEV_ALLOWLIST_INVALID").rstrip("/")
        parsed = _split_url(item, code="DEV_ALLOWLIST_INVALID")
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in item
            or "#" in item
            or "*" in item
            or item in result
        ):
            raise PublicationBlocked("DEV_ALLOWLIST_INVALID")
        result.add(item)
    return frozenset(result)


def _static_origin_url_allowed(
    raw: str, *, dev_allowlist: frozenset[str], code: str
) -> parse.SplitResult:
    """Static-origin URL gate: HTTPS is unchanged; HTTP needs an exact origin.

    Plain HTTP is accepted only when the canonical http://host[:port] origin
    is present in the dev allowlist. Userinfo, query, fragment, wildcards and
    any other scheme stay fail-closed for both schemes. Callers validate the
    template probe and the formatted URL, so format fields cannot move a
    probe onto another origin.
    """
    parsed = _split_url(raw, code=code)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "#" in raw
        or "?" in raw
        or any(ord(char) < 32 for char in raw)
    ):
        raise PublicationBlocked(code)
    if parsed.scheme == "https":
        return parsed
    if parsed.scheme != "http":
        raise PublicationBlocked(code)
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    origin = f"http://{host}" + (f":{parsed.port}" if parsed.port is not None else "")
    if not dev_allowlist or origin not in dev_allowlist:
        raise PublicationBlocked(code)
    return parsed


def _exact_https_origin(raw: Any, *, code: str) -> str:
    candidate = _safe_text(raw, maximum=2048, code=code)
    if "*" in candidate:
        raise PublicationBlocked(code)
    parsed = _validate_https_url(candidate, code=code)
    hostname = parsed.hostname
    if (
        not hostname
        or not hostname.isascii()
        or hostname != hostname.lower()
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise PublicationBlocked(code)
    host = f"[{hostname}]" if ":" in hostname else hostname
    expected_netloc = host + (f":{parsed.port}" if parsed.port is not None else "")
    without_trailing_slash = candidate[:-1] if candidate.endswith("/") else candidate
    if without_trailing_slash != f"https://{expected_netloc}":
        raise PublicationBlocked(code)
    port = "" if parsed.port is None or parsed.port == 443 else f":{parsed.port}"
    return f"https://{host}{port}"


def _static_security_headers(shell_origin: str) -> dict[str, str]:
    return {
        "content-security-policy": (
            "default-src 'self'; base-uri 'none'; object-src 'none'; "
            f"frame-ancestors {shell_origin}"
        ),
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
        "permissions-policy": "camera=(), microphone=(), geolocation=()",
    }


@dataclass(frozen=True, slots=True)
class StaticOriginConfig:
    url_template: str
    plugin_shell_origin: str
    security_headers: Mapping[str, str]
    dev_allowlist: frozenset[str] = frozenset()

    @classmethod
    def parse(cls, value: Any, *, dev_allowlist: frozenset[str] = frozenset()) -> StaticOriginConfig:
        document = _exact_keys(
            value,
            {"url_template", "plugin_shell_origin", "security_headers"},
            code="CSP_CONFIG_INVALID",
        )
        shell_origin = _exact_https_origin(
            document["plugin_shell_origin"], code="CSP_CONFIG_INVALID"
        )
        template = _safe_text(
            document["url_template"], maximum=2048, code="CSP_CONFIG_INVALID"
        )
        try:
            fields = [field for _, field, _, _ in string.Formatter().parse(template) if field]
        except ValueError as exc:
            raise PublicationBlocked("CSP_CONFIG_INVALID") from exc
        if "bundle_digest" not in fields or any(
            field not in {"plugin_id", "version", "bundle_digest"} for field in fields
        ):
            raise PublicationBlocked("CSP_CONFIG_INVALID")
        try:
            probe = template.format(
                plugin_id="plugin", version="0.0.0",
                bundle_digest="sha256:" + "0" * 64,
            )
        except (KeyError, ValueError) as exc:
            raise PublicationBlocked("CSP_CONFIG_INVALID") from exc
        static = _static_origin_url_allowed(
            probe, dev_allowlist=dev_allowlist, code="CSP_CONFIG_INVALID"
        )
        shell = _split_url(shell_origin, code="CSP_CONFIG_INVALID")
        if (
            static.hostname == shell.hostname
            and (static.port or 443) == (shell.port or 443)
        ):
            raise PublicationBlocked("CSP_CONFIG_INVALID")
        raw_headers = document["security_headers"]
        if not isinstance(raw_headers, dict):
            raise PublicationBlocked("CSP_CONFIG_INVALID")
        headers: dict[str, str] = {}
        for raw_name, raw_value in raw_headers.items():
            name = _safe_text(raw_name, maximum=128, code="CSP_CONFIG_INVALID").lower()
            if name in headers:
                raise PublicationBlocked("CSP_CONFIG_INVALID")
            headers[name] = _safe_text(raw_value, maximum=4096, code="CSP_CONFIG_INVALID")
        expected_headers = _static_security_headers(shell_origin)
        if set(headers) != set(expected_headers) or headers != expected_headers:
            raise PublicationBlocked("CSP_CONFIG_INVALID")
        _validate_csp(headers["content-security-policy"], code="CSP_CONFIG_INVALID")
        return cls(
            url_template=template,
            plugin_shell_origin=shell_origin,
            security_headers=headers,
            dev_allowlist=dev_allowlist,
        )

    def url_for(self, *, plugin_id: str, version: str, bundle_digest: str) -> str:
        try:
            url = self.url_template.format(
                plugin_id=plugin_id, version=version, bundle_digest=bundle_digest
            )
        except (KeyError, ValueError) as exc:
            raise PublicationBlocked("CSP_CONFIG_INVALID") from exc
        parsed = _static_origin_url_allowed(
            url, dev_allowlist=self.dev_allowlist, code="CSP_ORIGIN_INVALID"
        )
        if bundle_digest not in parsed.path:
            raise PublicationBlocked("CSP_ORIGIN_INVALID")
        return url


def _validate_csp(value: str, *, code: str) -> None:
    directives: dict[str, list[str]] = {}
    for part in value.split(";"):
        tokens = part.strip().split()
        if not tokens:
            continue
        name = tokens[0].lower()
        if name in directives:
            raise PublicationBlocked(code)
        directives[name] = tokens[1:]
    if not REQUIRED_CSP_DIRECTIVES <= set(directives):
        raise PublicationBlocked(code)
    if directives["base-uri"] != ["'none'"] or directives["object-src"] != ["'none'"]:
        raise PublicationBlocked(code)
    if not directives["frame-ancestors"] or "*" in directives["frame-ancestors"]:
        raise PublicationBlocked(code)


@dataclass(frozen=True, slots=True)
class SourceBinding:
    source_id: str
    company_id: str
    plugin_id: str
    repository: str
    repository_id: int
    ref: str
    trusted_workflow_ref: str
    static_origin: StaticOriginConfig | None

    @classmethod
    def parse(cls, value: Any, *, dev_allowlist: frozenset[str] = frozenset()) -> SourceBinding:
        document = _exact_keys(
            value,
            {
                "source_id",
                "company_id",
                "plugin_id",
                "repository",
                "repository_id",
                "ref",
                "trusted_workflow_ref",
                "static_origin",
            },
            code="SOURCE_CONFIG_INVALID",
        )
        source_id = _safe_text(
            document["source_id"], maximum=80, code="SOURCE_CONFIG_INVALID"
        )
        company_id = _safe_text(
            document["company_id"], maximum=40, code="SOURCE_CONFIG_INVALID"
        )
        plugin_id = document["plugin_id"]
        repository = document["repository"]
        repository_id = document["repository_id"]
        ref = document["ref"]
        workflow = document["trusted_workflow_ref"]
        if (
            SOURCE_ID_RE.fullmatch(source_id) is None
            or PLUGIN_ID_RE.fullmatch(str(plugin_id)) is None
            or REPOSITORY_RE.fullmatch(str(repository)) is None
            or isinstance(repository_id, bool)
            or not isinstance(repository_id, int)
            or repository_id <= 0
            or not isinstance(ref, str)
            or PROTECTED_REF_RE.fullmatch(ref) is None
            or not _trusted_workflow_matches(
                workflow, source_ref=ref, repository=str(repository)
            )
        ):
            raise PublicationBlocked("SOURCE_CONFIG_INVALID")
        raw_origin = document["static_origin"]
        origin = (
            None
            if raw_origin is None
            else StaticOriginConfig.parse(raw_origin, dev_allowlist=dev_allowlist)
        )
        return cls(
            source_id=source_id,
            company_id=company_id,
            plugin_id=str(plugin_id),
            repository=str(repository),
            repository_id=repository_id,
            ref=ref,
            trusted_workflow_ref=str(workflow),
            static_origin=origin,
        )


@dataclass(frozen=True, slots=True)
class PublicationConfig:
    publication_base_url: str
    scanner_image: str
    sources: tuple[SourceBinding, ...]

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> PublicationConfig:
        raw = environ.get(CONFIG_ENV, "")
        if not raw or len(raw.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise PublicationBlocked("PROTECTED_CONFIG_REQUIRED")
        document = _exact_keys(
            _json_loads(raw, code="PROTECTED_CONFIG_INVALID"),
            {"schema_version", "publication_base_url", "scanner_image", "sources"},
            code="PROTECTED_CONFIG_INVALID",
        )
        if document["schema_version"] != 1 or isinstance(document["schema_version"], bool):
            raise PublicationBlocked("PROTECTED_CONFIG_INVALID")
        allowlist = _parse_dev_allowlist(environ.get(DEV_HTTP_ALLOWLIST_ENV, ""))
        base_url = _normalise_base_url(document["publication_base_url"], allowlist)
        scanner_image = document["scanner_image"]
        if not isinstance(scanner_image, str) or SCANNER_IMAGE_RE.fullmatch(scanner_image) is None:
            raise PublicationBlocked("SCANNER_CONFIG_REQUIRED")
        raw_sources = document["sources"]
        if not isinstance(raw_sources, list) or len(raw_sources) > 256:
            raise PublicationBlocked("SOURCE_CONFIG_INVALID")
        sources = tuple(
            SourceBinding.parse(item, dev_allowlist=allowlist) for item in raw_sources
        )
        identities = [(item.source_id, item.company_id, item.plugin_id, item.repository) for item in sources]
        if len(set(identities)) != len(identities) or len({item.source_id for item in sources}) != len(
            sources
        ):
            raise PublicationBlocked("SOURCE_CONFIG_INVALID")
        return cls(
            publication_base_url=base_url,
            scanner_image=scanner_image,
            sources=sources,
        )

    @property
    def publication_url(self) -> str:
        return self.publication_base_url + PUBLICATION_PATH


@dataclass(frozen=True, slots=True)
class ReleaseTrust:
    """Trusted release facts derived before OIDC or any registry write."""

    builder_identity: str
    expected_repository: str
    expected_repository_id: str
    source_ref: str
    source_sha: str
    event_name: str

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> ReleaseTrust:
        repository = environ.get("GITHUB_REPOSITORY", "")
        repository_id = environ.get("GITHUB_REPOSITORY_ID", "")
        event_name = environ.get("GITHUB_EVENT_NAME", "")
        source_ref = environ.get("GITHUB_REF", "")
        source_sha = environ.get("GITHUB_SHA", "")
        input_workflow_ref = environ.get("INPUT_WORKFLOW_REF", "")
        workflow_sha = environ.get("WORKFLOW_SHA", "")
        direct = repository == CANONICAL_REPOSITORY and not input_workflow_ref
        workflow_ref = source_ref if direct else workflow_sha
        builder_identity = TRUSTED_WORKFLOW_PREFIX + workflow_ref
        expected_repository = (
            CANONICAL_REPOSITORY if direct else environ.get("EXPECTED_REPOSITORY", "")
        )
        expected_repository_id = environ.get("EXPECTED_REPOSITORY_ID", "")
        expected_ref = environ.get("EXPECTED_REF", "")
        default_branch = environ.get("DEFAULT_BRANCH", "")
        if (
            event_name not in RELEASE_EVENTS
            or REPOSITORY_RE.fullmatch(repository) is None
            or repository != expected_repository
            or not expected_repository_id.isdigit()
            or int(expected_repository_id) <= 0
            or repository_id != expected_repository_id
            or PROTECTED_REF_RE.fullmatch(source_ref) is None
            or source_ref != expected_ref
            or source_ref != f"refs/heads/{default_branch}"
            or SHA_RE.fullmatch(source_sha) is None
            or (
                not direct
                and (
                    SHA_RE.fullmatch(workflow_sha) is None
                    or input_workflow_ref != builder_identity
                )
            )
        ):
            raise PublicationBlocked("GITHUB_SOURCE_CONTEXT_INVALID")
        return cls(
            builder_identity=builder_identity,
            expected_repository=expected_repository,
            expected_repository_id=expected_repository_id,
            source_ref=source_ref,
            source_sha=source_sha,
            event_name=event_name,
        )


def validate_release_oidc_claims(payload: Any, *, trust: ReleaseTrust) -> None:
    """Check local fail-closed claims; the backend remains the OIDC authority."""
    if not isinstance(payload, dict):
        raise PublicationBlocked("OIDC_RESPONSE_INVALID")
    expected = {
        "iss": "https://token.actions.githubusercontent.com",
        "repository": trust.expected_repository,
        "repository_id": trust.expected_repository_id,
        "ref": trust.source_ref,
        "sha": trust.source_sha,
        "event_name": trust.event_name,
        "job_workflow_ref": trust.builder_identity,
    }
    audience = payload.get("aud")
    if (
        any(payload.get(claim) != value for claim, value in expected.items())
        or payload.get("ref_protected") != "true"
        or (
            audience != OIDC_AUDIENCE
            and not (isinstance(audience, list) and audience == [OIDC_AUDIENCE])
        )
    ):
        raise PublicationBlocked("OIDC_CLAIMS_MISMATCH")


@dataclass(frozen=True, slots=True)
class GitHubContext:
    repository: str
    repository_id: str
    ref: str
    commit_sha: str
    event_name: str
    run_id: str
    run_attempt: str
    builder_identity: str

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str], *, builder_identity: str
    ) -> GitHubContext:
        context = cls(
            repository=environ.get("GITHUB_REPOSITORY", ""),
            repository_id=environ.get("GITHUB_REPOSITORY_ID", ""),
            ref=environ.get("GITHUB_REF", ""),
            commit_sha=environ.get("GITHUB_SHA", ""),
            event_name=environ.get("GITHUB_EVENT_NAME", ""),
            run_id=environ.get("GITHUB_RUN_ID", ""),
            run_attempt=environ.get("GITHUB_RUN_ATTEMPT", ""),
            builder_identity=builder_identity,
        )
        if (
            REPOSITORY_RE.fullmatch(context.repository) is None
            or not context.repository_id.isdigit()
            or int(context.repository_id) <= 0
            or PROTECTED_REF_RE.fullmatch(context.ref) is None
            or SHA_RE.fullmatch(context.commit_sha) is None
            or context.event_name not in RELEASE_EVENTS
            or not context.run_id.isdigit()
            or not context.run_attempt.isdigit()
            or int(context.run_id) <= 0
            or int(context.run_attempt) <= 0
            or not _trusted_workflow_matches(
                context.builder_identity,
                source_ref=context.ref,
                repository=context.repository,
            )
        ):
            raise PublicationBlocked("GITHUB_SOURCE_CONTEXT_INVALID")
        return context

    @property
    def run_url(self) -> str:
        return (
            f"https://github.com/{self.repository}/actions/runs/"
            f"{self.run_id}/attempts/{self.run_attempt}"
        )


def bind_source(
    config: PublicationConfig, context: GitHubContext, *, plugin_id: str
) -> SourceBinding:
    matches = [
        source
        for source in config.sources
        if (
            source.plugin_id == plugin_id
            and source.repository == context.repository
            and str(source.repository_id) == context.repository_id
            and source.ref == context.ref
            and source.trusted_workflow_ref == context.builder_identity
        )
    ]
    if len(matches) != 1:
        raise PublicationBlocked("SOURCE_BINDING_REQUIRED")
    return matches[0]


def _validate_release_refs(
    *, bundle_ref: str, bundle_digest: str, backend_ref: str, backend_digest: str
) -> None:
    if (
        OCI_REF_RE.fullmatch(bundle_ref) is None
        or OCI_REF_RE.fullmatch(backend_ref) is None
        or DIGEST_RE.fullmatch(bundle_digest) is None
        or DIGEST_RE.fullmatch(backend_digest) is None
        or not bundle_ref.endswith("@" + bundle_digest)
        or not backend_ref.endswith("@" + backend_digest)
        or bundle_ref == backend_ref
        or bundle_digest == backend_digest
        or "/plugins-quarantine/" in bundle_ref
        or "/plugins-quarantine/" in backend_ref
        or "/plugins/" not in bundle_ref
        or "/plugins/" not in backend_ref
    ):
        raise PublicationBlocked("RELEASE_REFS_INVALID")
    backend_repository = backend_ref.rsplit("@", 1)[0]
    bundle_repository = bundle_ref.rsplit("@", 1)[0]
    if not backend_repository.endswith("/backend") or bundle_repository != (
        backend_repository[:-8] + "/bundle"
    ):
        raise PublicationBlocked("RELEASE_REFS_INVALID")


def validate_release_decision(
    decision: Any,
    *,
    plugin_id: str,
    version: str,
    bundle_ref: str,
    bundle_digest: str,
    backend_ref: str,
    backend_digest: str,
) -> None:
    if not isinstance(decision, dict) or (
        decision.get("decision") != "PUBLISH_ALLOWED"
        or decision.get("publish_allowed") is not True
        or decision.get("code") != "OK"
        or decision.get("artifact_ref") != bundle_ref
        or decision.get("backend_ref") != backend_ref
        or decision.get("backend_digest") != backend_digest
    ):
        raise PublicationBlocked("RELEASE_VERIFICATION_REQUIRED")
    summary = decision.get("manifest_summary")
    if not isinstance(summary, dict) or (
        summary.get("plugin_id") != plugin_id
        or summary.get("version") != version
        or summary.get("manifest_content_verified") is not True
        or summary.get("backend_image_ref") != backend_ref
        or summary.get("backend_digest") != backend_digest
    ):
        raise PublicationBlocked("RELEASE_VERIFICATION_REQUIRED")
    checks = decision.get("checks")
    if not isinstance(checks, list):
        raise PublicationBlocked("RELEASE_VERIFICATION_REQUIRED")
    observed: set[str] = set()
    for item in checks:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or item.get("ok") is not True:
            raise PublicationBlocked("RELEASE_VERIFICATION_REQUIRED")
        observed.add(name)
    if not REQUIRED_RELEASE_CHECKS <= observed:
        raise PublicationBlocked("RELEASE_VERIFICATION_REQUIRED")
    if not bundle_ref.endswith("@" + bundle_digest):
        raise PublicationBlocked("RELEASE_REFS_INVALID")


def _validate_manifest_binding(
    manifest: Any,
    *,
    source: SourceBinding,
    context: GitHubContext,
    version: str,
    backend_ref: str,
    backend_digest: str,
) -> dict[str, Any]:
    if not isinstance(manifest, dict) or SEMVER_RE.fullmatch(version) is None:
        raise PublicationBlocked("PUBLICATION_MANIFEST_INVALID")
    annotations = manifest.get("annotations")
    if not isinstance(annotations, dict):
        raise PublicationBlocked("PUBLICATION_MANIFEST_INVALID")
    expected = {
        "plugin_id": source.plugin_id,
        "manifest_version": version,
        "backend_digest": backend_digest,
        "com.agentscale.plugin.backend_image_ref": backend_ref,
        "com.agentscale.plugin.source_repository": context.repository,
        "com.agentscale.plugin.source_sha": context.commit_sha,
    }
    if any(annotations.get(name) != value for name, value in expected.items()):
        raise PublicationBlocked("PUBLICATION_MANIFEST_INVALID")
    return manifest


def _reject_self_asserted_evidence(value: Any, *, code: str) -> None:
    if isinstance(value, dict):
        if UNTRUSTED_EVIDENCE_KEYS & set(value):
            raise PublicationBlocked(code)
        for nested in value.values():
            _reject_self_asserted_evidence(nested, code=code)
    elif isinstance(value, list):
        for nested in value:
            _reject_self_asserted_evidence(nested, code=code)


def _default_scan_runner(command: list[str]) -> None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=SCAN_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublicationBlocked("SCANNER_UNAVAILABLE") from exc
    if completed.returncode != 0:
        raise PublicationBlocked("SCANNER_FAILED")


def run_pinned_scan(
    config: PublicationConfig,
    *,
    backend_ref: str,
    report_path: str,
    docker_config_path: str,
    runner: Callable[[list[str]], None] = _default_scan_runner,
) -> None:
    """Run the pinned scanner with an isolated, disposable output mount."""
    destination = Path(os.path.abspath(report_path))
    docker_config = Path(docker_config_path)
    if (
        not docker_config_path
        or docker_config.is_symlink()
        or not docker_config.is_file()
        or destination.exists()
        or destination.is_symlink()
    ):
        raise PublicationBlocked("SCAN_EVIDENCE_INVALID")
    staging: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.scan-", dir=str(destination.parent)
            )
        )
        command = [
            "docker",
            "run",
            "--rm",
            "--pull=always",
            "--volume",
            f"{docker_config_path}:/root/.docker/config.json:ro",
            "--volume",
            "trivy-cache:/root/.cache/trivy",
            "--volume",
            f"{staging}:/out",
            config.scanner_image,
            "image",
            "--scanners",
            "vuln",
            "--severity",
            "HIGH,CRITICAL",
            "--ignore-unfixed=false",
            "--no-progress",
            "--exit-code",
            "0",
            "--timeout",
            f"{SCAN_TIMEOUT_SECONDS}s",
            "--format",
            "json",
            "--output",
            f"/out/{destination.name}",
            backend_ref,
        ]
        runner(command)
        if staging.is_symlink() or not staging.is_dir():
            raise PublicationBlocked("SCAN_EVIDENCE_REQUIRED")
        staged_report = staging / destination.name
        if staged_report.is_symlink() or not staged_report.is_file():
            raise PublicationBlocked("SCAN_EVIDENCE_REQUIRED")
        if destination.exists() or destination.is_symlink():
            raise PublicationBlocked("SCAN_EVIDENCE_INVALID")
        os.replace(staged_report, destination)
    except PublicationBlocked:
        raise
    except OSError as exc:
        raise PublicationBlocked("SCAN_EVIDENCE_INVALID") from exc
    except Exception as exc:
        raise PublicationBlocked("SCANNER_FAILED") from exc
    finally:
        cleanup_error: OSError | None = None
        if staging is not None:
            try:
                if staging.is_symlink():
                    staging.unlink()
                elif staging.exists():
                    shutil.rmtree(staging)
            except OSError as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            raise PublicationBlocked("SCAN_EVIDENCE_INVALID") from cleanup_error


def validate_scan_report(
    report: Any, *, backend_ref: str, scanner_image: str
) -> bool:
    _reject_self_asserted_evidence(report, code="SCAN_EVIDENCE_UNTRUSTED")
    if (
        SCANNER_IMAGE_RE.fullmatch(scanner_image) is None
        or not isinstance(report, dict)
        or report.get("SchemaVersion") != 2
        or isinstance(report.get("SchemaVersion"), bool)
        or report.get("ArtifactName") != backend_ref
        or report.get("ArtifactType") != "container_image"
    ):
        raise PublicationBlocked("SCAN_EVIDENCE_INVALID")
    metadata = report.get("Metadata")
    if not isinstance(metadata, dict) or backend_ref not in (
        metadata.get("RepoDigests") if isinstance(metadata.get("RepoDigests"), list) else []
    ):
        raise PublicationBlocked("SCAN_RELEASE_BINDING_MISMATCH")
    results = report.get("Results")
    if not isinstance(results, list) or not results:
        raise PublicationBlocked("SCAN_EVIDENCE_INVALID")
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("Target"), str) or not result["Target"]:
            raise PublicationBlocked("SCAN_EVIDENCE_INVALID")
        vulnerabilities = result.get("Vulnerabilities", [])
        if vulnerabilities is None:
            vulnerabilities = []
        if not isinstance(vulnerabilities, list):
            raise PublicationBlocked("SCAN_EVIDENCE_INVALID")
        if vulnerabilities:
            raise PublicationBlocked("SCAN_POLICY_FAILED", status="QUARANTINED")
    return True


def validate_csp_response(
    response: HttpResponse,
    *,
    source: SourceBinding,
    bundle_digest: str,
) -> bool:
    origin = source.static_origin
    if origin is None:
        raise PublicationBlocked("CSP_EVIDENCE_REQUIRED")
    if response.status != 200:
        raise PublicationBlocked("CSP_ORIGIN_HTTP_ERROR")
    for name, expected in origin.security_headers.items():
        if response.headers.get(name) != expected:
            raise PublicationBlocked("CSP_SECURITY_HEADERS_FAILED")
    _validate_csp(
        response.headers.get("content-security-policy", ""),
        code="CSP_SECURITY_HEADERS_FAILED",
    )
    if response.headers.get("x-agentscale-bundle-digest") != bundle_digest:
        raise PublicationBlocked("CSP_ARTIFACT_BINDING_MISMATCH")
    return True


@dataclass(frozen=True, slots=True)
class PublicationOutcome:
    status: str
    code: str


def fetch_github_oidc(transport: Any, environ: Mapping[str, str]) -> str:
    request_url = environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    request_token = environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    parsed = _validate_https_url(request_url, code="OIDC_REQUEST_INVALID", allow_query=True)
    if (
        not parsed.hostname
        or not (
            parsed.hostname == "actions.githubusercontent.com"
            or parsed.hostname.endswith(".actions.githubusercontent.com")
        )
        or not request_token
        or request_token != request_token.strip()
        or any(char.isspace() for char in request_token)
    ):
        raise PublicationBlocked("OIDC_REQUEST_INVALID")
    query = parse.parse_qsl(parsed.query, keep_blank_values=True)
    if any(name == "audience" for name, _ in query):
        raise PublicationBlocked("OIDC_REQUEST_INVALID")
    query.append(("audience", OIDC_AUDIENCE))
    url = parse.urlunsplit(parsed._replace(query=parse.urlencode(query)))
    response = _send(
        transport,
        "GET",
        url,
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer " + request_token,
        },
        unavailable_code="OIDC_UNAVAILABLE",
    )
    if response.status != 200:
        raise PublicationBlocked("OIDC_HTTP_ERROR")
    envelope = _json_loads(response.body, code="OIDC_RESPONSE_INVALID")
    token = envelope.get("value") if isinstance(envelope, dict) else None
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 16384
        or token != token.strip()
        or any(char.isspace() for char in token)
        or len(token.split(".")) != 3
    ):
        raise PublicationBlocked("OIDC_RESPONSE_INVALID")
    return token


def build_publication_payload(
    *,
    source: SourceBinding,
    context: GitHubContext,
    version: str,
    bundle_ref: str,
    bundle_digest: str,
    backend_ref: str,
    backend_digest: str,
    manifest: dict[str, Any],
    sbom_digest: str,
    provenance_digest: str,
) -> dict[str, Any]:
    if DIGEST_RE.fullmatch(sbom_digest) is None or DIGEST_RE.fullmatch(provenance_digest) is None:
        raise PublicationBlocked("EVIDENCE_FILE_INVALID")
    return {
        "source_id": source.source_id,
        "plugin_id": source.plugin_id,
        "version": version,
        "bundle_ref": bundle_ref,
        "bundle_digest": bundle_digest,
        "backend_ref": backend_ref,
        "backend_digest": backend_digest,
        "manifest": manifest,
        "commit_sha": context.commit_sha,
        "ref": context.ref,
        "run_url": context.run_url,
        "evidence": {
            "scan_verified": True,
            "csp_verified": True,
            "sbom_digest": sbom_digest,
            "provenance_digest": provenance_digest,
        },
    }


def _response_code(document: Any) -> str | None:
    if not isinstance(document, dict):
        return None
    code = document.get("code")
    return code if isinstance(code, str) and CODE_RE.fullmatch(code) else None


def parse_publication_response(
    response: HttpResponse, payload: Mapping[str, Any]
) -> PublicationOutcome:
    if response.status != 201:
        try:
            document = _json_loads(response.body, code="PUBLICATION_RESPONSE_INVALID")
        except PublicationBlocked:
            raise PublicationBlocked(f"PLATFORM_HTTP_{response.status}")
        code = _response_code(document)
        status = document.get("status") if isinstance(document, dict) else None
        if status == "QUARANTINED" and code:
            raise PublicationBlocked(code, status="QUARANTINED")
        raise PublicationBlocked(f"PLATFORM_HTTP_{response.status}")
    document = _json_loads(response.body, code="PUBLICATION_RESPONSE_INVALID")
    code = _response_code(document)
    status = document.get("status") if isinstance(document, dict) else None
    if status == "QUARANTINED":
        raise PublicationBlocked(code or "QUARANTINED", status="QUARANTINED")
    expected = {
        "status": "PUBLISHED",
        "code": "PUBLISHED",
        "plugin_id": payload["plugin_id"],
        "version": payload["version"],
        "bundle_digest": payload["bundle_digest"],
        "backend_digest": payload["backend_digest"],
        "source_id": payload["source_id"],
    }
    if not isinstance(document, dict) or set(document) != set(expected):
        raise PublicationBlocked("PUBLICATION_RESPONSE_INVALID")
    if any(document.get(name) != value for name, value in expected.items()):
        raise PublicationBlocked("PUBLICATION_RESPONSE_MISMATCH")
    return PublicationOutcome(status="PUBLISHED", code="PUBLISHED")


def publish(
    *,
    environ: Mapping[str, str],
    transport: Any,
    plugin_id: str,
    version: str,
    builder_identity: str,
    bundle_ref: str,
    bundle_digest: str,
    backend_ref: str,
    backend_digest: str,
    manifest_path: str,
    release_decision_path: str,
    scan_report_path: str,
    bundle_sbom_path: str,
    bundle_provenance_path: str,
    release_evidence_path: str | None = None,
    scan_runner: Callable[[list[str]], None] = _default_scan_runner,
) -> PublicationOutcome:
    config = PublicationConfig.from_environment(environ)
    context = GitHubContext.from_environment(environ, builder_identity=builder_identity)
    source = bind_source(config, context, plugin_id=plugin_id)
    _validate_release_refs(
        bundle_ref=bundle_ref,
        bundle_digest=bundle_digest,
        backend_ref=backend_ref,
        backend_digest=backend_digest,
    )
    decision = _read_json(
        release_decision_path,
        maximum=MAX_CONFIG_BYTES,
        code="RELEASE_VERIFICATION_REQUIRED",
    )
    validate_release_decision(
        decision,
        plugin_id=plugin_id,
        version=version,
        bundle_ref=bundle_ref,
        bundle_digest=bundle_digest,
        backend_ref=backend_ref,
        backend_digest=backend_digest,
    )
    manifest = _validate_manifest_binding(
        _read_json(
            manifest_path, maximum=MAX_CONFIG_BYTES, code="PUBLICATION_MANIFEST_INVALID"
        ),
        source=source,
        context=context,
        version=version,
        backend_ref=backend_ref,
        backend_digest=backend_digest,
    )
    if release_evidence_path is not None:
        # Pre-promotion evidence: the pinned scan and the CSP probe already ran
        # against the QUARANTINE backend/bundle BEFORE any release GHCR write.
        # Revalidate the recorded facts against current inputs; never re-run
        # the scanner, never re-probe, never trust caller evidence booleans.
        release_evidence = _read_json(
            release_evidence_path,
            maximum=RELEASE_EVIDENCE_MAX_BYTES,
            code="RELEASE_EVIDENCE_REQUIRED",
        )
        scan_report_bytes = _read_bounded(
            scan_report_path,
            maximum=MAX_SCAN_REPORT_BYTES,
            code="SCAN_EVIDENCE_REQUIRED",
        )
        _validate_release_evidence(
            release_evidence,
            config=config,
            source=source,
            context=context,
            plugin_id=plugin_id,
            version=version,
            bundle_ref=bundle_ref,
            bundle_digest=bundle_digest,
            backend_ref=backend_ref,
            backend_digest=backend_digest,
            scan_report_sha256="sha256:" + hashlib.sha256(scan_report_bytes).hexdigest(),
        )
        validate_scan_report(
            _json_loads(scan_report_bytes, code="SCAN_EVIDENCE_REQUIRED"),
            backend_ref=release_evidence["quarantine"]["backend_ref"],
            scanner_image=config.scanner_image,
        )
    else:
        run_pinned_scan(
            config,
            backend_ref=backend_ref,
            report_path=scan_report_path,
            docker_config_path=environ.get("DOCKER_CONFIG_PATH", ""),
            runner=scan_runner,
        )
        scan_report = _read_json(
            scan_report_path,
            maximum=MAX_SCAN_REPORT_BYTES,
            code="SCAN_EVIDENCE_REQUIRED",
        )
        validate_scan_report(
            scan_report, backend_ref=backend_ref, scanner_image=config.scanner_image
        )
        if source.static_origin is None:
            raise PublicationBlocked("CSP_EVIDENCE_REQUIRED")
        csp_url = source.static_origin.url_for(
            plugin_id=plugin_id, version=version, bundle_digest=bundle_digest
        )
        csp_response = _send(
            transport,
            "HEAD",
            csp_url,
            headers={"Accept": "text/html", "Cache-Control": "no-cache"},
            unavailable_code="CSP_ORIGIN_UNAVAILABLE",
        )
        validate_csp_response(csp_response, source=source, bundle_digest=bundle_digest)

    # Only now have independent scan and CSP facts both been established.
    payload = build_publication_payload(
        source=source,
        context=context,
        version=version,
        bundle_ref=bundle_ref,
        bundle_digest=bundle_digest,
        backend_ref=backend_ref,
        backend_digest=backend_digest,
        manifest=manifest,
        sbom_digest=_file_digest(bundle_sbom_path),
        provenance_digest=_file_digest(bundle_provenance_path),
    )
    token = fetch_github_oidc(transport, environ)
    response = _send(
        transport,
        "POST",
        config.publication_url,
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        body=json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8"),
        unavailable_code="PLATFORM_UNAVAILABLE",
    )
    return parse_publication_response(response, payload)


def _append_summary(
    path: str,
    *,
    outcome: PublicationOutcome,
    plugin_id: str,
    version: str,
    bundle_ref: str,
    backend_ref: str,
) -> None:
    lines = [
        "### Platform plugin publication",
        f"Result: {outcome.status} ({outcome.code})",
        f"Plugin: {plugin_id} {version}",
        f"Verified release backend: {backend_ref}",
        f"Verified release bundle: {bundle_ref}",
        "Platform registration is a separate gate after release verification.",
    ]
    if outcome.status == "PUBLISHED":
        lines.append("Quarantine/verify/promote/release-reverify was validated before the platform POST.")
        lines.append("The platform returned an exact matching PUBLISHED response.")
    else:
        lines.append("No platform PUBLISHED or install result is claimed; the job gate is failed.")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    context = commands.add_parser("validate-context")
    context.add_argument("--plugin-id", required=True)
    context.add_argument("--builder-identity", required=True)

    publication = commands.add_parser("publish")
    publication.add_argument("--plugin-id", required=True)
    publication.add_argument("--plugin-version", required=True)
    publication.add_argument("--builder-identity", required=True)
    publication.add_argument("--bundle-ref", required=True)
    publication.add_argument("--bundle-digest", required=True)
    publication.add_argument("--backend-ref", required=True)
    publication.add_argument("--backend-digest", required=True)
    publication.add_argument("--manifest", required=True)
    publication.add_argument("--release-decision", required=True)
    publication.add_argument("--scan-report", required=True)
    publication.add_argument("--bundle-sbom", required=True)
    publication.add_argument("--bundle-provenance", required=True)
    publication.add_argument(
        "--release-evidence",
        default="",
        help="Pre-promotion release evidence JSON; when set, the recorded scan/CSP "
        "facts are revalidated instead of running a new scan/probe",
    )
    publication.add_argument("--summary", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    environ: Mapping[str, str] = os.environ
    if args.command == "validate-context":
        try:
            config = PublicationConfig.from_environment(environ)
            context = GitHubContext.from_environment(
                environ, builder_identity=args.builder_identity
            )
            source = bind_source(config, context, plugin_id=args.plugin_id)
        except PublicationBlocked as exc:
            print(f"::error::{exc.code}: protected source/publication config gate failed", file=sys.stderr)
            return 1
        except Exception:
            print("::error::PROTECTED_CONFIG_INVALID", file=sys.stderr)
            return 1
        print(f"Protected source binding validated for {source.plugin_id}; no source_id was accepted from input.")
        return 0

    outcome = PublicationOutcome(status="BLOCKED", code="PUBLICATION_FAILED")
    try:
        outcome = publish(
            environ=environ,
            transport=UrllibTransport(),
            plugin_id=args.plugin_id,
            version=args.plugin_version,
            builder_identity=args.builder_identity,
            bundle_ref=args.bundle_ref,
            bundle_digest=args.bundle_digest,
            backend_ref=args.backend_ref,
            backend_digest=args.backend_digest,
            manifest_path=args.manifest,
            release_decision_path=args.release_decision,
            scan_report_path=args.scan_report,
            bundle_sbom_path=args.bundle_sbom,
            bundle_provenance_path=args.bundle_provenance,
            release_evidence_path=args.release_evidence or None,
        )
    except PublicationBlocked as exc:
        outcome = PublicationOutcome(status=exc.status, code=exc.code)
    except Exception:
        outcome = PublicationOutcome(status="BLOCKED", code="PUBLICATION_FAILED")
    _append_summary(
        args.summary,
        outcome=outcome,
        plugin_id=args.plugin_id,
        version=args.plugin_version,
        bundle_ref=args.bundle_ref,
        backend_ref=args.backend_ref,
    )
    if outcome.status != "PUBLISHED":
        print(f"::error::{outcome.code}: platform publication gate failed", file=sys.stderr)
        return 1
    print("Platform publication returned PUBLISHED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
