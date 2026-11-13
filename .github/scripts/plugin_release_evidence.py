#!/usr/bin/env python3
"""Fail-closed pre-promotion release evidence collection for plugin CI.

Runs AFTER quarantine verification (all REQUIRED checks of the quarantine
decision) and BEFORE any release GHCR write/promotion. The pinned Trivy scan
targets the QUARANTINE backend digest; the static-origin CSP probe targets the
exact bundle digest. The bounded JSON evidence records the exact
quarantine+release ref/digest pairs, scanner identity, scan report digest and
the validated CSP response facts. This command requests no OIDC token, performs
no platform POST, no registry write and no promotion. plugin_publication.py
publish --release-evidence revalidates this snapshot after release verify.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def _load_publication():
    script = Path(__file__).with_name("plugin_publication.py")
    spec = importlib.util.spec_from_file_location("trusted_plugin_publication", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load plugin_publication.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--plugin-version", required=True)
    parser.add_argument("--builder-identity", required=True)
    parser.add_argument("--quarantine-bundle-ref", required=True)
    parser.add_argument("--quarantine-bundle-digest", required=True)
    parser.add_argument("--quarantine-backend-ref", required=True)
    parser.add_argument("--quarantine-backend-digest", required=True)
    parser.add_argument("--release-bundle-ref", required=True)
    parser.add_argument("--release-bundle-digest", required=True)
    parser.add_argument("--release-backend-ref", required=True)
    parser.add_argument("--release-backend-digest", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--quarantine-decision", required=True)
    parser.add_argument("--scan-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", default="")
    return parser


def _append_summary(path: str, *, bundle_ref: str, backend_ref: str) -> None:
    if not path:
        return
    lines = [
        "### Plugin release evidence (pre-promotion)",
        "Pinned Trivy scan of the quarantine backend and the exact-digest CSP probe passed.",
        "No OIDC token, no platform POST, no registry write and no promotion were performed.",
        f"Release backend (digest unchanged): {backend_ref}",
        f"Release bundle (digest unchanged): {bundle_ref}",
    ]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: Any = None,
    scan_runner: Any = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    publication = _load_publication()
    if transport is None:
        transport = publication.UrllibTransport()
    if scan_runner is None:
        scan_runner = publication._default_scan_runner
    if environ is None:
        environ = os.environ
    try:
        config = publication.PublicationConfig.from_environment(environ)
        context = publication.GitHubContext.from_environment(
            environ, builder_identity=args.builder_identity
        )
        source = publication.bind_source(config, context, plugin_id=args.plugin_id)
        publication._validate_quarantine_refs(
            bundle_ref=args.quarantine_bundle_ref,
            bundle_digest=args.quarantine_bundle_digest,
            backend_ref=args.quarantine_backend_ref,
            backend_digest=args.quarantine_backend_digest,
        )
        publication._validate_release_refs(
            bundle_ref=args.release_bundle_ref,
            bundle_digest=args.release_bundle_digest,
            backend_ref=args.release_backend_ref,
            backend_digest=args.release_backend_digest,
        )
        if (
            args.quarantine_bundle_digest != args.release_bundle_digest
            or args.quarantine_backend_digest != args.release_backend_digest
        ):
            # Promotion is a repository copy: the immutable digests must match.
            raise publication.PublicationBlocked("RELEASE_REFS_INVALID")
        decision = publication._read_json(
            args.quarantine_decision,
            maximum=publication.MAX_CONFIG_BYTES,
            code="RELEASE_VERIFICATION_REQUIRED",
        )
        # Decision top-level fields use quarantine refs, but manifest_summary
        # stores the RELEASE backend ref (from signed annotations). Validate
        # each against its correct namespace instead of conflating them.
        if not isinstance(decision, dict) or (
            decision.get("decision") != "PUBLISH_ALLOWED"
            or decision.get("publish_allowed") is not True
            or decision.get("code") != "OK"
            or decision.get("artifact_ref") != args.quarantine_bundle_ref
            or decision.get("backend_ref") != args.quarantine_backend_ref
            or decision.get("backend_digest") != args.quarantine_backend_digest
        ):
            raise publication.PublicationBlocked("RELEASE_VERIFICATION_REQUIRED")
        summary = decision.get("manifest_summary")
        if not isinstance(summary, dict) or (
            summary.get("plugin_id") != args.plugin_id
            or summary.get("version") != args.plugin_version
            or summary.get("manifest_content_verified") is not True
            or summary.get("backend_image_ref") != args.release_backend_ref
            or summary.get("backend_digest") != args.release_backend_digest
        ):
            raise publication.PublicationBlocked("RELEASE_VERIFICATION_REQUIRED")
        checks = decision.get("checks")
        if not isinstance(checks, list):
            raise publication.PublicationBlocked("RELEASE_VERIFICATION_REQUIRED")
        observed: set[str] = set()
        for item in checks:
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or item.get("ok") is not True:
                raise publication.PublicationBlocked("RELEASE_VERIFICATION_REQUIRED")
            observed.add(name)
        if not publication.REQUIRED_RELEASE_CHECKS <= observed:
            raise publication.PublicationBlocked("RELEASE_VERIFICATION_REQUIRED")
        publication._validate_manifest_binding(
            publication._read_json(
                args.manifest,
                maximum=publication.MAX_CONFIG_BYTES,
                code="PUBLICATION_MANIFEST_INVALID",
            ),
            source=source,
            context=context,
            version=args.plugin_version,
            backend_ref=args.release_backend_ref,
            backend_digest=args.release_backend_digest,
        )
        publication.run_pinned_scan(
            config,
            backend_ref=args.quarantine_backend_ref,
            report_path=args.scan_report,
            docker_config_path=environ.get("DOCKER_CONFIG_PATH", ""),
            runner=scan_runner,
        )
        scan_report_bytes = publication._read_bounded(
            args.scan_report,
            maximum=publication.MAX_SCAN_REPORT_BYTES,
            code="SCAN_EVIDENCE_REQUIRED",
        )
        publication.validate_scan_report(
            publication._json_loads(scan_report_bytes, code="SCAN_EVIDENCE_REQUIRED"),
            backend_ref=args.quarantine_backend_ref,
            scanner_image=config.scanner_image,
        )
        if source.static_origin is None:
            raise publication.PublicationBlocked("CSP_EVIDENCE_REQUIRED")
        csp_url = source.static_origin.url_for(
            plugin_id=args.plugin_id,
            version=args.plugin_version,
            bundle_digest=args.quarantine_bundle_digest,
        )
        csp_response = publication._send(
            transport,
            "HEAD",
            csp_url,
            headers={"Accept": "text/html", "Cache-Control": "no-cache"},
            unavailable_code="CSP_ORIGIN_UNAVAILABLE",
        )
        publication.validate_csp_response(
            csp_response, source=source, bundle_digest=args.quarantine_bundle_digest
        )
        document = publication.build_release_evidence_document(
            source=source,
            context=context,
            plugin_id=args.plugin_id,
            version=args.plugin_version,
            quarantine_bundle_ref=args.quarantine_bundle_ref,
            quarantine_bundle_digest=args.quarantine_bundle_digest,
            quarantine_backend_ref=args.quarantine_backend_ref,
            quarantine_backend_digest=args.quarantine_backend_digest,
            release_bundle_ref=args.release_bundle_ref,
            release_bundle_digest=args.release_bundle_digest,
            release_backend_ref=args.release_backend_ref,
            release_backend_digest=args.release_backend_digest,
            scanner_image=config.scanner_image,
            scan_report_path=args.scan_report,
            scan_report_sha256="sha256:" + hashlib.sha256(scan_report_bytes).hexdigest(),
            csp_url=csp_url,
            csp_status=csp_response.status,
            csp_headers=csp_response.headers,
        )
        payload = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(payload) > publication.RELEASE_EVIDENCE_MAX_BYTES:
            raise publication.PublicationBlocked("EVIDENCE_FILE_INVALID")
        destination = Path(args.output)
        if destination.is_symlink() or destination.exists():
            raise publication.PublicationBlocked("EVIDENCE_FILE_INVALID")
        try:
            with open(destination, "x", encoding="utf-8") as handle:
                handle.write(payload.decode("utf-8") + "\n")
        except OSError as exc:
            raise publication.PublicationBlocked("EVIDENCE_FILE_INVALID") from exc
    except publication.PublicationBlocked as exc:
        print(f"::error::{exc.code}: release evidence gate failed", file=sys.stderr)
        return 1
    except Exception:
        print("::error::RELEASE_EVIDENCE_INVALID: release evidence gate failed", file=sys.stderr)
        return 1
    _append_summary(
        args.summary,
        bundle_ref=args.release_bundle_ref,
        backend_ref=args.release_backend_ref,
    )
    print("Release evidence collected before any release write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
