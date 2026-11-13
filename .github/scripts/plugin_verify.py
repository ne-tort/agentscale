#!/usr/bin/env python3
"""Fail-closed verification of quarantined and promoted plugin OCI artifacts.

Both the backend image and the OCI bundle require a keyless signature and their
own SPDX + SLSA attestations. Evidence is validated by subject digest, source
repository/full commit SHA, trusted builder workflow, and artifact-specific
build type. The full canonical source manifest must be the OCI config and an
identical explicit blob. No fixed layer count is imposed.

Importing this module performs no process, network, or registry operation.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

DECISION_PUBLISH = "PUBLISH_ALLOWED"
DECISION_QUARANTINE = "QUARANTINED"
PLUGIN_PROTOCOL = "plugin-rpc/1"
CAPABILITY_ALLOWLIST = frozenset(
    {"cache", "objects", "docs", "events", "jobs", "pods", "mcp", "secrets", "network"}
)
DEFAULT_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
SPDX_PREDICATE_TYPE = "https://spdx.dev/Document"
SLSA_PREDICATE_TYPES = frozenset({"https://slsa.dev/provenance/v0.2"})

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
_REF_RE = re.compile(
    r"^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]+)?"
    r"/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"@sha256:[0-9a-f]{64}$"
)


class VerifyErrorCode:
    OK = "OK"
    UNSIGNED = "UNSIGNED"
    VERIFY_FAILED = "VERIFY_FAILED"
    DIGEST_CONFLICT = "DIGEST_CONFLICT"
    INVALID_DIGEST = "INVALID_DIGEST"
    LATEST_FORBIDDEN = "LATEST_FORBIDDEN"
    INCOMPATIBLE = "INCOMPATIBLE"
    INVALID_MANIFEST = "INVALID_MANIFEST"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    MISSING_INPUT = "MISSING_INPUT"
    QUARANTINED = "QUARANTINED"


class VerifyError(Exception):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


CosignRunner = Callable[[list[str]], Any]


def _ci_cosign_runner(argv: list[str]) -> dict[str, Any]:
    import subprocess

    completed = subprocess.run(argv, check=True, capture_output=True, text=True)
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _cosign_verify_command(ref: str, identity: str, oidc_issuer: str) -> list[str]:
    return [
        "cosign", "verify",
        "--certificate-identity", identity,
        "--certificate-oidc-issuer", oidc_issuer,
        ref,
    ]


def _cosign_verify_attestation_command(
    ref: str, predicate_type: str, identity: str, oidc_issuer: str
) -> list[str]:
    return [
        "cosign", "verify-attestation",
        "--type", predicate_type,
        "--certificate-identity", identity,
        "--certificate-oidc-issuer", oidc_issuer,
        ref,
    ]


def _run_cosign(runner: CosignRunner, argv: list[str], label: str) -> dict[str, Any]:
    if not callable(runner):
        raise VerifyError(VerifyErrorCode.MISSING_INPUT, "cosign_runner is required")
    if not isinstance(argv, list) or not argv or argv[0] != "cosign":
        raise VerifyError(VerifyErrorCode.MISSING_INPUT, f"{label}: invalid cosign command")
    for argument in argv:
        if not isinstance(argument, str) or any(char in argument for char in ("\n", "\r", "\0")):
            raise VerifyError(VerifyErrorCode.MISSING_INPUT, f"{label}: unsafe command argument")
    try:
        result = runner(argv)
    except VerifyError:
        raise
    except Exception as exc:
        raise VerifyError(VerifyErrorCode.VERIFY_FAILED, f"{label}: {exc!r}") from exc
    if result is False:
        raise VerifyError(VerifyErrorCode.VERIFY_FAILED, f"{label}: cosign returned False")
    if isinstance(result, int) and result != 0:
        raise VerifyError(VerifyErrorCode.VERIFY_FAILED, f"{label}: cosign rc={result}")
    if isinstance(result, dict) and result.get("ok") is False:
        raise VerifyError(VerifyErrorCode.VERIFY_FAILED, f"{label}: ok=false")
    returncode = result.get("returncode", 0) if isinstance(result, dict) else getattr(result, "returncode", 0)
    if returncode != 0:
        raise VerifyError(VerifyErrorCode.VERIFY_FAILED, f"{label}: cosign rc={returncode}")
    stdout = result.get("stdout", "") if isinstance(result, dict) else getattr(result, "stdout", "")
    return {"label": label, "ref": argv[-1], "verified": True, "stdout": stdout or ""}


def _require_digest(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerifyError(VerifyErrorCode.INVALID_DIGEST, f"{field_name}: missing digest")
    candidate = value.strip()
    if ":latest" in candidate or candidate.endswith("/latest"):
        raise VerifyError(VerifyErrorCode.LATEST_FORBIDDEN, f"{field_name}: :latest is forbidden")
    if _DIGEST_RE.fullmatch(candidate) is None:
        raise VerifyError(VerifyErrorCode.INVALID_DIGEST, f"{field_name}: invalid digest {candidate!r}")
    return candidate


def _require_ref(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerifyError(VerifyErrorCode.MISSING_INPUT, f"{field_name}: missing digest-pinned ref")
    candidate = value.strip()
    if ":latest" in candidate or candidate.endswith("/latest"):
        raise VerifyError(VerifyErrorCode.LATEST_FORBIDDEN, f"{field_name}: :latest is forbidden")
    if _REF_RE.fullmatch(candidate) is None:
        raise VerifyError(VerifyErrorCode.INVALID_DIGEST, f"{field_name}: expected repo@sha256:<64 hex>")
    return candidate


def _load_manifest_module() -> Any:
    try:
        import plugin_manifest_builder as module  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import plugin_manifest_builder as module  # type: ignore[import-not-found]
    if not hasattr(module, "validate_signed_manifest"):
        raise VerifyError(VerifyErrorCode.INVALID_MANIFEST, "canonical builder validator is unavailable")
    return module


def _read_manifest(manifest_path: str) -> dict[str, Any]:
    if not isinstance(manifest_path, str) or not manifest_path.strip():
        raise VerifyError(VerifyErrorCode.MISSING_INPUT, "manifest path is required")
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise VerifyError(VerifyErrorCode.MISSING_INPUT, f"cannot read manifest: {exc!r}") from exc
    if ":latest" in raw:
        raise VerifyError(VerifyErrorCode.LATEST_FORBIDDEN, "manifest contains :latest")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VerifyError(VerifyErrorCode.INVALID_MANIFEST, f"invalid JSON: {exc!r}") from exc
    if not isinstance(payload, dict):
        raise VerifyError(VerifyErrorCode.INVALID_MANIFEST, "manifest root must be an object")
    return payload


def validate_manifest(manifest: dict[str, Any], *, backend_digest: str) -> dict[str, Any]:
    """Compatibility wrapper retaining the canonical plugin_id regression gate."""
    module = _load_manifest_module()
    try:
        summary = module.validate_signed_manifest(manifest, backend_digest=backend_digest)
        plugin_id = summary.get("plugin_id")
        if not isinstance(plugin_id, str) or _PLUGIN_ID_RE.fullmatch(plugin_id) is None:
            raise VerifyError(
                VerifyErrorCode.INVALID_MANIFEST,
                f"plugin_id must match ^[a-z][a-z0-9_]*$: {plugin_id!r}",
            )
        return summary
    except VerifyError:
        raise
    except module.ManifestError:
        raise
    except Exception as exc:
        raise VerifyError(VerifyErrorCode.INVALID_MANIFEST, f"manifest validation failed: {exc!r}") from exc


def _semver_key(value: str) -> tuple[int, int, int, int, tuple[Any, ...]]:
    match = _SEMVER_RE.fullmatch(value)
    if match is None:
        raise VerifyError(VerifyErrorCode.INCOMPATIBLE, f"invalid SemVer: {value!r}")
    prerelease = match.group("prerelease")
    parts: list[tuple[int, Any]] = []
    if prerelease:
        for chunk in prerelease.split("."):
            parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
        return (
            int(match.group("major")), int(match.group("minor")), int(match.group("patch")),
            0, tuple(parts),
        )
    return (
        int(match.group("major")), int(match.group("minor")), int(match.group("patch")), 1, (),
    )


@dataclass(frozen=True)
class GateResult:
    compatible: bool
    code: str
    reason: str
    checks: list[dict[str, Any]] = field(default_factory=list)


class CompatibilityGate:
    def __init__(
        self,
        platform_version: str | None = None,
        granted_capabilities: Iterable[str] | None = None,
        *,
        supported_capabilities: Iterable[str] = CAPABILITY_ALLOWLIST,
    ) -> None:
        self.platform_version = (platform_version or os.environ.get("PLATFORM_VERSION") or "").strip()
        raw_grants = granted_capabilities
        if raw_grants is None:
            raw_grants = [
                part.strip()
                for part in os.environ.get("PLUGIN_GRANTED_CAPABILITIES", "").split(",")
                if part.strip()
            ]
        self.granted_capabilities = frozenset(str(cap).strip() for cap in raw_grants if str(cap).strip())
        self.supported_capabilities = frozenset(supported_capabilities)

    def evaluate(self, summary: dict[str, Any]) -> GateResult:
        checks: list[dict[str, Any]] = []

        def check(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        version = summary.get("version", "")
        check("semver", isinstance(version, str) and _SEMVER_RE.fullmatch(version) is not None, repr(version))
        check("protocol", summary.get("protocol") == PLUGIN_PROTOCOL, repr(summary.get("protocol")))
        capabilities = tuple(summary.get("capabilities", ()))
        unsupported = tuple(cap for cap in capabilities if cap not in self.supported_capabilities)
        check("capability_allowlist", not unsupported, repr(unsupported))
        check("platform_version", bool(self.platform_version), self.platform_version or "missing")
        if not self.granted_capabilities:
            check("capability_grant", False, "no independently reviewed CapabilityGrant")
        else:
            missing = tuple(cap for cap in capabilities if cap not in self.granted_capabilities)
            check("capability_grant", not missing, repr(missing))
        try:
            version_ok = bool(self.platform_version) and _semver_key(
                summary.get("min_platform_version", "")
            ) <= _semver_key(self.platform_version)
            detail = "minimum platform comparison evaluated"
        except VerifyError as exc:
            version_ok, detail = False, exc.reason
        check("min_platform_version", version_ok, detail)
        failed = [item for item in checks if not item["ok"]]
        if failed:
            return GateResult(
                False,
                VerifyErrorCode.INCOMPATIBLE,
                "; ".join(f"{item['name']}: {item['detail']}" for item in failed),
                checks,
            )
        return GateResult(True, VerifyErrorCode.OK, "compatibility gate passed", checks)


def _decode_statement(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        result: list[dict[str, Any]] = []
        for item in value:
            result.extend(_decode_statement(item))
        return result
    if not isinstance(value, dict):
        return []
    if "predicateType" in value and "predicate" in value:
        return [value]
    encoded = value.get("payload")
    if isinstance(encoded, str):
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            return _decode_statement(json.loads(decoded))
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "invalid attestation payload") from exc
    return []


def _parse_attestations(stdout: str) -> list[dict[str, Any]]:
    if not isinstance(stdout, str) or not stdout.strip():
        raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "cosign returned no attestation payload")
    try:
        statements = _decode_statement(json.loads(stdout))
    except json.JSONDecodeError:
        statements = []
        for line in stdout.splitlines():
            if line.strip():
                try:
                    statements.extend(_decode_statement(json.loads(line)))
                except json.JSONDecodeError as exc:
                    raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "non-JSON attestation output") from exc
    if not statements:
        raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "no in-toto statements in attestation output")
    return statements


def _subject_matches(statement: dict[str, Any], ref: str) -> bool:
    expected = ref.rsplit("@sha256:", 1)[1]
    subjects = statement.get("subject")
    return isinstance(subjects, list) and any(
        isinstance(subject, dict)
        and bool(subject.get("name"))
        and subject.get("digest", {}).get("sha256") == expected
        for subject in subjects
    )


def _validate_sbom_predicate(
    predicate: dict[str, Any],
    *,
    kind: str,
    ref: str,
    source_repository: str,
    source_sha: str,
    manifest_digest: str,
) -> None:
    # Native BuildKit/Syft emits SPDX-2.2 for the backend image, so signed
    # backend evidence accepts 2.2 as well as 2.3. The bundle predicate is
    # generated by the trusted builder itself and stays strictly SPDX-2.3.
    # Namespace, packages, checksum and source binding below are unchanged.
    allowed_spdx_versions = {"SPDX-2.2", "SPDX-2.3"} if kind == "backend" else {"SPDX-2.3"}
    if predicate.get("spdxVersion") not in allowed_spdx_versions or not predicate.get("documentNamespace"):
        raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "SPDX version/namespace is missing")
    packages = predicate.get("packages")
    if not isinstance(packages, list) or not packages:
        raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "SPDX packages are missing")
    for package in packages:
        if not isinstance(package, dict) or not package.get("SPDXID") or not package.get("name"):
            raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "incomplete SPDX package")
    if kind == "bundle":
        namespace = str(predicate["documentNamespace"])
        comment = str(predicate.get("comment", ""))
        if f"/{source_repository}/{source_sha}/" not in namespace:
            raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "bundle SBOM source identity mismatch")
        if f"source_repository={source_repository}" not in comment or f"source_sha={source_sha}" not in comment:
            raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "bundle SBOM source comment mismatch")
        expected_checksums = {
            ref.rsplit("@sha256:", 1)[1],
            manifest_digest.removeprefix("sha256:"),
        }
        actual_checksums = {
            checksum.get("checksumValue")
            for package in packages
            for checksum in package.get("checksums", [])
            if isinstance(checksum, dict) and checksum.get("algorithm") == "SHA256"
        }
        if not expected_checksums <= actual_checksums:
            raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "bundle SBOM lacks bundle/config checksums")


def _validate_provenance_predicate(
    predicate: dict[str, Any],
    *,
    kind: str,
    source_repository: str,
    source_sha: str,
    builder_identity: str,
    manifest_digest: str,
) -> None:
    expected_build_type = (
        "https://agentscale.dev/plugin-backend-buildx/v1"
        if kind == "backend"
        else "https://agentscale.dev/plugin-oci-bundle/v1"
    )
    if predicate.get("buildType") != expected_build_type:
        raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, f"wrong {kind} provenance buildType")
    if predicate.get("builder", {}).get("id") != "https://github.com/" + builder_identity:
        raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "provenance builder identity mismatch")
    source = predicate.get("invocation", {}).get("configSource", {})
    if source.get("uri") != f"git+https://github.com/{source_repository}@{source_sha}":
        raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "provenance source URI mismatch")
    if source.get("digest", {}).get("sha1") != source_sha:
        raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "provenance source SHA mismatch")
    metadata = predicate.get("metadata", {})
    if not (metadata.get("buildInvocationId") or metadata.get("buildInvocationID")):
        raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "provenance run identity is missing")
    internal = predicate.get("invocation", {}).get("parameters")
    if not isinstance(internal, dict):
        raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "SLSA v0.2 invocation.parameters are missing")
    if kind == "backend":
        buildx = internal.get("buildxProvenance")
        if not isinstance(buildx, dict) or not buildx.get("builder", {}).get("id") or not buildx.get("buildType"):
            raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "backend lacks genuine buildx provenance")
    elif (
        internal.get("canonicalManifestDigest") != manifest_digest
        or internal.get("artifactKind") != "agentscale-plugin-bundle"
    ):
        raise VerifyError(VerifyErrorCode.INVALID_EVIDENCE, "bundle provenance is not bound to canonical config")


def _verify_evidence(
    runner: CosignRunner,
    *,
    ref: str,
    kind: str,
    identity: str,
    issuer: str,
    source_repository: str,
    source_sha: str,
    builder_identity: str,
    manifest_digest: str,
    validated_bundle_predicates: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for evidence_type, cli_type in (("sbom", "spdxjson"), ("provenance", "slsaprovenance")):
        result = _run_cosign(
            runner,
            _cosign_verify_attestation_command(ref, cli_type, identity, issuer),
            f"{kind}-{evidence_type}",
        )
        statements = _parse_attestations(result["stdout"])
        valid = False
        for statement in statements:
            predicate_type = statement.get("predicateType")
            type_matches = (
                predicate_type == SPDX_PREDICATE_TYPE
                if evidence_type == "sbom"
                else predicate_type in SLSA_PREDICATE_TYPES
            )
            if not type_matches or not _subject_matches(statement, ref):
                continue
            predicate = statement.get("predicate")
            if not isinstance(predicate, dict):
                continue
            try:
                if evidence_type == "sbom":
                    _validate_sbom_predicate(
                        predicate,
                        kind=kind,
                        ref=ref,
                        source_repository=source_repository,
                        source_sha=source_sha,
                        manifest_digest=manifest_digest,
                    )
                else:
                    _validate_provenance_predicate(
                        predicate,
                        kind=kind,
                        source_repository=source_repository,
                        source_sha=source_sha,
                        builder_identity=builder_identity,
                        manifest_digest=manifest_digest,
                    )
                if kind == "bundle" and validated_bundle_predicates is not None:
                    validated_bundle_predicates[evidence_type] = predicate
                valid = True
                break
            except VerifyError:
                continue
        if not valid:
            raise VerifyError(
                VerifyErrorCode.INVALID_EVIDENCE,
                f"{kind}: no {evidence_type} statement with valid subject/source/sha/builder/content",
            )
        checks.append({"name": f"attestation:{kind}:{evidence_type}", "ok": True, "detail": ref})
    return checks


def _quarantine(code: str, reason: str, checks: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": DECISION_QUARANTINE,
        "publish_allowed": False,
        "code": code,
        "reason": reason,
        "checks": checks,
        "manifest_summary": context.get("manifest_summary"),
        "backend_digest": context.get("backend_digest"),
        "artifact_ref": context.get("artifact_ref"),
        "backend_ref": context.get("backend_ref"),
        "quarantine_evidence": {
            "code": code, "reason": reason, "ref": context.get("artifact_ref"),
            "backend_digest": context.get("backend_digest"), "checks": checks,
        },
    }


def _allowed(checks: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": DECISION_PUBLISH,
        "publish_allowed": True,
        "code": VerifyErrorCode.OK,
        "reason": "signatures, independent evidence, canonical OCI and compatibility gate passed",
        "checks": checks,
        "manifest_summary": context.get("manifest_summary"),
        "backend_digest": context.get("backend_digest"),
        "artifact_ref": context.get("artifact_ref"),
        "backend_ref": context.get("backend_ref"),
        "quarantine_evidence": None,
    }


def _require_safe_output_path(value: str, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or any(char in value for char in ("\n", "\r", "\0"))
    ):
        raise VerifyError(VerifyErrorCode.MISSING_INPUT, f"{field_name}: unsafe output path")
    path = os.path.abspath(value)
    if os.path.islink(path) or (os.path.lexists(path) and not os.path.isfile(path)):
        raise VerifyError(VerifyErrorCode.MISSING_INPUT, f"{field_name}: unsafe output path")
    parent = os.path.dirname(path)
    while True:
        if os.path.islink(parent):
            raise VerifyError(VerifyErrorCode.MISSING_INPUT, f"{field_name}: symlinked output parent")
        next_parent = os.path.dirname(parent)
        if next_parent == parent:
            break
        parent = next_parent
    return path


def _bundle_output_requests(
    bundle_sbom_output: str | None,
    bundle_provenance_output: str | None,
) -> list[tuple[str, str, str]]:
    requests: list[tuple[str, str, str]] = []
    for value, evidence_type, field_name in (
        (bundle_sbom_output, "sbom", "bundle_sbom_output"),
        (bundle_provenance_output, "provenance", "bundle_provenance_output"),
    ):
        if value is not None:
            requests.append((_require_safe_output_path(value, field_name), evidence_type, field_name))
    normalized = [os.path.normcase(path) for path, _, _ in requests]
    if len(set(normalized)) != len(normalized):
        raise VerifyError(VerifyErrorCode.MISSING_INPUT, "bundle evidence outputs must be distinct")
    return requests


def _write_bundle_predicate_outputs(
    module: Any,
    predicates: dict[str, dict[str, Any]],
    bundle_sbom_output: str | None,
    bundle_provenance_output: str | None,
) -> None:
    """Atomically export only predicates accepted by every evidence validator."""
    requests = _bundle_output_requests(bundle_sbom_output, bundle_provenance_output)
    staged: list[tuple[str, str]] = []
    backups: list[tuple[str, str]] = []
    committed: list[str] = []
    success = False
    try:
        for path, evidence_type, field_name in requests:
            predicate = predicates.get(evidence_type)
            if predicate is None:
                raise VerifyError(
                    VerifyErrorCode.INVALID_EVIDENCE,
                    f"{field_name}: validated bundle predicate is unavailable",
                )
            parent = os.path.dirname(path)
            os.makedirs(parent, exist_ok=True)
            _require_safe_output_path(path, field_name)
            fd, temporary = tempfile.mkstemp(prefix=".plugin-evidence-", dir=parent)
            os.close(fd)
            staged.append((temporary, path))
            module._write_json(temporary, predicate)

        # Preserve any first-run files until every replacement is known to have
        # succeeded. A failed batch restores them and leaves no partial export.
        for path, _, field_name in requests:
            _require_safe_output_path(path, field_name)
            if os.path.exists(path):
                fd, backup = tempfile.mkstemp(prefix=".plugin-evidence-backup-", dir=os.path.dirname(path))
                os.close(fd)
                try:
                    os.replace(path, backup)
                except Exception:
                    os.unlink(backup)
                    raise
                backups.append((path, backup))
        for temporary, path in staged:
            os.replace(temporary, path)
            committed.append(path)
        success = True
    except Exception as exc:
        for path in reversed(committed):
            try:
                os.unlink(path)
            except OSError:
                pass
        for path, backup in reversed(backups):
            try:
                os.replace(backup, path)
            except OSError:
                # Keep the backup rather than destroying the only original copy.
                pass
        if isinstance(exc, VerifyError):
            raise
        raise VerifyError(VerifyErrorCode.VERIFY_FAILED, f"cannot export bundle evidence: {exc!r}") from exc
    finally:
        for temporary, _ in staged:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        if success:
            for _, backup in backups:
                try:
                    os.unlink(backup)
                except OSError:
                    pass


def verify_artifact(
    manifest_path: str,
    backend_digest: str,
    *,
    cosign_runner: CosignRunner,
    artifact_ref: str | None = None,
    backend_ref: str | None = None,
    source_manifest_path: str | None = None,
    canonical_manifest_path: str | None = None,
    pushed_manifest_path: str | None = None,
    source_repository: str | None = None,
    source_sha: str | None = None,
    builder_identity: str | None = None,
    platform_version: str | None = None,
    granted_capabilities: Iterable[str] | None = None,
    certificate_identity: str | None = None,
    certificate_oidc_issuer: str | None = None,
    bundle_sbom_output: str | None = None,
    bundle_provenance_output: str | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    context: dict[str, Any] = {}
    validated_bundle_predicates: dict[str, dict[str, Any]] = {}

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        if not callable(cosign_runner):
            raise VerifyError(VerifyErrorCode.MISSING_INPUT, "cosign_runner is required")
        _bundle_output_requests(bundle_sbom_output, bundle_provenance_output)
        digest = _require_digest(backend_digest, "backend_digest")
        artifact = _require_ref(artifact_ref or os.environ.get("PLUGIN_ARTIFACT_REF", ""), "artifact_ref")
        backend = _require_ref(backend_ref or os.environ.get("PLUGIN_BACKEND_REF", ""), "backend_ref")
        artifact_digest = artifact.rsplit("@", 1)[1]
        if artifact == backend or artifact_digest == digest:
            raise VerifyError(VerifyErrorCode.DIGEST_CONFLICT, "backend and bundle must be distinct artifacts")
        if not backend.endswith("@" + digest):
            raise VerifyError(VerifyErrorCode.DIGEST_CONFLICT, "backend ref/digest mismatch")
        backend_repository = backend.rsplit("@", 1)[0]
        artifact_repository = artifact.rsplit("@", 1)[0]
        if not backend_repository.endswith("/backend") or artifact_repository != backend_repository[:-8] + "/bundle":
            raise VerifyError(VerifyErrorCode.DIGEST_CONFLICT, "backend/bundle repository pair mismatch")
        context.update(backend_digest=digest, artifact_ref=artifact, backend_ref=backend)
        record("digest_pinned_refs", True, f"bundle={artifact}; backend={backend}")

        repository = (source_repository or "").strip()
        sha = (source_sha or "").strip()
        builder = (builder_identity or "").strip()
        if _REPOSITORY_RE.fullmatch(repository) is None or _SHA_RE.fullmatch(sha) is None:
            raise VerifyError(VerifyErrorCode.MISSING_INPUT, "source repository/full SHA are required")
        if not builder.startswith("ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@"):
            raise VerifyError(VerifyErrorCode.MISSING_INPUT, "untrusted builder workflow identity")
        identity = (certificate_identity or os.environ.get("COSIGN_CERTIFICATE_IDENTITY") or "").strip()
        if identity != "https://github.com/" + builder:
            raise VerifyError(VerifyErrorCode.MISSING_INPUT, "certificate identity must equal trusted builder")
        issuer = (
            certificate_oidc_issuer
            or os.environ.get("COSIGN_CERTIFICATE_OIDC_ISSUER")
            or DEFAULT_OIDC_ISSUER
        ).strip()
        if issuer != DEFAULT_OIDC_ISSUER:
            raise VerifyError(VerifyErrorCode.MISSING_INPUT, "unexpected OIDC issuer")

        for label, ref in (("oci-artifact", artifact), ("backend-image", backend)):
            _run_cosign(cosign_runner, _cosign_verify_command(ref, identity, issuer), label)
            record(f"cosign_verify:{label}", True, ref)

        if not source_manifest_path or not canonical_manifest_path or not pushed_manifest_path:
            raise VerifyError(VerifyErrorCode.MISSING_INPUT, "source, canonical and pushed manifests are required")
        module = _load_manifest_module()
        source_manifest = module._read_json(source_manifest_path)
        expected = module._read_json(manifest_path)
        pushed = module._read_json(pushed_manifest_path)
        if not isinstance(expected, dict) or not isinstance(pushed, dict):
            raise VerifyError(VerifyErrorCode.INVALID_MANIFEST, "OCI manifests must be JSON objects")
        # Builder sets org.opencontainers.image.created explicitly; ORAS preserves it.
        if pushed != expected:
            for key in sorted(set(list(pushed.keys()) + list(expected.keys()))):
                if pushed.get(key) != expected.get(key):
                    pushed_value = json.dumps(pushed.get(key), default=str)[:300]
                    expected_value = json.dumps(expected.get(key), default=str)[:300]
                    print(
                        f"::error::manifest diff key={key}: pushed={pushed_value} expected={expected_value}",
                        file=sys.stderr,
                    )
            raise VerifyError(VerifyErrorCode.INVALID_MANIFEST, "registry OCI manifest differs from signed expectation")
        canonical_bytes = module.canonical_manifest_bytes(source_manifest)
        with open(canonical_manifest_path, "rb") as handle:
            actual_canonical = handle.read()
        if actual_canonical != canonical_bytes:
            raise VerifyError(VerifyErrorCode.INVALID_MANIFEST, "canonical manifest bytes differ from full source")
        if "/plugins-quarantine/" in backend:
            # The signed annotation declares the intended release deploy pin while
            # quarantine is still staging. No registry operation uses that release
            # ref before promotion. Release reverify below requires this same
            # annotation to equal the actual promoted backend ref.
            expected_signed_backend_ref = backend.replace("/plugins-quarantine/", "/plugins/", 1)
            if "/plugins-quarantine/" not in artifact:
                raise VerifyError(VerifyErrorCode.DIGEST_CONFLICT, "quarantine backend/bundle namespace mismatch")
        elif "/plugins/" in backend:
            # Promotion changes repository, never digest or signed annotation.
            expected_signed_backend_ref = backend
            if "/plugins/" not in artifact:
                raise VerifyError(VerifyErrorCode.DIGEST_CONFLICT, "release backend/bundle namespace mismatch")
        else:
            raise VerifyError(VerifyErrorCode.INVALID_DIGEST, "backend ref is outside plugin trust namespaces")
        summary = module.validate_signed_manifest(
            expected,
            backend_digest=digest,
            source_manifest=source_manifest,
            backend_ref=expected_signed_backend_ref,
            source_repository=repository,
            source_sha=sha,
        )
        if summary["backend_image_ref"] != expected_signed_backend_ref:
            raise VerifyError(VerifyErrorCode.DIGEST_CONFLICT, "signed release backend link mismatch")
        if not summary.get("manifest_content_verified"):
            raise VerifyError(VerifyErrorCode.INVALID_MANIFEST, "full source manifest was not verified")
        context["manifest_summary"] = summary
        record("canonical_manifest_config_and_blob", True, summary["manifest_digest"])
        record("backend_bundle_link", True, summary["backend_image_ref"])

        for kind, ref in (("backend", backend), ("bundle", artifact)):
            checks.extend(
                _verify_evidence(
                    cosign_runner,
                    ref=ref,
                    kind=kind,
                    identity=identity,
                    issuer=issuer,
                    source_repository=repository,
                    source_sha=sha,
                    builder_identity=builder,
                    manifest_digest=summary["manifest_digest"],
                    validated_bundle_predicates=(
                        validated_bundle_predicates if kind == "bundle" else None
                    ),
                )
            )

        gate = CompatibilityGate(platform_version, granted_capabilities)
        gate_result = gate.evaluate(summary)
        for item in gate_result.checks:
            record(f"gate:{item['name']}", item["ok"], item["detail"])
        if not gate_result.compatible:
            return _quarantine(VerifyErrorCode.INCOMPATIBLE, gate_result.reason, checks, context)
        _write_bundle_predicate_outputs(
            module,
            validated_bundle_predicates,
            bundle_sbom_output,
            bundle_provenance_output,
        )
        return _allowed(checks, context)
    except VerifyError as exc:
        return _quarantine(exc.code, exc.reason, checks, context)
    except Exception as exc:
        code = VerifyErrorCode.QUARANTINED
        try:
            module = _load_manifest_module()
            if isinstance(exc, module.ManifestError):
                code = exc.code
        except Exception:
            pass
        return _quarantine(code, f"{type(exc).__name__}: {exc}", checks, context)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--canonical-manifest", required=True)
    parser.add_argument("--pushed-manifest", required=True)
    parser.add_argument("--backend-digest", required=True)
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--backend-ref", required=True)
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--builder-identity", required=True)
    parser.add_argument("--platform-version", default="")
    parser.add_argument("--granted-capabilities", default="")
    parser.add_argument("--certificate-identity", required=True)
    parser.add_argument("--certificate-oidc-issuer", default=DEFAULT_OIDC_ISSUER)
    parser.add_argument("--bundle-sbom-output", default="")
    parser.add_argument("--bundle-provenance-output", default="")
    parser.add_argument("--output", default="")
    return parser


def _write_github_files(decision: dict[str, Any]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"decision={decision['decision']}\n")
            handle.write(f"code={decision['code']}\n")
            handle.write(f"backend_digest={decision.get('backend_digest') or ''}\n")
            handle.write(f"artifact_ref={decision.get('artifact_ref') or ''}\n")
            handle.write(f"backend_ref={decision.get('backend_ref') or ''}\n")
            handle.write(f"publish_allowed={'true' if decision['publish_allowed'] else 'false'}\n")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        rows = [
            f"### {decision['decision']} ({decision['code']})",
            decision["reason"],
            "",
            "QUARANTINED is a trust decision, not a claim that no blob exists.",
            "Only a verified digest may be promoted to release; platform registration is separate.",
            "",
        ]
        for item in decision["checks"]:
            rows.append(f"- {'OK' if item['ok'] else 'FAIL'} {item['name']}: {item['detail']}")
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(rows) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_cli_parser().parse_args(argv)
    decision = verify_artifact(
        args.manifest,
        args.backend_digest,
        cosign_runner=_ci_cosign_runner,
        artifact_ref=args.artifact_ref,
        backend_ref=args.backend_ref,
        source_manifest_path=args.source_manifest,
        canonical_manifest_path=args.canonical_manifest,
        pushed_manifest_path=args.pushed_manifest,
        source_repository=args.source_repository,
        source_sha=args.source_sha,
        builder_identity=args.builder_identity,
        platform_version=args.platform_version or None,
        granted_capabilities=(
            [part.strip() for part in args.granted_capabilities.split(",") if part.strip()]
            if args.granted_capabilities else None
        ),
        certificate_identity=args.certificate_identity,
        certificate_oidc_issuer=args.certificate_oidc_issuer,
        bundle_sbom_output=args.bundle_sbom_output or None,
        bundle_provenance_output=args.bundle_provenance_output or None,
    )
    payload = json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True)
    print(payload)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    _write_github_files(decision)
    if decision["decision"] != DECISION_PUBLISH:
        print(f"::error::{decision['code']}: {decision['reason']} — release is forbidden", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
