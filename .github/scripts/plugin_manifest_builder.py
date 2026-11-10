#!/usr/bin/env python3
"""Build and validate the canonical OCI bundle for an Agentscale plugin.

The original source manifest.json is the canonical payload. Its canonical JSON
bytes are used as both the OCI config and an explicit layer/blob. The backend
image is not a bundle blob: a digest-pinned backend_image_ref annotation links
the two independently signed artifacts.

Only the Python standard library is used. No registry, Docker, cosign, network,
or subprocess operation happens at import time. Every malformed or ambiguous
input raises ManifestError (fail closed).
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
from typing import Any, Sequence

SCHEMA_VERSION = 2
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
PLUGIN_ARTIFACT_TYPE = "application/vnd.agentscale.plugin.oci"
PLUGIN_MANIFEST_MEDIA_TYPE = "application/vnd.agentscale.plugin.manifest.v1+json"
FRONTEND_MEDIA_TYPE = "application/vnd.agentscale.plugin.frontend"
MIGRATIONS_MEDIA_TYPE = "application/vnd.agentscale.plugin.liquibase+xml"
PLUGIN_PROTOCOL = "plugin-rpc/1"

CAPABILITY_ALLOWLIST = frozenset(
    {"cache", "objects", "docs", "events", "jobs", "pods", "mcp", "secrets", "network"}
)
NON_GATEWAY_CAPABILITIES = frozenset({"db.direct", "db.migrations"})

A_PLUGIN_ID = "plugin_id"
A_MANIFEST_VERSION = "manifest_version"
A_PROTOCOL = "protocol"
A_CAPABILITIES = "requestedCapabilities"
A_MIN_PLATFORM = "min_platform_version"
A_BACKEND_DIGEST = "backend_digest"
A_BACKEND_IMAGE_REF = "com.agentscale.plugin.backend_image_ref"
A_SOURCE_REPOSITORY = "com.agentscale.plugin.source_repository"
A_SOURCE_SHA = "com.agentscale.plugin.source_sha"
A_MANIFEST_DIGEST = "com.agentscale.plugin.manifest_digest"
A_LAYER_ROLE = "com.agentscale.plugin.layer.role"
ROLE_MANIFEST = "canonical-manifest"
ROLE_FRONTEND = "frontend-dist"
ROLE_MIGRATIONS = "migrations"

SPDX_PREDICATE_TYPE = "https://spdx.dev/Document"
SLSA_PREDICATE_TYPES = frozenset({"https://slsa.dev/provenance/v0.2"})
BACKEND_BUILD_TYPE = "https://agentscale.dev/plugin-backend-buildx/v1"
BUNDLE_BUILD_TYPE = "https://agentscale.dev/plugin-oci-bundle/v1"

FORBIDDEN_SELF_REFERENCE_KEYS = frozenset({"artifact_digest", "signature"})
#: Required v1 source fields.
MANIFEST_FIELDS = frozenset(
    {
        "plugin_id", "version", "protocol", "frontend", "backend", "capabilities",
        "migrations", "egress_ref", "public_pages", "webhooks", "min_platform_version",
    }
)
#: Optional v1 source fields that the signed manifest schema defines and the
#: production validator accepts. They are carried into the canonical payload
#: verbatim, so the builder must not reject a manifest that the platform accepts.
OPTIONAL_MANIFEST_FIELDS = frozenset({"mcp_servers", "pods", "rpc_operations", "secret_delivery"})
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
#: Fully digest-pinned OCI reference. Docker requires a tag before the digest in
#: a Dockerfile, so both ``repo@sha256:...`` and ``repo:tag@sha256:...`` are
#: valid pins; the digest alone decides identity and ``:latest`` stays banned by
#: _assert_no_forbidden_keys. Repository names keep docker's [._-] alphabet.
#: One path component of a docker repository name.
_REPO_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|[-]+)[a-z0-9]+)*"

_REF_RE = re.compile(
    r"^(?P<name>"
    r"(?:[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]+)?/)?"
    rf"{_REPO_COMPONENT}(?:/{_REPO_COMPONENT})*"
    r")"
    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9._-]{0,127}))?"
    r"@(?P<digest>sha256:[0-9a-f]{64})$"
)
_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_ABSOLUTE_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]*$")


class ManifestErrorCode:
    INVALID_PLUGIN_ID = "INVALID_PLUGIN_ID"
    INVALID_SEMVER = "INVALID_SEMVER"
    INVALID_PROTOCOL = "INVALID_PROTOCOL"
    INVALID_DIGEST = "INVALID_DIGEST"
    LATEST_FORBIDDEN = "LATEST_FORBIDDEN"
    UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
    FORBIDDEN_CAPABILITY = "FORBIDDEN_CAPABILITY"
    INVALID_CAPABILITIES = "INVALID_CAPABILITIES"
    INVALID_MIN_PLATFORM = "INVALID_MIN_PLATFORM"
    SELF_REFERENCE = "SELF_REFERENCE"
    INVALID_LAYERS = "INVALID_LAYERS"
    MISSING_FILE = "MISSING_FILE"
    INVALID_MANIFEST = "INVALID_MANIFEST"
    INVALID_LAYOUT = "INVALID_LAYOUT"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"


class ManifestError(Exception):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


def _fail(code: str, reason: str) -> None:
    raise ManifestError(code, reason)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(ManifestErrorCode.INVALID_MANIFEST, f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _read_json(path: str | os.PathLike[str]) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_object_without_duplicates)
    except ManifestError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(ManifestErrorCode.INVALID_MANIFEST, f"cannot read JSON {str(path)!r}: {exc!r}")


def _write_json(path: str | os.PathLike[str], payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return the one canonical byte representation whose digest is signed."""
    validate_source_manifest(manifest)
    return (
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


def _require_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        _fail(ManifestErrorCode.INVALID_DIGEST, f"{field} must match sha256:<64 lowercase hex>")
    return value


def _require_backend_ref(value: Any) -> tuple[str, str]:
    if not isinstance(value, str) or _REF_RE.fullmatch(value) is None or ":latest" in value:
        _fail(ManifestErrorCode.INVALID_DIGEST, "backend_image_ref must be a digest-pinned OCI ref")
    return value, value.rsplit("@", 1)[1]


def _require_plugin_id(value: Any) -> str:
    if not isinstance(value, str) or _PLUGIN_ID_RE.fullmatch(value) is None:
        _fail(
            ManifestErrorCode.INVALID_PLUGIN_ID,
            f"plugin_id must match ^[a-z][a-z0-9_]*$: {value!r}",
        )
    return value


def _require_semver(value: Any, field: str, code: str) -> str:
    if not isinstance(value, str) or _SEMVER_RE.fullmatch(value) is None:
        _fail(code, f"{field} must be SemVer 2.0.0: {value!r}")
    return value


def _normalise_capabilities(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        _fail(ManifestErrorCode.INVALID_CAPABILITIES, "capabilities must be an array")
    result: list[str] = []
    for capability in values:
        if not isinstance(capability, str):
            _fail(ManifestErrorCode.INVALID_CAPABILITIES, "capability must be a string")
        if capability in NON_GATEWAY_CAPABILITIES:
            _fail(ManifestErrorCode.FORBIDDEN_CAPABILITY, f"forbidden capability {capability!r}")
        if capability not in CAPABILITY_ALLOWLIST:
            _fail(ManifestErrorCode.UNKNOWN_CAPABILITY, f"unknown capability {capability!r}")
        if capability in result:
            _fail(ManifestErrorCode.INVALID_CAPABILITIES, f"duplicate capability {capability!r}")
        result.append(capability)
    return tuple(result)


def _assert_no_forbidden_keys(node: Any, path: str = "manifest") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_SELF_REFERENCE_KEYS:
                _fail(ManifestErrorCode.SELF_REFERENCE, f"self-reference at {path}.{key}")
            _assert_no_forbidden_keys(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _assert_no_forbidden_keys(value, f"{path}[{index}]")
    elif isinstance(node, str) and ":latest" in node:
        _fail(ManifestErrorCode.LATEST_FORBIDDEN, f"mutable :latest at {path}")


def _validate_relative_path(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or _PATH_RE.fullmatch(value) is None
        or value.startswith("/")
        or ".." in value
    ):
        _fail(ManifestErrorCode.INVALID_MANIFEST, f"invalid {field}: {value!r}")
    return value


def _validate_absolute_path(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or _ABSOLUTE_PATH_RE.fullmatch(value) is None
        or ".." in value
    ):
        _fail(ManifestErrorCode.INVALID_MANIFEST, f"invalid {field}: {value!r}")
    return value


def validate_source_manifest(manifest: Any) -> dict[str, Any]:
    """Validate the closed v1 source contract without optional dependencies."""
    if not isinstance(manifest, dict):
        _fail(ManifestErrorCode.INVALID_MANIFEST, "source manifest must be an object")
    missing = sorted(MANIFEST_FIELDS - set(manifest))
    if missing:
        _fail(
            ManifestErrorCode.INVALID_MANIFEST,
            f"source fields are missing: {missing}",
        )
    unknown = sorted(set(manifest) - MANIFEST_FIELDS - OPTIONAL_MANIFEST_FIELDS)
    if unknown:
        _fail(
            ManifestErrorCode.INVALID_MANIFEST,
            f"source fields must be exactly {sorted(MANIFEST_FIELDS | OPTIONAL_MANIFEST_FIELDS)}",
        )
    _assert_no_forbidden_keys(manifest)
    _require_plugin_id(manifest["plugin_id"])
    _require_semver(manifest["version"], "version", ManifestErrorCode.INVALID_SEMVER)
    if manifest["protocol"] != PLUGIN_PROTOCOL:
        _fail(ManifestErrorCode.INVALID_PROTOCOL, "protocol must be plugin-rpc/1")
    _require_semver(
        manifest["min_platform_version"],
        "min_platform_version",
        ManifestErrorCode.INVALID_MIN_PLATFORM,
    )
    _normalise_capabilities(manifest["capabilities"])

    frontend = manifest["frontend"]
    if not isinstance(frontend, dict) or set(frontend) != {"entry", "base_path"}:
        _fail(ManifestErrorCode.INVALID_LAYOUT, "frontend must contain entry and base_path only")
    _validate_relative_path(frontend["entry"], "frontend.entry")
    _validate_absolute_path(frontend["base_path"], "frontend.base_path")

    backend = manifest["backend"]
    if not isinstance(backend, dict) or set(backend) != {"port", "health"}:
        _fail(ManifestErrorCode.INVALID_LAYOUT, "backend must contain port and health only")
    port = backend["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        _fail(ManifestErrorCode.INVALID_LAYOUT, "backend.port must be in 1..65535")
    _validate_absolute_path(backend["health"], "backend.health")

    migrations = manifest["migrations"]
    if not isinstance(migrations, dict) or set(migrations) != {"engine", "path"}:
        _fail(ManifestErrorCode.INVALID_LAYOUT, "migrations must contain engine and path only")
    if migrations["engine"] not in {"liquibase", "flyway", "alembic"}:
        _fail(ManifestErrorCode.INVALID_LAYOUT, "unsupported migrations.engine")
    _validate_relative_path(migrations["path"], "migrations.path")

    if not isinstance(manifest["egress_ref"], str) or not re.fullmatch(
        r"grant:[A-Za-z0-9_.:/-]+", manifest["egress_ref"]
    ):
        _fail(ManifestErrorCode.INVALID_MANIFEST, "invalid egress_ref")
    for field in ("public_pages", "webhooks"):
        values = manifest[field]
        if not isinstance(values, list) or len(values) != len(set(values)):
            _fail(ManifestErrorCode.INVALID_MANIFEST, f"{field} must be a unique array")
        for value in values:
            _validate_absolute_path(value, field)
            if value == "/":
                _fail(ManifestErrorCode.INVALID_MANIFEST, f"{field} path must not be the bare root")
    return manifest


def _inside(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        _fail(ManifestErrorCode.INVALID_LAYOUT, f"{label} escapes its package root")
    return resolved


def _validate_protocol_fixture(source_root: Path) -> None:
    """Validate the checked-in plugin-rpc fixture in the platform repository."""
    path = source_root / "packages/schemas/plugin-manifest/v1/protocol.json"
    if not path.is_file():
        return
    contract = _read_json(path)
    base = {"protocol", "type", "plugin_id", "version", "artifact_digest", "instance_id", "nonce"}
    expected = {
        "hostReady": ("host.ready", base),
        "pluginReady": ("plugin.ready", base),
        "grantReady": ("grant.ready", base | {"capabilities", "scope", "expires_at"}),
        "pluginRpcRequest": (
            "plugin.rpc.request",
            base | {"request_id", "method", "context", "input"},
        ),
        "pluginRpcResponse": ("plugin.rpc.response", base | {"request_id", "ok"}),
        "pluginError": ("plugin.error", base | {"error"}),
    }
    if not isinstance(contract, dict):
        _fail(ManifestErrorCode.INVALID_PROTOCOL, "protocol fixture must be an object")
    one_of = contract.get("oneOf", [])
    refs = {
        item.get("$ref") if isinstance(item, dict) else None
        for item in one_of
    }
    if len(one_of) != len(expected) or refs != {
        f"#/$defs/{name}" for name in expected
    }:
        _fail(ManifestErrorCode.INVALID_PROTOCOL, "protocol fixture must declare six message types")
    definitions = contract.get("$defs", {})
    for name, (message_type, expected_required) in expected.items():
        definition = definitions.get(name)
        if not isinstance(definition, dict):
            _fail(ManifestErrorCode.INVALID_PROTOCOL, f"missing protocol definition {name}")
        properties = definition.get("properties", {})
        if not isinstance(properties, dict):
            _fail(ManifestErrorCode.INVALID_PROTOCOL, f"invalid protocol properties {name}")
        if properties.get("type", {}).get("const") != message_type:
            _fail(ManifestErrorCode.INVALID_PROTOCOL, f"wrong message type for {name}")
        raw_required = definition.get("required", [])
        if not isinstance(raw_required, list):
            _fail(ManifestErrorCode.INVALID_PROTOCOL, f"invalid protocol required fields {name}")
        required = set(raw_required)
        if (
            definition.get("type") != "object"
            or definition.get("additionalProperties") is not False
            or required != expected_required
            or not required <= set(properties)
        ):
            _fail(ManifestErrorCode.INVALID_PROTOCOL, f"incomplete protocol fixture {name}")


def _resolve_arg_reference(base: str, arg_defaults: dict[str, str]) -> str:
    """Разрешить ``${ARG}``/``$ARG`` в ссылку на образ через дефолт ARG.

    ``FROM ${PYTHON_BASE_IMAGE}`` — штатная практика Dockerfile: адрес базы
    приходит build-arg-ом. Проверка, отвергающая любую такую форму, ломает
    корректный Dockerfile, поэтому дефолт ARG подставляется, а результат
    валидируется тем же регексом, что и литеральная ссылка.

    ARG без дефолта разрешать нельзя: тогда база задаётся только при сборке,
    и статическая проверка ничего о ней не доказывает — пусть падает.
    """
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", base)
    if match is None:
        return base
    name = match.group(1) or match.group(2)
    return arg_defaults.get(name, base)


def _validate_dockerfile_bases(path: Path) -> None:
    """Reject mutable base inputs; local stage names and scratch are allowed."""
    stages: set[str] = set()
    # Объявленные ARG с дефолтом: FROM ${ARG} допустим, только если сам
    # дефолт пиннут по digest. ARG без дефолта отвергается — иначе базой
    # может оказаться произвольный тег, переданный при сборке.
    arg_defaults: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
    except OSError as exc:
        _fail(ManifestErrorCode.MISSING_FILE, f"cannot read Dockerfile: {exc!r}")
    if re.search(r"\b(?:curl|wget)\b", text) and "sha256sum -c" not in text:
        _fail(ManifestErrorCode.INVALID_LAYOUT, "Dockerfile downloads require an explicit sha256sum -c check")
    if "pip install" in text and "--require-hashes" not in text:
        _fail(ManifestErrorCode.INVALID_LAYOUT, "pip installs must use --require-hashes with a locked requirements file")
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.lower().startswith("# syntax="):
            syntax = stripped.split("=", 1)[1].strip()
            if _REF_RE.fullmatch(syntax) is None:
                _fail(ManifestErrorCode.INVALID_LAYOUT, "Dockerfile syntax frontend must be digest-pinned")
            continue
        tokens = stripped.split()
        if not tokens:
            continue
        if tokens[0].upper() == "ARG" and len(tokens) >= 2:
            name, _, default = tokens[1].partition("=")
            if default:
                arg_defaults[name] = default.strip('"')
            continue
        if tokens[0].upper() != "FROM":
            continue
        index = 1
        while index < len(tokens) and tokens[index].startswith("--"):
            index += 1
        if index >= len(tokens):
            _fail(ManifestErrorCode.INVALID_LAYOUT, "FROM without an image")
        base = tokens[index]
        reference = _resolve_arg_reference(base, arg_defaults)
        if (
            reference.lower() != "scratch"
            and reference not in stages
            and _REF_RE.fullmatch(reference) is None
        ):
            _fail(
                ManifestErrorCode.INVALID_LAYOUT,
                f"base image must be pinned by @sha256 digest, got {base!r}",
            )
        if len(tokens) > index + 2 and tokens[index + 1].upper() == "AS":
            stages.add(tokens[index + 2])


def _copy_plain_tree(source: Path, destination: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        _fail(ManifestErrorCode.INVALID_LAYOUT, f"missing or symlinked directory {source}")
    for root, dirs, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        for name in dirs + files:
            if (root_path / name).is_symlink():
                _fail(ManifestErrorCode.INVALID_LAYOUT, f"symlink is forbidden in plugin payload: {name}")
        for name in files:
            source_file = root_path / name
            relative = source_file.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target)


def _make_deterministic_archive(source: Path, destination: Path) -> None:
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        _fail(ManifestErrorCode.INVALID_LAYERS, f"empty payload directory {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in files:
                    if path.is_symlink():
                        _fail(ManifestErrorCode.INVALID_LAYOUT, "archive input contains a symlink")
                    info = tarfile.TarInfo(path.relative_to(source).as_posix())
                    info.size = path.stat().st_size
                    info.mtime = 0
                    info.mode = 0o644
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)


def prepare_package(
    source_root: str,
    plugin_dir: str,
    manifest_path: str,
    output_dir: str,
) -> dict[str, Any]:
    """Resolve source layout and create staging/canonical/archive payloads."""
    root = Path(source_root).resolve()
    if not plugin_dir or Path(plugin_dir).is_absolute():
        _fail(ManifestErrorCode.INVALID_LAYOUT, "plugin_dir must be a relative package path")
    package = _inside(root / plugin_dir, root, "plugin_dir")
    if not package.is_dir():
        _fail(ManifestErrorCode.MISSING_FILE, f"plugin package not found: {plugin_dir}")
    source_manifest_path = (
        _inside(root / manifest_path, package, "manifest_path")
        if manifest_path
        else package / "manifest.json"
    )
    if not source_manifest_path.is_file():
        _fail(ManifestErrorCode.MISSING_FILE, "manifest.json not found in package")
    source_manifest = validate_source_manifest(_read_json(source_manifest_path))
    _validate_protocol_fixture(root)

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    canonical_path = output / "canonical-manifest.json"
    canonical_path.write_bytes(canonical_manifest_bytes(source_manifest))

    frontend_dist = package / "frontend" / "dist"
    frontend_static = package / "frontend"
    frontend_source = frontend_dist if frontend_dist.is_dir() else frontend_static
    staged_frontend = output / "frontend-dist"
    if staged_frontend.exists():
        shutil.rmtree(staged_frontend)
    _copy_plain_tree(frontend_source, staged_frontend)
    entry = _inside(staged_frontend / source_manifest["frontend"]["entry"], staged_frontend, "frontend.entry")
    if not entry.is_file():
        _fail(ManifestErrorCode.INVALID_LAYOUT, "frontend.entry is absent from staged dist")

    migrations = package / "migrations"
    if not migrations.is_dir():
        _fail(ManifestErrorCode.INVALID_LAYOUT, "migrations directory is required")
    migration_file = _inside(package / source_manifest["migrations"]["path"], package, "migrations.path")
    if not migration_file.is_file():
        _fail(ManifestErrorCode.INVALID_LAYOUT, "manifest migrations.path does not exist")

    frontend_archive = output / "frontend-dist.tar.gz"
    migrations_archive = output / "migrations.tar.gz"
    _make_deterministic_archive(staged_frontend, frontend_archive)
    _make_deterministic_archive(migrations, migrations_archive)

    dockerfile = package / "Dockerfile"
    if not dockerfile.is_file():
        dockerfile = package / "backend" / "Dockerfile"
    if not dockerfile.is_file():
        _fail(ManifestErrorCode.INVALID_LAYOUT, "expected Dockerfile or backend/Dockerfile")
    _validate_dockerfile_bases(dockerfile)

    return {
        "plugin_id": source_manifest["plugin_id"],
        "version": source_manifest["version"],
        "capabilities": list(source_manifest["capabilities"]),
        "min_platform_version": source_manifest["min_platform_version"],
        "protocol": source_manifest["protocol"],
        "plugin_dir": plugin_dir,
        "package_root": str(package),
        "manifest_path": str(source_manifest_path),
        "canonical_manifest": str(canonical_path),
        "frontend_archive": str(frontend_archive),
        "migrations_archive": str(migrations_archive),
        "dockerfile": str(dockerfile),
        "docker_context": str(package),
    }


def _layer_descriptor(path: Path, media_type: str, role: str, title: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        _fail(ManifestErrorCode.MISSING_FILE, f"layer payload not found: {path}")
    return {
        "mediaType": media_type,
        "digest": _sha256_file(path),
        "size": path.stat().st_size,
        "annotations": {
            A_LAYER_ROLE: role,
            "org.opencontainers.image.title": title,
        },
    }


def build_bundle_manifest(
    source_manifest: dict[str, Any],
    backend_ref: str,
    canonical_manifest_path: str,
    frontend_archive: str,
    migrations_archive: str,
    source_repository: str,
    source_sha: str,
) -> dict[str, Any]:
    source_manifest = copy.deepcopy(validate_source_manifest(source_manifest))
    backend_ref, backend_digest = _require_backend_ref(backend_ref)
    if _REPOSITORY_RE.fullmatch(source_repository) is None:
        _fail(ManifestErrorCode.INVALID_MANIFEST, "invalid source repository")
    if _SHA_RE.fullmatch(source_sha) is None:
        _fail(ManifestErrorCode.INVALID_MANIFEST, "source_sha must be a full commit SHA")

    canonical_path = Path(canonical_manifest_path)
    expected_bytes = canonical_manifest_bytes(source_manifest)
    if not canonical_path.is_file() or canonical_path.read_bytes() != expected_bytes:
        _fail(ManifestErrorCode.INVALID_MANIFEST, "canonical manifest bytes differ from source manifest")
    manifest_digest = _sha256_bytes(expected_bytes)

    layers = [
        _layer_descriptor(canonical_path, PLUGIN_MANIFEST_MEDIA_TYPE, ROLE_MANIFEST, "canonical-manifest.json"),
        _layer_descriptor(Path(frontend_archive), FRONTEND_MEDIA_TYPE, ROLE_FRONTEND, "frontend-dist.tar.gz"),
        _layer_descriptor(Path(migrations_archive), MIGRATIONS_MEDIA_TYPE, ROLE_MIGRATIONS, "migrations.tar.gz"),
    ]
    annotations = {
        A_PLUGIN_ID: source_manifest["plugin_id"],
        A_MANIFEST_VERSION: source_manifest["version"],
        A_PROTOCOL: PLUGIN_PROTOCOL,
        A_CAPABILITIES: ",".join(source_manifest["capabilities"]),
        A_MIN_PLATFORM: source_manifest["min_platform_version"],
        A_BACKEND_DIGEST: backend_digest,
        A_BACKEND_IMAGE_REF: backend_ref,
        A_SOURCE_REPOSITORY: source_repository,
        A_SOURCE_SHA: source_sha,
        A_MANIFEST_DIGEST: manifest_digest,
    }
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "artifactType": PLUGIN_ARTIFACT_TYPE,
        "config": {
            "mediaType": PLUGIN_MANIFEST_MEDIA_TYPE,
            "digest": manifest_digest,
            "size": len(expected_bytes),
        },
        "layers": layers,
        "annotations": annotations,
    }
    _assert_no_forbidden_keys(manifest)
    return manifest


def annotation_file(manifest: dict[str, Any]) -> dict[str, Any]:
    """ORAS annotation-file payload, including per-layer role annotations."""
    result: dict[str, Any] = {"$manifest": manifest["annotations"]}
    for layer in manifest["layers"]:
        title = layer["annotations"]["org.opencontainers.image.title"]
        result[title] = layer["annotations"]
    return result


def validate_signed_manifest(
    manifest: Any,
    *,
    backend_digest: str,
    source_manifest: dict[str, Any] | None = None,
    backend_ref: str | None = None,
    source_repository: str | None = None,
    source_sha: str | None = None,
) -> dict[str, Any]:
    """Validate structure and, when source is supplied, canonical content."""
    if not isinstance(manifest, dict):
        _fail(ManifestErrorCode.INVALID_MANIFEST, "OCI manifest must be an object")
    _assert_no_forbidden_keys(manifest)
    if manifest.get("schemaVersion") != SCHEMA_VERSION or manifest.get("mediaType") != OCI_MANIFEST_MEDIA_TYPE:
        _fail(ManifestErrorCode.INVALID_MANIFEST, "unsupported OCI schemaVersion/mediaType")
    if manifest.get("artifactType") != PLUGIN_ARTIFACT_TYPE:
        _fail(ManifestErrorCode.INVALID_MANIFEST, "wrong plugin artifactType")
    annotations = manifest.get("annotations")
    if not isinstance(annotations, dict):
        _fail(ManifestErrorCode.INVALID_MANIFEST, "missing OCI annotations")
    plugin_id = _require_plugin_id(annotations.get(A_PLUGIN_ID))
    version = _require_semver(annotations.get(A_MANIFEST_VERSION), "manifest_version", ManifestErrorCode.INVALID_SEMVER)
    if annotations.get(A_PROTOCOL) != PLUGIN_PROTOCOL:
        _fail(ManifestErrorCode.INVALID_PROTOCOL, "wrong protocol annotation")
    minimum = _require_semver(
        annotations.get(A_MIN_PLATFORM), "min_platform_version", ManifestErrorCode.INVALID_MIN_PLATFORM
    )
    raw_capabilities = annotations.get(A_CAPABILITIES, "")
    capabilities = _normalise_capabilities(raw_capabilities.split(",") if raw_capabilities else [])
    expected_digest = _require_digest(backend_digest, "backend_digest")
    if annotations.get(A_BACKEND_DIGEST) != expected_digest:
        _fail(ManifestErrorCode.INVALID_DIGEST, "backend_digest annotation mismatch")
    linked_ref, linked_digest = _require_backend_ref(annotations.get(A_BACKEND_IMAGE_REF))
    if linked_digest != expected_digest or (backend_ref is not None and linked_ref != backend_ref):
        _fail(ManifestErrorCode.INVALID_DIGEST, "signed backend_image_ref link mismatch")

    config = manifest.get("config")
    if not isinstance(config, dict) or config.get("mediaType") != PLUGIN_MANIFEST_MEDIA_TYPE:
        _fail(ManifestErrorCode.INVALID_MANIFEST, "canonical source manifest must be the OCI config")
    config_digest = _require_digest(config.get("digest"), "config.digest")
    config_size = config.get("size")
    if isinstance(config_size, bool) or not isinstance(config_size, int) or config_size < 1:
        _fail(ManifestErrorCode.INVALID_MANIFEST, "invalid config.size")
    if annotations.get(A_MANIFEST_DIGEST) != config_digest:
        _fail(ManifestErrorCode.INVALID_DIGEST, "manifest digest annotation differs from config")

    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        _fail(ManifestErrorCode.INVALID_LAYERS, "layers must be a non-empty array; no fixed count is imposed")
    role_counts: dict[str, int] = {}
    canonical_layer: dict[str, Any] | None = None
    for layer in layers:
        if not isinstance(layer, dict):
            _fail(ManifestErrorCode.INVALID_LAYERS, "layer descriptor must be an object")
        _require_digest(layer.get("digest"), "layer.digest")
        size = layer.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            _fail(ManifestErrorCode.INVALID_LAYERS, "invalid layer.size")
        layer_annotations = layer.get("annotations")
        if not isinstance(layer_annotations, dict):
            _fail(ManifestErrorCode.INVALID_LAYERS, "layer annotations are required")
        role = layer_annotations.get(A_LAYER_ROLE)
        if role not in {ROLE_MANIFEST, ROLE_FRONTEND, ROLE_MIGRATIONS} and not (
            isinstance(role, str) and role.startswith("extension/")
        ):
            _fail(ManifestErrorCode.INVALID_LAYERS, f"unknown layer role {role!r}")
        role_counts[role] = role_counts.get(role, 0) + 1
        if role_counts[role] > 1:
            _fail(ManifestErrorCode.INVALID_LAYERS, f"duplicate layer role {role}")
        if role == ROLE_MANIFEST:
            canonical_layer = layer
            if layer.get("mediaType") != PLUGIN_MANIFEST_MEDIA_TYPE:
                _fail(ManifestErrorCode.INVALID_LAYERS, "wrong canonical manifest layer mediaType")
    if canonical_layer is None or canonical_layer["digest"] != config_digest or canonical_layer["size"] != config_size:
        _fail(ManifestErrorCode.INVALID_LAYERS, "canonical manifest must be both config and an identical blob")

    content_verified = False
    if source_manifest is not None:
        source_manifest = validate_source_manifest(source_manifest)
        expected_bytes = canonical_manifest_bytes(source_manifest)
        if config_digest != _sha256_bytes(expected_bytes) or config_size != len(expected_bytes):
            _fail(ManifestErrorCode.INVALID_DIGEST, "OCI config is not the full canonical source manifest")
        if plugin_id != source_manifest["plugin_id"] or version != source_manifest["version"]:
            _fail(ManifestErrorCode.INVALID_MANIFEST, "resolved identity differs from source manifest")
        if capabilities != tuple(source_manifest["capabilities"]):
            _fail(ManifestErrorCode.INVALID_CAPABILITIES, "capabilities differ from source manifest")
        if minimum != source_manifest["min_platform_version"]:
            _fail(ManifestErrorCode.INVALID_MIN_PLATFORM, "minimum platform differs from source manifest")
        if not {ROLE_FRONTEND, ROLE_MIGRATIONS} <= set(role_counts):
            _fail(ManifestErrorCode.INVALID_LAYERS, "source contract requires frontend and migrations blobs")
        if annotations.get(A_SOURCE_REPOSITORY) != source_repository or _REPOSITORY_RE.fullmatch(str(source_repository)) is None:
            _fail(ManifestErrorCode.INVALID_MANIFEST, "source repository annotation mismatch")
        if annotations.get(A_SOURCE_SHA) != source_sha or _SHA_RE.fullmatch(str(source_sha)) is None:
            _fail(ManifestErrorCode.INVALID_MANIFEST, "source SHA annotation mismatch")
        content_verified = True

    return {
        "plugin_id": plugin_id,
        "version": version,
        "protocol": PLUGIN_PROTOCOL,
        "capabilities": capabilities,
        "min_platform_version": minimum,
        "backend_digest": expected_digest,
        "backend_image_ref": linked_ref,
        "manifest_digest": config_digest,
        "manifest_content_verified": content_verified,
        "layer_count": len(layers),
        "layer_roles": tuple(sorted(role_counts)),
    }


def _source_provenance(builder_id: str, build_type: str, repository: str, sha: str, run_id: str) -> dict[str, Any]:
    if not builder_id.startswith("ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@"):
        _fail(ManifestErrorCode.INVALID_EVIDENCE, "builder is not the trusted plugin workflow")
    if _REPOSITORY_RE.fullmatch(repository) is None or _SHA_RE.fullmatch(sha) is None or not run_id:
        _fail(ManifestErrorCode.INVALID_EVIDENCE, "invalid source/run identity")
    return {
        "builder": {"id": "https://github.com/" + builder_id},
        "buildType": build_type,
        "invocation": {
            "configSource": {
                "uri": f"git+https://github.com/{repository}@{sha}",
                "digest": {"sha1": sha},
            }
        },
        "metadata": {"buildInvocationId": run_id},
    }


def _statement_has_subject(statement: dict[str, Any], digest: str) -> bool:
    expected = digest.removeprefix("sha256:")
    subjects = statement.get("subject")
    return isinstance(subjects, list) and any(
        isinstance(subject, dict)
        and subject.get("name")
        and subject.get("digest", {}).get("sha256") == expected
        for subject in subjects
    )


def prepare_backend_evidence(
    evidence_dir: str,
    backend_image_manifest_digest: str,
    source_repository: str,
    source_sha: str,
    builder_id: str,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate buildx backend evidence and bind it to the trusted workflow."""
    expected_digest = _require_digest(backend_image_manifest_digest, "backend image manifest digest")
    candidates: dict[str, list[dict[str, Any]]] = {SPDX_PREDICATE_TYPE: [], "slsa": []}
    for path in sorted(Path(evidence_dir).rglob("*.json")):
        payload = _read_json(path)
        if not isinstance(payload, dict) or "predicateType" not in payload:
            continue
        if not _statement_has_subject(payload, expected_digest):
            _fail(ManifestErrorCode.INVALID_EVIDENCE, f"buildx evidence has wrong subject: {path}")
        if payload.get("predicateType") == SPDX_PREDICATE_TYPE:
            candidates[SPDX_PREDICATE_TYPE].append(payload)
        elif payload.get("predicateType") in SLSA_PREDICATE_TYPES:
            candidates["slsa"].append(payload)
    if len(candidates[SPDX_PREDICATE_TYPE]) != 1 or len(candidates["slsa"]) != 1:
        _fail(ManifestErrorCode.INVALID_EVIDENCE, "expected exactly one buildx SBOM and one provenance statement")
    sbom_statement = candidates[SPDX_PREDICATE_TYPE][0]
    provenance_statement = candidates["slsa"][0]
    sbom = sbom_statement.get("predicate")
    original_provenance = provenance_statement.get("predicate")
    if not isinstance(sbom, dict) or not isinstance(sbom.get("packages"), list) or not sbom["packages"]:
        _fail(ManifestErrorCode.INVALID_EVIDENCE, "buildx SPDX predicate has no packages")
    if not isinstance(original_provenance, dict) or not original_provenance.get("builder", {}).get("id"):
        _fail(ManifestErrorCode.INVALID_EVIDENCE, "buildx provenance has no builder identity")
    provenance = _source_provenance(builder_id, BACKEND_BUILD_TYPE, source_repository, source_sha, run_id)
    provenance["invocation"]["parameters"] = {
        "buildxProvenance": original_provenance,
        "buildxEvidenceSha256": hashlib.sha256(
            json.dumps(provenance_statement, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    return sbom, provenance


def build_bundle_evidence(
    source_manifest: dict[str, Any],
    oci_manifest: dict[str, Any],
    bundle_digest: str,
    source_repository: str,
    source_sha: str,
    builder_id: str,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate independent bundle SBOM + SLSA predicates (never backend reuse)."""
    bundle_digest = _require_digest(bundle_digest, "bundle digest")
    summary = validate_signed_manifest(
        oci_manifest,
        backend_digest=oci_manifest.get("annotations", {}).get(A_BACKEND_DIGEST, ""),
        source_manifest=source_manifest,
        backend_ref=oci_manifest.get("annotations", {}).get(A_BACKEND_IMAGE_REF),
        source_repository=source_repository,
        source_sha=source_sha,
    )
    provenance = _source_provenance(builder_id, BUNDLE_BUILD_TYPE, source_repository, source_sha, run_id)
    provenance["invocation"]["parameters"] = {
        "artifactKind": "agentscale-plugin-bundle",
        "canonicalManifestDigest": summary["manifest_digest"],
        "layers": [
            {"digest": layer["digest"], "mediaType": layer["mediaType"]}
            for layer in oci_manifest["layers"]
        ],
    }
    package_entries = [
        {
            "SPDXID": "SPDXRef-bundle",
            "name": f"{summary['plugin_id']}-oci-bundle",
            "downloadLocation": "NOASSERTION",
            "checksums": [{"algorithm": "SHA256", "checksumValue": bundle_digest.removeprefix("sha256:")}],
        },
        {
            "SPDXID": "SPDXRef-canonical-manifest",
            "name": f"{summary['plugin_id']}-canonical-manifest",
            "downloadLocation": "NOASSERTION",
            "checksums": [{"algorithm": "SHA256", "checksumValue": summary["manifest_digest"].removeprefix("sha256:")}],
        },
    ]
    relationships = [
        {"spdxElementId": "SPDXRef-DOCUMENT", "relatedSpdxElement": "SPDXRef-bundle", "relationshipType": "DESCRIBES"}
    ]
    for index, layer in enumerate(oci_manifest["layers"]):
        package_id = f"SPDXRef-layer-{index}"
        package_entries.append(
            {
                "SPDXID": package_id,
                "name": layer["annotations"]["org.opencontainers.image.title"],
                "downloadLocation": "NOASSERTION",
                "checksums": [{"algorithm": "SHA256", "checksumValue": layer["digest"].removeprefix("sha256:")}],
            }
        )
        relationships.append(
            {"spdxElementId": "SPDXRef-bundle", "relatedSpdxElement": package_id, "relationshipType": "CONTAINS"}
        )
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{summary['plugin_id']}-{summary['version']}-bundle",
        "documentNamespace": f"https://agentscale.dev/spdx/{source_repository}/{source_sha}/{bundle_digest}",
        "comment": f"source_repository={source_repository};source_sha={source_sha};artifact=bundle",
        "creationInfo": {
            "created": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "creators": ["Tool: agentscale-canonical-bundle-sbom/v1"],
        },
        "packages": package_entries,
        "relationships": relationships,
    }
    return sbom, provenance


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="validate layout and create staging payloads")
    prepare.add_argument("--source-root", required=True)
    prepare.add_argument("--plugin-dir", required=True)
    prepare.add_argument("--manifest-path", default="")
    prepare.add_argument("--output-dir", required=True)

    build = commands.add_parser("build", help="build canonical OCI and ORAS annotation files")
    build.add_argument("--source-manifest", required=True)
    build.add_argument("--canonical-manifest", required=True)
    build.add_argument("--backend-ref", required=True)
    build.add_argument("--frontend-archive", required=True)
    build.add_argument("--migrations-archive", required=True)
    build.add_argument("--source-repository", required=True)
    build.add_argument("--source-sha", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--annotation-output", required=True)

    for name in ("backend-evidence", "bundle-evidence"):
        command = commands.add_parser(name)
        command.add_argument("--source-repository", required=True)
        command.add_argument("--source-sha", required=True)
        command.add_argument("--builder-id", required=True)
        command.add_argument("--run-id", required=True)
        command.add_argument("--output-sbom", required=True)
        command.add_argument("--output-provenance", required=True)
        if name == "backend-evidence":
            command.add_argument("--evidence-dir", required=True)
            command.add_argument("--backend-image-manifest-digest", required=True)
        else:
            command.add_argument("--source-manifest", required=True)
            command.add_argument("--oci-manifest", required=True)
            command.add_argument("--bundle-digest", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_package(args.source_root, args.plugin_dir, args.manifest_path, args.output_dir)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "build":
            source_manifest = _read_json(args.source_manifest)
            manifest = build_bundle_manifest(
                source_manifest,
                args.backend_ref,
                args.canonical_manifest,
                args.frontend_archive,
                args.migrations_archive,
                args.source_repository,
                args.source_sha,
            )
            _write_json(args.output, manifest)
            _write_json(args.annotation_output, annotation_file(manifest))
            print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "backend-evidence":
            sbom, provenance = prepare_backend_evidence(
                args.evidence_dir,
                args.backend_image_manifest_digest,
                args.source_repository,
                args.source_sha,
                args.builder_id,
                args.run_id,
            )
        else:
            sbom, provenance = build_bundle_evidence(
                _read_json(args.source_manifest),
                _read_json(args.oci_manifest),
                args.bundle_digest,
                args.source_repository,
                args.source_sha,
                args.builder_id,
                args.run_id,
            )
        _write_json(args.output_sbom, sbom)
        _write_json(args.output_provenance, provenance)
        print(json.dumps({"sbom": args.output_sbom, "provenance": args.output_provenance}))
        return 0
    except ManifestError as exc:
        print(f"{exc.code}: {exc.reason}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"{ManifestErrorCode.INVALID_MANIFEST}: unexpected failure: {exc!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
