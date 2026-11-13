"""Offline regressions for pre-promotion release evidence (B4).

plugin_release_evidence.py collects the pinned Trivy scan and the exact-digest
CSP probe after quarantine_verify and BEFORE any release GHCR write, without
OIDC, platform POST or registry writes. plugin_publication.py publish
--release-evidence revalidates that snapshot after release verify, never
re-runs the scanner/probe and never trusts caller evidence booleans. These
tests use only stdlib fakes; nothing here executes docker, cosign, git or
network commands. Live behavior is intentionally unverified.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github" / "scripts" / "plugin_publication.py").is_file():
            return parent
    raise RuntimeError("plugin CI repository root not found")


ROOT = _repo_root()


def _load(module_name: str, filename: str):
    script = ROOT / ".github" / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load " + filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


publication = _load("plugin_publication_offline_evidence_test", "plugin_publication.py")
release_evidence = _load(
    "plugin_release_evidence_offline_test", "plugin_release_evidence.py"
)

COMMIT = "c" * 40
BUILDER = "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "a" * 40
BACKEND_DIGEST = "sha256:" + "b" * 64
BUNDLE_DIGEST = "sha256:" + "f" * 64
OTHER_DIGEST = "sha256:" + "0" * 64
QUAR_BACKEND_REF = (
    "ghcr.io/ne-tort/plugins-quarantine/hello_plugin/backend@" + BACKEND_DIGEST
)
QUAR_BUNDLE_REF = (
    "ghcr.io/ne-tort/plugins-quarantine/hello_plugin/bundle@" + BUNDLE_DIGEST
)
RELEASE_BACKEND_REF = "ghcr.io/ne-tort/plugins/hello_plugin/backend@" + BACKEND_DIGEST
RELEASE_BUNDLE_REF = "ghcr.io/ne-tort/plugins/hello_plugin/bundle@" + BUNDLE_DIGEST
SCANNER_IMAGE = "aquasec/trivy:0.58.2@sha256:" + "9" * 64
EXPECTED_RELEASE_CHECKS = (
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
)
CSP = (
    "default-src 'self'; base-uri 'none'; object-src 'none'; "
    "frame-ancestors https://agentscale.example.test"
)
STATIC_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _source() -> dict:
    return {
        "source_id": "psrc_0123456789abcdef",
        "company_id": "company_1",
        "plugin_id": "hello_plugin",
        "repository": "ne-tort/example",
        "repository_id": 123456,
        "ref": "refs/heads/main",
        "trusted_workflow_ref": BUILDER,
        "static_origin": {
            "url_template": (
                "https://plugins.example.test/{plugin_id}/{version}/"
                "{bundle_digest}/index.html"
            ),
            "plugin_shell_origin": "https://agentscale.example.test",
            "security_headers": dict(STATIC_HEADERS),
        },
    }


def _config() -> dict:
    return {
        "schema_version": 1,
        "publication_base_url": "https://platform.example.test/base",
        "scanner_image": SCANNER_IMAGE,
        "sources": [_source()],
    }


def _environment(config: dict | None = None) -> dict[str, str]:
    # Deliberately no ACTIONS_ID_TOKEN_REQUEST_*: evidence collection and any
    # blocked publish path must never request an OIDC token.
    return {
        publication.CONFIG_ENV: json.dumps(config or _config()),
        "GITHUB_REPOSITORY": "ne-tort/example",
        "GITHUB_REPOSITORY_ID": "123456",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": COMMIT,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "DOCKER_CONFIG_PATH": "",
    }


def _decision(*, bundle_ref: str, backend_ref: str, backend_digest: str) -> dict:
    return {
        "decision": "PUBLISH_ALLOWED",
        "publish_allowed": True,
        "code": "OK",
        "artifact_ref": bundle_ref,
        "backend_ref": backend_ref,
        "backend_digest": backend_digest,
        "manifest_summary": {
            "plugin_id": "hello_plugin",
            "version": "1.2.0",
            "manifest_content_verified": True,
            "backend_image_ref": backend_ref,
            "backend_digest": backend_digest,
        },
        "checks": [{"name": name, "ok": True} for name in EXPECTED_RELEASE_CHECKS],
    }


def _scan_report(backend_ref: str, vulnerabilities: list | None = None) -> dict:
    return {
        "SchemaVersion": 2,
        "ArtifactName": backend_ref,
        "ArtifactType": "container_image",
        "Metadata": {"RepoDigests": [backend_ref]},
        "Results": [
            {
                "Target": "backend",
                "Vulnerabilities": vulnerabilities if vulnerabilities is not None else [],
            }
        ],
    }


def _payload_response() -> dict:
    return {
        "status": "PUBLISHED",
        "code": "PUBLISHED",
        "plugin_id": "hello_plugin",
        "version": "1.2.0",
        "bundle_digest": BUNDLE_DIGEST,
        "backend_digest": BACKEND_DIGEST,
        "source_id": "psrc_0123456789abcdef",
    }


def _json_response(status: int, document: dict) -> object:
    return publication.HttpResponse(
        status=status,
        headers={},
        body=json.dumps(document).encode("utf-8"),
    )


def _csp_response(*, digest: str = BUNDLE_DIGEST) -> object:
    headers = dict(STATIC_HEADERS)
    headers["X-AgentScale-Bundle-Digest"] = digest
    return publication.HttpResponse(
        status=200,
        headers={key.lower(): value for key, value in headers.items()},
        body=b"",
    )


class FakeTransport:
    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def send(self, method: str, url: str, **kwargs) -> object:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected network call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class EvidenceFixture:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.manifest = self.root / "oci.json"
        self.quarantine_decision = self.root / "quarantine-decision.json"
        self.release_decision = self.root / "release-decision.json"
        self.scan = self.root / "quarantine-scan.json"
        self.output = self.root / "release-evidence.json"
        self.docker_config = self.root / "docker-config.json"
        self.sbom = self.root / "bundle-sbom.json"
        self.provenance = self.root / "bundle-provenance.json"
        self.commands: list[list[str]] = []
        self.manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "annotations": {
                        "plugin_id": "hello_plugin",
                        "manifest_version": "1.2.0",
                        "backend_digest": BACKEND_DIGEST,
                        "com.agentscale.plugin.backend_image_ref": RELEASE_BACKEND_REF,
                        "com.agentscale.plugin.source_repository": "ne-tort/example",
                        "com.agentscale.plugin.source_sha": COMMIT,
                    },
                }
            ),
            encoding="utf-8",
        )
        self.quarantine_decision.write_text(
            json.dumps(
                _decision(
                    bundle_ref=QUAR_BUNDLE_REF,
                    backend_ref=QUAR_BACKEND_REF,
                    backend_digest=BACKEND_DIGEST,
                )
            ),
            encoding="utf-8",
        )
        self.release_decision.write_text(
            json.dumps(
                _decision(
                    bundle_ref=RELEASE_BUNDLE_REF,
                    backend_ref=RELEASE_BACKEND_REF,
                    backend_digest=BACKEND_DIGEST,
                )
            ),
            encoding="utf-8",
        )
        self.docker_config.write_text("{}", encoding="utf-8")
        self.sbom.write_text('{"spdxVersion":"SPDX-2.3"}', encoding="utf-8")
        self.provenance.write_text('{"buildType":"test"}', encoding="utf-8")

    def env(self, config: dict | None = None) -> dict[str, str]:
        env = _environment(config)
        env["DOCKER_CONFIG_PATH"] = str(self.docker_config)
        return env

    def args(self) -> list[str]:
        return [
            "--plugin-id", "hello_plugin",
            "--plugin-version", "1.2.0",
            "--builder-identity", BUILDER,
            "--quarantine-bundle-ref", QUAR_BUNDLE_REF,
            "--quarantine-bundle-digest", BUNDLE_DIGEST,
            "--quarantine-backend-ref", QUAR_BACKEND_REF,
            "--quarantine-backend-digest", BACKEND_DIGEST,
            "--release-bundle-ref", RELEASE_BUNDLE_REF,
            "--release-bundle-digest", BUNDLE_DIGEST,
            "--release-backend-ref", RELEASE_BACKEND_REF,
            "--release-backend-digest", BACKEND_DIGEST,
            "--manifest", str(self.manifest),
            "--quarantine-decision", str(self.quarantine_decision),
            "--scan-report", str(self.scan),
            "--output", str(self.output),
        ]

    def write_scan_to_staging(self, command: list[str], report: dict) -> None:
        mounts = [value[:-5] for value in command if value.endswith(":/out")]
        if len(mounts) != 1:
            raise AssertionError(f"expected one /out mount, got {mounts}")
        staged = Path(mounts[0]) / self.scan.name
        staged.write_text(json.dumps(report), encoding="utf-8")

    def scan_runner(self, report: dict | None = None):
        def run(command: list[str]) -> None:
            self.commands.append(command)
            self.write_scan_to_staging(command, report or _scan_report(QUAR_BACKEND_REF))

        return run

    def write_evidence(self, **overrides) -> dict:
        self.scan.write_text(json.dumps(_scan_report(QUAR_BACKEND_REF)), encoding="utf-8")
        document = publication.build_release_evidence_document(
            source=publication.SourceBinding.parse(_source()),
            context=publication.GitHubContext.from_environment(
                self.env(), builder_identity=BUILDER
            ),
            plugin_id="hello_plugin",
            version="1.2.0",
            quarantine_bundle_ref=QUAR_BUNDLE_REF,
            quarantine_bundle_digest=BUNDLE_DIGEST,
            quarantine_backend_ref=QUAR_BACKEND_REF,
            quarantine_backend_digest=BACKEND_DIGEST,
            release_bundle_ref=RELEASE_BUNDLE_REF,
            release_bundle_digest=BUNDLE_DIGEST,
            release_backend_ref=RELEASE_BACKEND_REF,
            release_backend_digest=BACKEND_DIGEST,
            scanner_image=SCANNER_IMAGE,
            scan_report_path=str(self.scan),
            scan_report_sha256="sha256:" + hashlib.sha256(self.scan.read_bytes()).hexdigest(),
            csp_url=(
                "https://plugins.example.test/hello_plugin/1.2.0/"
                + BUNDLE_DIGEST
                + "/index.html"
            ),
            csp_status=200,
            csp_headers=_csp_response().headers,
        )
        document.update(overrides)
        self.output.write_text(json.dumps(document), encoding="utf-8")
        return document

    def publish_kwargs(self, env, transport, runner, *, with_evidence: bool = True) -> dict:
        return {
            "environ": env,
            "transport": transport,
            "plugin_id": "hello_plugin",
            "version": "1.2.0",
            "builder_identity": BUILDER,
            "bundle_ref": RELEASE_BUNDLE_REF,
            "bundle_digest": BUNDLE_DIGEST,
            "backend_ref": RELEASE_BACKEND_REF,
            "backend_digest": BACKEND_DIGEST,
            "manifest_path": str(self.manifest),
            "release_decision_path": str(self.release_decision),
            "scan_report_path": str(self.scan),
            "bundle_sbom_path": str(self.sbom),
            "bundle_provenance_path": str(self.provenance),
            "release_evidence_path": str(self.output) if with_evidence else None,
            "scan_runner": runner,
        }


class ScanStagingIsolationTests(unittest.TestCase):
    """Pinned scanner output is isolated from RUNNER_TEMP siblings."""

    def _symlink_or_skip(self, target: Path, link: Path) -> None:
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks are unavailable on this host: {exc}")

    def test_staging_mount_is_isolated_and_report_is_moved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted = root / "runner-temp"
            trusted.mkdir()
            sentinel = trusted / "signed-manifest.json"
            sentinel.write_text("trusted-sibling", encoding="utf-8")
            destination = trusted / "backend-release-scan.json"
            docker_config = root / "docker-config.json"
            docker_config.write_text("{}", encoding="utf-8")
            config = publication.PublicationConfig.from_environment(_environment())
            staging_seen: list[Path] = []

            def runner(command: list[str]) -> None:
                mounts = [value[:-5] for value in command if value.endswith(":/out")]
                self.assertEqual(len(mounts), 1)
                staging = Path(mounts[0]).absolute()
                staging_seen.append(staging)
                self.assertEqual(staging.parent, destination.parent)
                self.assertNotEqual(staging, destination.parent)
                self.assertNotIn(f"{destination.parent}:/out", command)
                self.assertEqual(command[command.index("--output") + 1], f"/out/{destination.name}")
                names = {item.name for item in staging.iterdir()}
                self.assertEqual(names, set(), "trusted sibling must not be exposed by /out")
                (staging / destination.name).write_text(
                    '{"SchemaVersion":2}',
                    encoding="utf-8",
                )
                (staging / "scanner-extra.txt").write_text("extra", encoding="utf-8")

            publication.run_pinned_scan(
                config,
                backend_ref=QUAR_BACKEND_REF,
                report_path=str(destination),
                docker_config_path=str(docker_config),
                runner=runner,
            )
            self.assertEqual(len(staging_seen), 1)
            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                '{"SchemaVersion":2}',
            )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "trusted-sibling")
            self.assertFalse(staging_seen[0].exists())
            self.assertEqual(list(trusted.glob(f".{destination.name}.scan-*")), [])

    def test_malicious_staged_symlink_is_rejected_and_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted = root / "runner-temp"
            trusted.mkdir()
            destination = trusted / "backend-release-scan.json"
            external = root / "outside-scan.json"
            external.write_text("external", encoding="utf-8")
            probe = root / "symlink-probe"
            self._symlink_or_skip(external, probe)
            probe.unlink()
            docker_config = root / "docker-config.json"
            docker_config.write_text("{}", encoding="utf-8")
            config = publication.PublicationConfig.from_environment(_environment())
            staging_seen: list[Path] = []

            def runner(command: list[str]) -> None:
                staging = Path(
                    next(value[:-5] for value in command if value.endswith(":/out"))
                ).absolute()
                staging_seen.append(staging)
                (staging / "scanner-extra.txt").write_text("extra", encoding="utf-8")
                (staging / destination.name).symlink_to(external)

            with self.assertRaises(publication.PublicationBlocked) as denied:
                publication.run_pinned_scan(
                    config,
                    backend_ref=QUAR_BACKEND_REF,
                    report_path=str(destination),
                    docker_config_path=str(docker_config),
                    runner=runner,
                )
            self.assertEqual(denied.exception.code, "SCAN_EVIDENCE_REQUIRED")
            self.assertFalse(destination.exists())
            self.assertFalse(destination.is_symlink())
            self.assertEqual(external.read_text(encoding="utf-8"), "external")
            self.assertFalse(staging_seen[0].exists())
            self.assertEqual(list(trusted.glob(f".{destination.name}.scan-*")), [])

    def test_preseeded_destination_is_rejected_without_scanner_or_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "backend-release-scan.json"
            destination.write_text("preseeded", encoding="utf-8")
            docker_config = root / "docker-config.json"
            docker_config.write_text("{}", encoding="utf-8")
            config = publication.PublicationConfig.from_environment(_environment())

            def runner(command: list[str]) -> None:
                self.fail("scanner must not run for a preseeded destination")

            with self.assertRaises(publication.PublicationBlocked) as denied:
                publication.run_pinned_scan(
                    config,
                    backend_ref=QUAR_BACKEND_REF,
                    report_path=str(destination),
                    docker_config_path=str(docker_config),
                    runner=runner,
                )
            self.assertEqual(denied.exception.code, "SCAN_EVIDENCE_INVALID")
            self.assertEqual(destination.read_text(encoding="utf-8"), "preseeded")
            self.assertEqual(list(root.glob(".backend-release-scan.json.scan-*")), [])


class ReleaseEvidenceCollectionTests(unittest.TestCase):
    def test_collect_writes_bounded_evidence_without_oidc_or_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            transport = FakeTransport([_csp_response()])
            env = fixture.env()
            self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST_URL", env)
            result = release_evidence.main(
                fixture.args(),
                transport=transport,
                scan_runner=fixture.scan_runner(),
                environ=env,
            )
            self.assertEqual(result, 0)
            self.assertEqual([call["method"] for call in transport.calls], ["HEAD"])
            self.assertNotIn("Authorization", transport.calls[0]["headers"])
            self.assertIn(BUNDLE_DIGEST, transport.calls[0]["url"])
            self.assertEqual(len(fixture.commands), 1)
            self.assertEqual(fixture.commands[0][-1], QUAR_BACKEND_REF)
            self.assertIn(SCANNER_IMAGE, fixture.commands[0])
            self.assertIn("--pull=always", fixture.commands[0])
            self.assertNotIn("plugins/hello_plugin", " ".join(fixture.commands[0]))
            document = json.loads(fixture.output.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], 1)
            self.assertEqual(document["quarantine"]["bundle_ref"], QUAR_BUNDLE_REF)
            self.assertEqual(document["quarantine"]["backend_ref"], QUAR_BACKEND_REF)
            self.assertEqual(document["release"]["bundle_ref"], RELEASE_BUNDLE_REF)
            self.assertEqual(document["release"]["backend_ref"], RELEASE_BACKEND_REF)
            self.assertEqual(
                document["scan_report"]["sha256"],
                "sha256:" + hashlib.sha256(fixture.scan.read_bytes()).hexdigest(),
            )
            self.assertEqual(document["scanner_image"], SCANNER_IMAGE)
            self.assertEqual(document["repository"], "ne-tort/example")
            self.assertEqual(document["commit_sha"], COMMIT)
            self.assertEqual(document["builder_identity"], BUILDER)
            self.assertEqual(document["csp"]["status"], 200)
            self.assertEqual(document["csp"]["bundle_digest"], BUNDLE_DIGEST)
            self.assertEqual(
                document["csp"]["headers"]["x-agentscale-bundle-digest"], BUNDLE_DIGEST
            )
            self.assertNotIn("scan_verified", document)
            self.assertNotIn("csp_verified", document)

    def test_collect_requires_all_release_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            decision = _decision(
                bundle_ref=QUAR_BUNDLE_REF,
                backend_ref=QUAR_BACKEND_REF,
                backend_digest=BACKEND_DIGEST,
            )
            decision["checks"] = decision["checks"][:-1]
            fixture.quarantine_decision.write_text(json.dumps(decision), encoding="utf-8")
            transport = FakeTransport([])
            result = release_evidence.main(
                fixture.args(),
                transport=transport,
                scan_runner=lambda command: self.fail("scanner must not run"),
                environ=fixture.env(),
            )
            self.assertEqual(result, 1)
            self.assertEqual(fixture.commands, [])
            self.assertEqual(transport.calls, [])
            self.assertFalse(fixture.output.exists())

    def test_collect_rejects_release_quarantine_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            args = fixture.args()
            index = args.index("--release-bundle-digest")
            args[index + 1] = OTHER_DIGEST
            ref_index = args.index("--release-bundle-ref")
            args[ref_index + 1] = (
                "ghcr.io/ne-tort/plugins/hello_plugin/bundle@" + OTHER_DIGEST
            )
            result = release_evidence.main(
                args,
                transport=FakeTransport([]),
                scan_runner=lambda command: self.fail("scanner must not run"),
                environ=fixture.env(),
            )
            self.assertEqual(result, 1)
            self.assertEqual(fixture.commands, [])
            self.assertFalse(fixture.output.exists())

    def test_collect_rejects_quarantine_ref_in_release_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            args = fixture.args()
            index = args.index("--quarantine-bundle-ref")
            args[index + 1] = RELEASE_BUNDLE_REF
            result = release_evidence.main(
                args,
                transport=FakeTransport([]),
                scan_runner=lambda command: self.fail("scanner must not run"),
                environ=fixture.env(),
            )
            self.assertEqual(result, 1)
            self.assertEqual(fixture.commands, [])
            self.assertFalse(fixture.output.exists())

    def test_collect_scan_findings_are_quarantine_not_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            report = _scan_report(
                QUAR_BACKEND_REF, vulnerabilities=[{"VulnerabilityID": "CVE-1"}]
            )
            transport = FakeTransport([])
            result = release_evidence.main(
                fixture.args(),
                transport=transport,
                scan_runner=fixture.scan_runner(report),
                environ=fixture.env(),
            )
            self.assertEqual(result, 1)
            self.assertEqual(len(fixture.commands), 1)
            self.assertEqual(transport.calls, [])
            self.assertFalse(fixture.output.exists())

    def test_collect_csp_binding_mismatch_blocks_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            transport = FakeTransport([_csp_response(digest=OTHER_DIGEST)])
            result = release_evidence.main(
                fixture.args(),
                transport=transport,
                scan_runner=fixture.scan_runner(),
                environ=fixture.env(),
            )
            self.assertEqual(result, 1)
            self.assertEqual(len(fixture.commands), 1)
            self.assertFalse(fixture.output.exists())

    def test_collect_rejects_source_repository_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            env = fixture.env()
            env["GITHUB_REPOSITORY"] = "ne-tort/other"
            result = release_evidence.main(
                fixture.args(),
                transport=FakeTransport([]),
                scan_runner=lambda command: self.fail("scanner must not run"),
                environ=env,
            )
            self.assertEqual(result, 1)
            self.assertEqual(fixture.commands, [])
            self.assertFalse(fixture.output.exists())


class PublishWithReleaseEvidenceTests(unittest.TestCase):
    def test_publish_with_valid_evidence_skips_scanner_and_csp_and_posts_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            fixture.write_evidence()
            transport = FakeTransport([
                _json_response(200, {"value": "h.p.s"}),
                _json_response(201, _payload_response()),
            ])
            env = fixture.env()
            env["ACTIONS_ID_TOKEN_REQUEST_URL"] = (
                "https://pipelinesghubeus23.actions.githubusercontent.com/"
                "jobs/oidctoken?api-version=2.0"
            )
            env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"] = "github-request-secret"
            outcome = publication.publish(
                **fixture.publish_kwargs(
                    env,
                    transport,
                    lambda command: self.fail("second scanner must not run"),
                )
            )
            self.assertEqual(outcome.status, "PUBLISHED")
            self.assertEqual([call["method"] for call in transport.calls], ["GET", "POST"])
            oidc, post = transport.calls
            self.assertIn("actions.githubusercontent.com", oidc["url"])
            self.assertEqual(
                post["url"],
                "https://platform.example.test/base/internal/plugin-artifacts",
            )
            self.assertEqual(post["headers"]["Authorization"], "Bearer h.p.s")
            payload = json.loads(post["body"])
            self.assertEqual(payload["bundle_digest"], BUNDLE_DIGEST)
            self.assertEqual(payload["backend_digest"], BACKEND_DIGEST)
            self.assertEqual(payload["bundle_ref"], RELEASE_BUNDLE_REF)
            self.assertEqual(payload["backend_ref"], RELEASE_BACKEND_REF)
            self.assertEqual(payload["commit_sha"], COMMIT)
            self.assertIs(payload["evidence"]["scan_verified"], True)
            self.assertIs(payload["evidence"]["csp_verified"], True)
            self.assertEqual(
                payload["evidence"]["sbom_digest"],
                "sha256:" + hashlib.sha256(fixture.sbom.read_bytes()).hexdigest(),
            )

    def test_publish_rejects_tampered_scan_digest_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            document = fixture.write_evidence()
            document["scan_report"]["sha256"] = OTHER_DIGEST
            fixture.output.write_text(json.dumps(document), encoding="utf-8")
            transport = FakeTransport([])
            with self.assertRaises(publication.PublicationBlocked) as denied:
                publication.publish(
                    **fixture.publish_kwargs(
                        fixture.env(),
                        transport,
                        lambda command: self.fail("scanner must not run"),
                    )
                )
            self.assertEqual(denied.exception.code, "RELEASE_EVIDENCE_INVALID")
            self.assertEqual(transport.calls, [])

    def test_publish_rejects_self_asserted_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            document = fixture.write_evidence()
            document["scan_verified"] = True
            fixture.output.write_text(json.dumps(document), encoding="utf-8")
            transport = FakeTransport([])
            with self.assertRaises(publication.PublicationBlocked) as denied:
                publication.publish(
                    **fixture.publish_kwargs(
                        fixture.env(),
                        transport,
                        lambda command: self.fail("scanner must not run"),
                    )
                )
            self.assertEqual(denied.exception.code, "RELEASE_EVIDENCE_UNTRUSTED")
            self.assertEqual(transport.calls, [])

    def test_publish_rejects_wrong_release_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            document = fixture.write_evidence()
            document["release"]["backend_ref"] = (
                "ghcr.io/ne-tort/plugins/hello_plugin/backend@" + OTHER_DIGEST
            )
            document["release"]["backend_digest"] = OTHER_DIGEST
            fixture.output.write_text(json.dumps(document), encoding="utf-8")
            transport = FakeTransport([])
            with self.assertRaises(publication.PublicationBlocked) as denied:
                publication.publish(
                    **fixture.publish_kwargs(
                        fixture.env(),
                        transport,
                        lambda command: self.fail("scanner must not run"),
                    )
                )
            self.assertEqual(denied.exception.code, "RELEASE_EVIDENCE_INVALID")
            self.assertEqual(transport.calls, [])

    def test_publish_rejects_tampered_csp_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            document = fixture.write_evidence()
            document["csp"]["headers"]["x-content-type-options"] = "sniff"
            fixture.output.write_text(json.dumps(document), encoding="utf-8")
            transport = FakeTransport([])
            with self.assertRaises(publication.PublicationBlocked) as denied:
                publication.publish(
                    **fixture.publish_kwargs(
                        fixture.env(),
                        transport,
                        lambda command: self.fail("scanner must not run"),
                    )
                )
            self.assertEqual(denied.exception.code, "CSP_SECURITY_HEADERS_FAILED")
            self.assertEqual(transport.calls, [])

    def test_publish_rejects_wrong_scanner_image_in_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            document = fixture.write_evidence()
            document["scanner_image"] = "aquasec/trivy:0.58.2@sha256:" + "1" * 64
            fixture.output.write_text(json.dumps(document), encoding="utf-8")
            transport = FakeTransport([])
            with self.assertRaises(publication.PublicationBlocked) as denied:
                publication.publish(
                    **fixture.publish_kwargs(
                        fixture.env(),
                        transport,
                        lambda command: self.fail("scanner must not run"),
                    )
                )
            self.assertEqual(denied.exception.code, "RELEASE_EVIDENCE_INVALID")
            self.assertEqual(transport.calls, [])

    def test_publish_rejects_missing_evidence_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            fixture.scan.write_text(
                json.dumps(_scan_report(QUAR_BACKEND_REF)), encoding="utf-8"
            )
            transport = FakeTransport([])
            with self.assertRaises(publication.PublicationBlocked) as denied:
                publication.publish(
                    **fixture.publish_kwargs(
                        fixture.env(),
                        transport,
                        lambda command: self.fail("scanner must not run"),
                    )
                )
            self.assertEqual(denied.exception.code, "RELEASE_EVIDENCE_REQUIRED")
            self.assertEqual(transport.calls, [])

    def test_legacy_publish_without_evidence_keeps_scanner_and_csp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = EvidenceFixture(tmp)
            transport = FakeTransport([
                _csp_response(),
                _json_response(200, {"value": "h.p.s"}),
                _json_response(201, _payload_response()),
            ])

            def legacy_runner(command: list[str]) -> None:
                fixture.write_scan_to_staging(command, _scan_report(RELEASE_BACKEND_REF))

            env = fixture.env()
            env["ACTIONS_ID_TOKEN_REQUEST_URL"] = (
                "https://pipelinesghubeus23.actions.githubusercontent.com/"
                "jobs/oidctoken?api-version=2.0"
            )
            env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"] = "github-request-secret"
            outcome = publication.publish(
                **fixture.publish_kwargs(
                    env, transport, legacy_runner, with_evidence=False
                )
            )
            self.assertEqual(outcome.status, "PUBLISHED")
            self.assertEqual(
                [call["method"] for call in transport.calls], ["HEAD", "GET", "POST"]
            )


class WorkflowReleaseEvidenceStaticTests(unittest.TestCase):
    """Static workflow guards for the pre-promotion release evidence step.

    These guards pin the TARGET workflow contract of plugin-artifacts.yml:
    plugin_release_evidence.py runs after id: quarantine_verify and BEFORE
    id: promote (the first release-namespace registry write), and publish
    consumes the exact evidence/scan files the step produced instead of
    re-running the scanner or the CSP probe. File names are extracted from
    the workflow itself, never hardcoded here.
    """

    @classmethod
    def setUpClass(cls) -> None:
        workflow = (ROOT / ".github" / "workflows" / "plugin-artifacts.yml").read_text(
            encoding="utf-8"
        )
        cls.release = workflow[workflow.index("  release:") :]
        cls.evidence_source = (
            ROOT / ".github" / "scripts" / "plugin_release_evidence.py"
        ).read_text(encoding="utf-8")
        cls.publication_source = (
            ROOT / ".github" / "scripts" / "plugin_publication.py"
        ).read_text(encoding="utf-8")

    @staticmethod
    def _step(text: str, needle: str) -> str:
        """Return the YAML step block (6-space dash indent) containing needle."""
        index = text.index(needle)
        starts = [m.start() for m in re.finditer(r"(?m)^      - ", text)]
        previous = max(start for start in starts if start <= index)
        following = min((start for start in starts if start > index), default=len(text))
        return text[previous:following]

    def _evidence_step(self) -> str:
        return self._step(self.release, 'python3 "$CI_DIR/plugin_release_evidence.py"')

    def _publish_step(self) -> str:
        return self._step(self.release, '"$CI_DIR/plugin_publication.py" publish')

    def test_release_evidence_step_is_after_quarantine_verify_and_before_promote(self) -> None:
        quarantine_verify = self.release.index("id: quarantine_verify")
        evidence = self.release.index('python3 "$CI_DIR/plugin_release_evidence.py"')
        promote = self.release.index("id: promote")
        self.assertLess(quarantine_verify, evidence)
        self.assertLess(evidence, promote)
        # The evidence step consumes the decision written by quarantine_verify.
        self.assertIn("--quarantine-decision", self._evidence_step())
        self.assertIn("quarantine-decision.json", self._evidence_step())

    def test_release_evidence_step_uses_quarantine_refs_and_release_repos_by_digest(self) -> None:
        step = self._evidence_step()
        self.assertIn('"$CI_DIR/plugin_release_evidence.py"', step)
        # Quarantine refs/digests are the actual verified artifacts.
        self.assertIn("--quarantine-bundle-ref", step)
        self.assertIn("--quarantine-backend-ref", step)
        self.assertTrue(
            "$ARTIFACT_REF" in step or "$QUAR_BUNDLE" in step,
            "quarantine bundle ref must come from the verified quarantine variables",
        )
        self.assertTrue(
            "$BACKEND_REF" in step or "$QUAR_BACKEND" in step,
            "quarantine backend ref must come from the verified quarantine variables",
        )
        self.assertIn('--quarantine-bundle-digest "$ARTIFACT_DIGEST"', step)
        self.assertIn('--quarantine-backend-digest "$BACKEND_DIGEST"', step)
        # Release repos are the digest-pinned promotion targets, before any
        # promote-time RELEASE_*_REF environment exists.
        self.assertIn("--release-bundle-ref", step)
        self.assertIn("--release-backend-ref", step)
        self.assertIn('"$RELEASE_BUNDLE@$ARTIFACT_DIGEST"', step)
        self.assertIn('"$RELEASE_BACKEND@$BACKEND_DIGEST"', step)
        self.assertIn('--release-bundle-digest "$ARTIFACT_DIGEST"', step)
        self.assertIn('--release-backend-digest "$BACKEND_DIGEST"', step)
        self.assertIn("--manifest", step)
        self.assertIn("--scan-report", step)
        self.assertIn("--output", step)

    def test_release_evidence_step_has_no_registry_write_oidc_or_platform_post(self) -> None:
        step = self._evidence_step()
        for forbidden in (
            "oras push",
            "oras copy",
            "cosign sign",
            "cosign attest",
            "cosign copy",
            "docker buildx",
            "docker push",
            "--push",
            "ACTIONS_ID_TOKEN",
            '"$CI_DIR/plugin_publication.py" publish',
        ):
            self.assertNotIn(forbidden, step, forbidden)

    def test_publish_reuses_exact_evidence_and_scan_files_without_rescan(self) -> None:
        evidence_step = self._evidence_step()
        publish_step = self._publish_step()
        evidence_output = re.search(r'--output "([^"]+)"', evidence_step)
        scan_report = re.search(r'--scan-report "([^"]+)"', evidence_step)
        self.assertIsNotNone(evidence_output, "evidence step must declare --output")
        self.assertIsNotNone(scan_report, "evidence step must declare --scan-report")
        # Final publish CLI (plugin_publication.py:1514-1519): --release-evidence
        # revalidates the recorded facts; the same scan file is re-read, never
        # regenerated, and no second scanner/CSP probe runs in this step.
        self.assertIn(f'--release-evidence "{evidence_output.group(1)}"', publish_step)
        self.assertIn(f'--scan-report "{scan_report.group(1)}"', publish_step)
        self.assertIn('--release-decision "$RUNNER_TEMP/release-decision.json"', publish_step)
        for forbidden in ("trivy", "docker run", "curl ", "run_pinned_scan"):
            self.assertNotIn(forbidden, publish_step, forbidden)

    def test_no_release_registry_write_precedes_the_evidence_step(self) -> None:
        evidence = self.release.index('python3 "$CI_DIR/plugin_release_evidence.py"')
        # oras push of the QUARANTINE bundle is allowed earlier; every release
        # copy (the actual release-namespace writes) must follow the evidence.
        for token in ("oras copy", "cosign copy"):
            for match in re.finditer(re.escape(token), self.release):
                self.assertGreater(
                    match.start(),
                    evidence,
                    f"{token} at offset {match.start()} precedes release evidence",
                )

    def test_release_verify_still_precedes_publish_and_promotion_is_unchanged(self) -> None:
        release_verify = self.release.index("id: release_verify")
        publish = self.release.index('"$CI_DIR/plugin_publication.py" publish')
        self.assertLess(release_verify, publish)
        promote = self._step(self.release, "id: promote")
        self.assertIn('oras copy --recursive "$QUAR_BACKEND@$BACKEND_DIGEST"', promote)
        self.assertIn('oras copy --recursive "$QUAR_BUNDLE@$ARTIFACT_DIGEST"', promote)
        self.assertIn('cosign copy --only=sig,att "$QUAR_BACKEND@$BACKEND_DIGEST"', promote)
        self.assertIn('cosign copy --only=sig,att "$QUAR_BUNDLE@$ARTIFACT_DIGEST"', promote)

    def test_self_asserted_evidence_booleans_are_never_accepted(self) -> None:
        # The collector never writes caller-style booleans into the evidence
        # document, and publish revalidation keeps the typed rejection.
        self.assertNotIn('"scan_verified"', self.evidence_source)
        self.assertNotIn('"csp_verified"', self.evidence_source)
        self.assertIn(
            '_reject_self_asserted_evidence(document, code="RELEASE_EVIDENCE_UNTRUSTED")',
            self.publication_source,
        )
        for step in (self._evidence_step(), self._publish_step()):
            self.assertNotIn("--scan-verified", step)
            self.assertNotIn("--csp-verified", step)


if __name__ == "__main__":
    unittest.main()
