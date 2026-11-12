#!/usr/bin/env python3
"""Restore verification inputs from an immutable, already-signed OCI bundle.

A rerun of the same commit must keep the exact backend/bundle digests that the
durable PublicationApproval binds to. Instead of rebuilding and re-pushing, the
workflow fetches the signed quarantine bundle manifest (oras manifest fetch)
and its config blob (oras blob fetch). This helper then proves, offline and
fail-closed, that the fetched artifact is byte-identical to the locally
regenerated source output, and rewrites the local files that
plugin_verify.py consumes (oci-manifest.json, pushed-manifest.json,
canonical manifest).

The bundle digest is the sha256 of the RAW manifest bytes exactly as ORAS
returned them (not a pretty-printed re-serialization). The signed
backend_image_ref annotation declares the intended RELEASE backend pin, so
it is checked against the expected release backend repository plus the resolved
backend digest. Source annotations and org.opencontainers.image.created are
never rewritten: the raw manifest is stored unchanged.

No network, registry, cosign, Docker or subprocess operation happens here. The
helper does not sign, promote or emit any PUBLISH_ALLOWED-style decision; it
only validates inputs and writes local verification files. Any malformed or
ambiguous input raises ResumeBlocked BEFORE the output directory is created.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import plugin_manifest_builder as builder


class ResumeErrorCode:
    INVALID_DIGEST = "RESUME_INVALID_DIGEST"
    DIGEST_CONFLICT = "RESUME_DIGEST_CONFLICT"
    INVALID_REPOSITORY = "RESUME_INVALID_REPOSITORY"
    REPOSITORY_PAIR_MISMATCH = "RESUME_REPOSITORY_PAIR_MISMATCH"
    INPUT_MISSING = "RESUME_INPUT_MISSING"
    INVALID_MANIFEST = "RESUME_INVALID_MANIFEST"
    BUNDLE_DIGEST_MISMATCH = "RESUME_BUNDLE_DIGEST_MISMATCH"
    CONFIG_DIGEST_MISMATCH = "RESUME_CONFIG_DIGEST_MISMATCH"
    CONFIG_SIZE_MISMATCH = "RESUME_CONFIG_SIZE_MISMATCH"
    CANONICAL_MISMATCH = "RESUME_CANONICAL_MISMATCH"
    BACKEND_REF_MISMATCH = "RESUME_BACKEND_REF_MISMATCH"
    BACKEND_DIGEST_MISMATCH = "RESUME_BACKEND_DIGEST_MISMATCH"
    MANIFEST_DIGEST_MISMATCH = "RESUME_MANIFEST_DIGEST_MISMATCH"


class ResumeBlocked(Exception):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


def _fail(code: str, reason: str) -> None:
    raise ResumeBlocked(code, reason)


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        _fail(ResumeErrorCode.INPUT_MISSING, f"cannot read {label}: {path}")


def _require_digest(value: str, label: str) -> str:
    # Reuse the trusted builder's strict pattern instead of duplicating it.
    if builder._DIGEST_RE.fullmatch(value or "") is None:
        _fail(
            ResumeErrorCode.INVALID_DIGEST,
            f"{label} must be sha256:<64 lowercase hex>, got {value!r}",
        )
    return value


def _require_repository(value: str, label: str) -> str:
    # Location only; identity is enforced later by exact annotation equality.
    if (
        not value
        or value != value.strip()
        or any(char.isspace() for char in value)
        or "://" in value
        or "@" in value
        or value.endswith("/")
    ):
        _fail(
            ResumeErrorCode.INVALID_REPOSITORY,
            f"{label} must be a bare OCI repository without scheme/tag/digest",
        )
    return value


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(ResumeErrorCode.INVALID_MANIFEST, f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def resume_verification_inputs(
    *,
    bundle_manifest_path: Path,
    config_blob_path: Path,
    local_canonical_manifest_path: Path,
    backend_digest: str,
    bundle_digest: str,
    release_backend: str,
    release_bundle: str,
    output_dir: Path,
) -> dict[str, str]:
    """Validate the signed bundle against local source and stage verify inputs.

    Returns the workflow env mapping with exact digests, release refs and the
    staged file paths. Nothing is written unless every check passes.
    """
    _require_digest(backend_digest, "backend_digest")
    _require_digest(bundle_digest, "bundle_digest")
    if backend_digest == bundle_digest:
        _fail(
            ResumeErrorCode.DIGEST_CONFLICT,
            "backend and bundle digests must be distinct",
        )
    _require_repository(release_backend, "release_backend")
    _require_repository(release_bundle, "release_bundle")
    # Mirror plugin_verify.py's backend/bundle repository pair contract
    # (verify.py:551-554) so a mismatched pair fails here, not after staging.
    if not release_backend.endswith("/backend"):
        _fail(
            ResumeErrorCode.REPOSITORY_PAIR_MISMATCH,
            "release_backend repository must end with /backend",
        )
    if release_bundle != release_backend[:-8] + "/bundle":
        _fail(
            ResumeErrorCode.REPOSITORY_PAIR_MISMATCH,
            "release_bundle must be the /bundle sibling of release_backend",
        )

    manifest_bytes = _read_bytes(bundle_manifest_path, "raw bundle manifest")
    actual_bundle_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    if actual_bundle_digest != bundle_digest:
        _fail(
            ResumeErrorCode.BUNDLE_DIGEST_MISMATCH,
            "sha256(raw bundle manifest) differs from the resolved bundle digest",
        )
    try:
        manifest = json.loads(
            manifest_bytes.decode("utf-8"), object_pairs_hook=_no_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(ResumeErrorCode.INVALID_MANIFEST, "bundle manifest is not valid JSON")
    if not isinstance(manifest, dict):
        _fail(ResumeErrorCode.INVALID_MANIFEST, "bundle manifest must be an object")
    if (
        manifest.get("schemaVersion") != builder.SCHEMA_VERSION
        or manifest.get("mediaType") != builder.OCI_MANIFEST_MEDIA_TYPE
    ):
        _fail(ResumeErrorCode.INVALID_MANIFEST, "unexpected OCI manifest schema/mediaType")
    config = manifest.get("config")
    if not isinstance(config, dict):
        _fail(ResumeErrorCode.INVALID_MANIFEST, "missing OCI config descriptor")
    if config.get("mediaType") != builder.PLUGIN_MANIFEST_MEDIA_TYPE:
        _fail(ResumeErrorCode.INVALID_MANIFEST, "unexpected OCI config mediaType")

    config_bytes = _read_bytes(config_blob_path, "config blob")
    config_digest = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
    if config.get("digest") != config_digest:
        _fail(
            ResumeErrorCode.CONFIG_DIGEST_MISMATCH,
            "config descriptor digest differs from the fetched config blob",
        )
    if config.get("size") != len(config_bytes):
        _fail(
            ResumeErrorCode.CONFIG_SIZE_MISMATCH,
            "config descriptor size differs from the fetched config blob",
        )

    local_canonical = _read_bytes(local_canonical_manifest_path, "local canonical manifest")
    if config_bytes != local_canonical:
        _fail(
            ResumeErrorCode.CANONICAL_MISMATCH,
            "signed config blob is not byte-identical to the regenerated canonical manifest",
        )

    annotations = manifest.get("annotations")
    if not isinstance(annotations, dict):
        _fail(ResumeErrorCode.INVALID_MANIFEST, "missing OCI annotations")
    expected_backend_ref = f"{release_backend}@{backend_digest}"
    if annotations.get(builder.A_BACKEND_IMAGE_REF) != expected_backend_ref:
        _fail(
            ResumeErrorCode.BACKEND_REF_MISMATCH,
            "signed backend_image_ref is not the expected release backend pin",
        )
    if annotations.get(builder.A_BACKEND_DIGEST) != backend_digest:
        _fail(
            ResumeErrorCode.BACKEND_DIGEST_MISMATCH,
            "backend_digest annotation differs from the resolved backend digest",
        )
    if annotations.get(builder.A_MANIFEST_DIGEST) != config_digest:
        _fail(
            ResumeErrorCode.MANIFEST_DIGEST_MISMATCH,
            "manifest_digest annotation differs from the config blob digest",
        )

    # Validation complete: only now materialize verification inputs. Raw bytes
    # are stored unchanged, so signatures, source annotations and created keep
    # their original meaning for the subsequent cosign/plugin_verify runs.
    # oci-manifest.json and pushed-manifest.json are intentionally IDENTICAL
    # bytes: the resumed path has no independently rebuilt local expectation.
    # Integrity comes from sha256(raw)==bundle_digest above plus the mandatory
    # subsequent full plugin_verify/cosign run, never from an expected/pushed
    # diff, which is vacuous here by design.
    output_dir.mkdir(parents=True, exist_ok=True)
    oci_manifest_path = output_dir / "oci-manifest.json"
    pushed_manifest_path = output_dir / "pushed-manifest.json"
    canonical_path = output_dir / "resumed-canonical-manifest.json"
    oci_manifest_path.write_bytes(manifest_bytes)
    pushed_manifest_path.write_bytes(manifest_bytes)
    canonical_path.write_bytes(config_bytes)
    return {
        "ARTIFACT_DIGEST": bundle_digest,
        "BACKEND_DIGEST": backend_digest,
        "ARTIFACT_REF": f"{release_bundle}@{bundle_digest}",
        "BACKEND_REF": expected_backend_ref,
        "CANONICAL_MANIFEST": str(canonical_path),
        "OCI_MANIFEST": str(oci_manifest_path),
        "PUSHED_MANIFEST": str(pushed_manifest_path),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    resume = commands.add_parser(
        "resume", help="validate signed bundle and stage verification inputs"
    )
    resume.add_argument("--bundle-manifest", required=True,
                        help="raw bytes from: oras manifest fetch <quarantine bundle>@<digest>")
    resume.add_argument("--config-blob", required=True,
                        help="raw bytes from: oras blob fetch <quarantine bundle>@<config digest>")
    resume.add_argument("--local-canonical-manifest", required=True,
                        help="canonical-manifest.json regenerated locally by prepare")
    resume.add_argument("--backend-digest", required=True)
    resume.add_argument("--bundle-digest", required=True)
    resume.add_argument("--release-backend", required=True,
                        help="expected release backend repository (no tag/digest)")
    resume.add_argument("--release-bundle", required=True,
                        help="release bundle repository used for ARTIFACT_REF (no tag/digest)")
    resume.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = resume_verification_inputs(
            bundle_manifest_path=Path(args.bundle_manifest),
            config_blob_path=Path(args.config_blob),
            local_canonical_manifest_path=Path(args.local_canonical_manifest),
            backend_digest=args.backend_digest,
            bundle_digest=args.bundle_digest,
            release_backend=args.release_backend,
            release_bundle=args.release_bundle,
            output_dir=Path(args.output_dir),
        )
    except ResumeBlocked as exc:
        print(f"::error::{exc.code}: {exc.reason}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
