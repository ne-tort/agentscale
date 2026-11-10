"""Focused stdlib tests for plugin CI; no pytest or external service is needed."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import types
import unittest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github" / "scripts" / "plugin_manifest_builder.py").is_file():
            return parent
    raise RuntimeError("plugin CI repository root not found")


ROOT = _repo_root()
BUILDER_PATH = ROOT / ".github" / "scripts" / "plugin_manifest_builder.py"
VERIFY_PATH = ROOT / ".github" / "scripts" / "plugin_verify.py"
PUBLICATION_PATH = ROOT / ".github" / "scripts" / "plugin_publication.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "plugin-artifacts.yml"


def _load(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load(BUILDER_PATH, "plugin_manifest_builder")
sys.modules["plugin_manifest_builder"] = builder
verifier = _load(VERIFY_PATH, "plugin_verify_supply_chain_test")


def _source_manifest() -> dict:
    return {
        "plugin_id": "hello_plugin",
        "version": "0.1.0-rc.1+build.7",
        "protocol": "plugin-rpc/1",
        "frontend": {"entry": "index.html", "base_path": "/"},
        "backend": {"port": 8080, "health": "/health"},
        "capabilities": ["cache", "objects"],
        "migrations": {"engine": "liquibase", "path": "migrations/changelog.xml"},
        "egress_ref": "grant:hello_plugin",
        "public_pages": [],
        "webhooks": ["/webhook/ping"],
        "min_platform_version": "4.0.0",
    }


def _make_oci(tmp: str, *, use_dist: bool = False) -> dict:
    root = Path(tmp) / "source"
    package = root / "packages" / "plugins" / "hello-plugin"
    frontend = package / "frontend" / "dist" if use_dist else package / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("dist" if use_dist else "static", encoding="utf-8")
    if use_dist:
        (package / "frontend" / "ignored-static.js").write_text("ignored", encoding="utf-8")
    (package / "migrations").mkdir(parents=True)
    (package / "migrations" / "changelog.xml").write_text("<databaseChangeLog/>", encoding="utf-8")
    (package / "backend").mkdir(parents=True)
    (package / "backend" / "Dockerfile").write_text(
        "FROM ghcr.io/ne-tort/base@sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    source = _source_manifest()
    (package / "manifest.json").write_text(json.dumps(source), encoding="utf-8")
    resolved = builder.prepare_package(
        str(root), "packages/plugins/hello-plugin", "", str(Path(tmp) / "prepared")
    )
    backend_digest = "sha256:" + "b" * 64
    backend_ref = "ghcr.io/ne-tort/plugins-quarantine/hello_plugin/backend@" + backend_digest
    release_backend_ref = backend_ref.replace("/plugins-quarantine/", "/plugins/")
    source_sha = "c" * 40
    oci = builder.build_bundle_manifest(
        source,
        release_backend_ref,
        resolved["canonical_manifest"],
        resolved["frontend_archive"],
        resolved["migrations_archive"],
        "ne-tort/agentscale",
        source_sha,
    )
    expected_path = Path(tmp) / "oci.json"
    pushed_path = Path(tmp) / "pushed.json"
    for path in (expected_path, pushed_path):
        path.write_text(json.dumps(oci, sort_keys=True), encoding="utf-8")
    return {
        "root": root,
        "package": package,
        "source": source,
        "resolved": resolved,
        "backend_digest": backend_digest,
        "backend_ref": backend_ref,
        "release_backend_ref": release_backend_ref,
        "source_sha": source_sha,
        "oci": oci,
        "expected_path": expected_path,
        "pushed_path": pushed_path,
        "artifact_digest": "sha256:" + "f" * 64,
        "artifact_ref": "ghcr.io/ne-tort/plugins-quarantine/hello_plugin/bundle@" + "sha256:" + "f" * 64,
        "builder_id": "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "1" * 40,
    }


def _backend_predicates(tmp: str, fixture: dict) -> tuple[dict, dict]:
    evidence = Path(tmp) / "buildx"
    evidence.mkdir(exist_ok=True)
    image_digest = "sha256:" + "d" * 64
    subject = [{"name": "backend-image", "digest": {"sha256": "d" * 64}}]
    sbom_statement = {
        "_type": "https://in-toto.io/Statement/v0.1",
        "predicateType": builder.SPDX_PREDICATE_TYPE,
        "subject": subject,
        "predicate": {
            "spdxVersion": "SPDX-2.3",
            "documentNamespace": "https://example.test/backend-sbom",
            "SPDXID": "SPDXRef-DOCUMENT",
            "packages": [{"SPDXID": "SPDXRef-image", "name": "backend"}],
        },
    }
    provenance_statement = {
        "_type": "https://in-toto.io/Statement/v0.1",
        "predicateType": "https://slsa.dev/provenance/v0.2",
        "subject": subject,
        "predicate": {
            "builder": {"id": "https://github.com/docker/buildkit"},
            "buildType": "https://github.com/docker/buildx/build",
        },
    }
    (evidence / "sbom.json").write_text(json.dumps(sbom_statement), encoding="utf-8")
    (evidence / "provenance.json").write_text(json.dumps(provenance_statement), encoding="utf-8")
    return builder.prepare_backend_evidence(
        str(evidence),
        image_digest,
        "ne-tort/agentscale",
        fixture["source_sha"],
        fixture["builder_id"],
        "run-1",
    )


class CanonicalLayoutTests(unittest.TestCase):
    def test_checked_in_manifest_and_protocol_fixtures_stay_aligned(self) -> None:
        schema = json.loads(
            (ROOT / "packages" / "schemas" / "plugin-manifest" / "v1" / "schema.json").read_text(
                encoding="utf-8"
            )
        )
        required = {
            "plugin_id", "version", "protocol", "frontend", "backend", "capabilities",
            "migrations", "egress_ref", "public_pages", "webhooks", "min_platform_version",
        }
        optional = {"rpc_operations", "mcp_servers", "pods", "secret_delivery"}
        self.assertEqual(set(schema["required"]), required)
        self.assertLessEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(set(schema["properties"]), required | optional)
        self.assertEqual(set(builder.MANIFEST_FIELDS), required)
        self.assertEqual(schema["properties"]["plugin_id"]["pattern"], builder._PLUGIN_ID_RE.pattern)
        for value in ("0.1.0", "1.0.0-rc.1+build.7"):
            self.assertIsNotNone(re.fullmatch(schema["properties"]["version"]["pattern"], value))
            self.assertIsNotNone(builder._SEMVER_RE.fullmatch(value))
        for value in ("01.0.0", "1.0.0-01", "1.0"):
            self.assertIsNone(re.fullmatch(schema["properties"]["version"]["pattern"], value))
            self.assertIsNone(builder._SEMVER_RE.fullmatch(value))

        protocol = json.loads(
            (ROOT / "packages" / "schemas" / "plugin-manifest" / "v1" / "protocol.json").read_text(
                encoding="utf-8"
            )
        )
        expected_messages = {
            "hostReady": "host.ready",
            "pluginReady": "plugin.ready",
            "grantReady": "grant.ready",
            "pluginRpcRequest": "plugin.rpc.request",
            "pluginRpcResponse": "plugin.rpc.response",
            "pluginError": "plugin.error",
        }
        self.assertEqual(
            {entry["$ref"].rsplit("/", 1)[-1] for entry in protocol["oneOf"]},
            set(expected_messages),
        )
        for name, message_type in expected_messages.items():
            self.assertEqual(
                protocol["$defs"][name]["properties"]["type"]["const"],
                message_type,
            )
        builder._validate_protocol_fixture(ROOT)

    def test_protocol_fixture_validator_rejects_inexact_required_fields(self) -> None:
        source = json.loads(
            (ROOT / "packages" / "schemas" / "plugin-manifest" / "v1" / "protocol.json").read_text(
                encoding="utf-8"
            )
        )
        source["$defs"]["grantReady"]["required"].remove("expires_at")
        with tempfile.TemporaryDirectory() as tmp:
            protocol_path = (
                Path(tmp) / "packages" / "schemas" / "plugin-manifest" / "v1" / "protocol.json"
            )
            protocol_path.parent.mkdir(parents=True)
            protocol_path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(
                builder.ManifestError,
                "incomplete protocol fixture grantReady",
            ):
                builder._validate_protocol_fixture(Path(tmp))

    def test_plugin_dir_identity_and_static_frontend_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            resolved = fixture["resolved"]
            self.assertEqual(resolved["plugin_id"], "hello_plugin")
            self.assertEqual(resolved["plugin_dir"], "packages/plugins/hello-plugin")
            self.assertEqual(Path(resolved["dockerfile"]).parts[-2:], ("backend", "Dockerfile"))
            self.assertEqual(resolved["docker_context"], str(fixture["package"]))
            self.assertTrue(Path(resolved["frontend_archive"]).is_file())

    def test_existing_frontend_dist_wins_over_static_frontend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp, use_dist=True)
            with tarfile.open(fixture["resolved"]["frontend_archive"], "r:gz") as archive:
                self.assertEqual(archive.getnames(), ["index.html"])

    def test_full_manifest_is_config_and_identical_blob(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            oci = fixture["oci"]
            expected_digest = "sha256:" + __import__("hashlib").sha256(
                builder.canonical_manifest_bytes(fixture["source"])
            ).hexdigest()
            self.assertEqual(oci["config"]["digest"], expected_digest)
            manifest_layers = [
                layer for layer in oci["layers"]
                if layer["annotations"][builder.A_LAYER_ROLE] == builder.ROLE_MANIFEST
            ]
            self.assertEqual(len(manifest_layers), 1)
            self.assertEqual(manifest_layers[0]["digest"], expected_digest)
            summary = builder.validate_signed_manifest(
                oci,
                backend_digest=fixture["backend_digest"],
                source_manifest=fixture["source"],
                backend_ref=fixture["release_backend_ref"],
                source_repository="ne-tort/agentscale",
                source_sha=fixture["source_sha"],
            )
            self.assertTrue(summary["manifest_content_verified"])

    def test_extension_layer_does_not_trigger_fixed_three_layer_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            oci = copy.deepcopy(fixture["oci"])
            oci["layers"].append(
                {
                    "mediaType": builder.FRONTEND_MEDIA_TYPE,
                    "digest": "sha256:" + "e" * 64,
                    "size": 1,
                    "annotations": {
                        builder.A_LAYER_ROLE: "extension/docs",
                        "org.opencontainers.image.title": "docs.tar.gz",
                    },
                }
            )
            summary = builder.validate_signed_manifest(
                oci,
                backend_digest=fixture["backend_digest"],
                source_manifest=fixture["source"],
                backend_ref=fixture["release_backend_ref"],
                source_repository="ne-tort/agentscale",
                source_sha=fixture["source_sha"],
            )
            self.assertEqual(summary["layer_count"], 4)

    def test_canonical_plugin_id_and_semver_are_fail_closed(self) -> None:
        for plugin_id in ("hello-plugin", "1plugin", "Plugin"):
            source = _source_manifest()
            source["plugin_id"] = plugin_id
            with self.subTest(plugin_id=plugin_id), self.assertRaises(builder.ManifestError):
                builder.validate_source_manifest(source)
        for version in ("1.0.0-01", "1.0.0-alpha.01", "01.0.0", "1.0"):
            source = _source_manifest()
            source["version"] = version
            with self.subTest(version=version), self.assertRaises(builder.ManifestError):
                builder.validate_source_manifest(source)


    def test_mutable_dockerfile_inputs_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            dockerfile = Path(fixture["resolved"]["dockerfile"])
            dockerfile.write_text("FROM python:3.13-slim\n", encoding="utf-8")
            with self.assertRaises(builder.ManifestError):
                builder.prepare_package(
                    str(fixture["root"]),
                    "packages/plugins/hello-plugin",
                    "",
                    str(Path(tmp) / "mutable-output"),
                )
            dockerfile.write_text(
                "FROM ghcr.io/ne-tort/base@sha256:" + "a" * 64 + "\n"
                + "RUN curl -fsSL https://example.test/tool | tar -xz\n",
                encoding="utf-8",
            )
            with self.assertRaises(builder.ManifestError):
                builder.prepare_package(
                    str(fixture["root"]),
                    "packages/plugins/hello-plugin",
                    "",
                    str(Path(tmp) / "download-output"),
                )

    def test_backend_and_bundle_evidence_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            backend_sbom, backend_provenance = _backend_predicates(tmp, fixture)
            bundle_sbom, bundle_provenance = builder.build_bundle_evidence(
                fixture["source"],
                fixture["oci"],
                fixture["artifact_digest"],
                "ne-tort/agentscale",
                fixture["source_sha"],
                fixture["builder_id"],
                "run-1",
            )
            self.assertIn("buildxProvenance", backend_provenance["invocation"]["parameters"])
            self.assertNotIn("buildxProvenance", bundle_provenance["invocation"]["parameters"])
            self.assertNotEqual(backend_provenance["buildType"], bundle_provenance["buildType"])
            self.assertIn("artifact=bundle", bundle_sbom["comment"])
            self.assertNotEqual(backend_sbom, bundle_sbom)


class VerificationTests(unittest.TestCase):
    def _verify(self, fixture: dict, tmp: str, *, release: bool = False, tamper: str = "") -> dict:
        backend_sbom, backend_provenance = _backend_predicates(tmp, fixture)
        bundle_sbom, bundle_provenance = builder.build_bundle_evidence(
            fixture["source"],
            fixture["oci"],
            fixture["artifact_digest"],
            "ne-tort/agentscale",
            fixture["source_sha"],
            fixture["builder_id"],
            "run-1",
        )
        backend_ref = fixture["backend_ref"]
        artifact_ref = fixture["artifact_ref"]
        if release:
            backend_ref = backend_ref.replace("/plugins-quarantine/", "/plugins/")
            artifact_ref = artifact_ref.replace("/plugins-quarantine/", "/plugins/")
        calls: list[list[str]] = []

        def fake_cosign(argv: list[str]) -> dict:
            calls.append(argv)
            if argv[1] == "verify":
                return {"returncode": 0, "stdout": ""}
            self.assertEqual(argv[1], "verify-attestation")
            ref = argv[-1]
            evidence_type = argv[argv.index("--type") + 1]
            is_backend = ref == backend_ref
            if evidence_type == "spdxjson":
                predicate = copy.deepcopy(backend_sbom if is_backend else bundle_sbom)
                predicate_type = builder.SPDX_PREDICATE_TYPE
            else:
                predicate = copy.deepcopy(backend_provenance if is_backend else bundle_provenance)
                predicate_type = "https://slsa.dev/provenance/v0.2"
            if tamper == "builder" and is_backend and evidence_type == "slsaprovenance":
                predicate["builder"]["id"] = "https://github.com/untrusted/workflow@main"
            digest = ref.rsplit("@sha256:", 1)[1]
            if tamper == "subject":
                digest = "0" * 64
            statement = {
                "_type": "https://in-toto.io/Statement/v0.1",
                "predicateType": predicate_type,
                "subject": [{"name": ref, "digest": {"sha256": digest}}],
                "predicate": predicate,
            }
            return {"returncode": 0, "stdout": json.dumps(statement)}

        result = verifier.verify_artifact(
            str(fixture["expected_path"]),
            fixture["backend_digest"],
            cosign_runner=fake_cosign,
            artifact_ref=artifact_ref,
            backend_ref=backend_ref,
            source_manifest_path=fixture["resolved"]["manifest_path"],
            canonical_manifest_path=fixture["resolved"]["canonical_manifest"],
            pushed_manifest_path=str(fixture["pushed_path"]),
            source_repository="ne-tort/agentscale",
            source_sha=fixture["source_sha"],
            builder_identity=fixture["builder_id"],
            platform_version="4.0.0",
            granted_capabilities=fixture["source"]["capabilities"],
            certificate_identity="https://github.com/" + fixture["builder_id"],
        )
        self.assertEqual(len([call for call in calls if call[1] == "verify"]), 2)
        expected_attestations = {"builder": 2, "subject": 1}.get(tamper, 4)
        self.assertEqual(
            len([call for call in calls if call[1] == "verify-attestation"]),
            expected_attestations,
        )
        return result

    def test_quarantine_and_release_refs_pass_with_real_content_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            quarantine = self._verify(fixture, tmp)
            self.assertEqual(quarantine["decision"], verifier.DECISION_PUBLISH, quarantine)
            release = self._verify(fixture, tmp, release=True)
            self.assertEqual(release["decision"], verifier.DECISION_PUBLISH, release)
            self.assertEqual(
                release["manifest_summary"]["backend_image_ref"],
                fixture["release_backend_ref"],
            )

    def test_wrong_builder_or_subject_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            wrong_builder = self._verify(fixture, tmp, tamper="builder")
            self.assertEqual(wrong_builder["decision"], verifier.DECISION_QUARANTINE)
            self.assertEqual(wrong_builder["code"], verifier.VerifyErrorCode.INVALID_EVIDENCE)
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            wrong_subject = self._verify(fixture, tmp, tamper="subject")
            self.assertEqual(wrong_subject["decision"], verifier.DECISION_QUARANTINE)


class WorkflowStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.publication = PUBLICATION_PATH.read_text(encoding="utf-8")
        cls.dry = cls.workflow[cls.workflow.index("  dry-run:") : cls.workflow.index("  release:")]
        cls.release = cls.workflow[cls.workflow.index("  release:") :]

    def test_pr_has_no_writes_or_persistent_credentials(self) -> None:
        self.assertIn("packages: none", self.dry)
        self.assertIn("id-token: none", self.dry)
        for value in ("docker login", "oras push", "--push", "packages: write", "id-token: write"):
            self.assertNotIn(value, self.dry)
        self.assertNotIn("agentscale-ci", self.workflow)
        self.assertNotIn("GHCR_PUSH_TOKEN", self.workflow)

    def test_verify_precedes_release_and_release_is_reverified(self) -> None:
        verify = self.release.index("Verify signatures and all evidence before any release write")
        promote = self.release.index("Promote verified backend and bundle with all referrers by digest")
        recheck = self.release.index("Reverify actual release refs and copied referrers")
        self.assertLess(verify, promote)
        self.assertLess(promote, recheck)
        self.assertIn("oras copy --recursive", self.release)

    def test_oidc_full_sha_and_immutable_repository_constraints(self) -> None:
        for claim in ("repository", "repository_id", "ref", "sha", "event_name", "job_workflow_ref"):
            self.assertIn(f'"{claim}"', self.publication)
        self.assertIn('[[ "$WORKFLOW_SHA" =~ ^[0-9a-f]{40}$ ]]', self.release)
        self.assertIn('SHA_RE = re.compile(r"^[0-9a-f]{40}$")', self.publication)
        self.assertIn("ref_protected", self.publication)
        self.assertIn("agentscale-plugin-ci", self.publication)
        self.assertIn("ReleaseTrust.from_environment", self.release)
        self.assertIn("validate_release_oidc_claims", self.release)

    def test_no_mutable_actions_and_tools_require_checksums(self) -> None:
        self.assertIsNone(re.search(r"^\s*uses:", self.workflow, re.MULTILINE))
        self.assertIsNone(re.search(r"@[vV][0-9]+(?:\.|\b)", self.workflow))
        self.assertIn("COSIGN_LINUX_AMD64_SHA256", self.workflow)
        self.assertIn("ORAS_LINUX_AMD64_SHA256", self.workflow)
        self.assertIn("sha256sum -c", self.workflow)
        self.assertIn("BUILDKIT_IMAGE", self.workflow)
        self.assertIn('--driver-opt "image=$BUILDKIT_IMAGE"', self.workflow)

    def test_manifest_evidence_refs_and_real_platform_register(self) -> None:
        self.assertIn("--annotation-file", self.release)
        self.assertIn("--config", self.release)
        self.assertIn("backend-sbom.json", self.release)
        self.assertIn("bundle-sbom.json", self.release)
        self.assertIn("publish through platform OIDC", self.release)
        self.assertIn('"$CI_DIR/plugin_publication.py" publish', self.release)
        self.assertNotIn("if: ${{ false }}", self.release)
        self.assertNotIn("backend contract exists", self.release)


if __name__ == "__main__":
    unittest.main()
