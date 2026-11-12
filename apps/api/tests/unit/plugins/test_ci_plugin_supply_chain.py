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
        # The builder must accept exactly the schema surface: every optional v1
        # field the platform validator accepts is allowed into the canonical
        # payload, and nothing else is.
        self.assertEqual(set(builder.OPTIONAL_MANIFEST_FIELDS), optional)
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

    def test_dockerfile_tag_plus_digest_pin_is_accepted(self) -> None:
        # Docker requires a tag before the digest in a Dockerfile, so the
        # documented form `python:3.13-slim@sha256:<digest>` must be a valid pin.
        digest = "a" * 64
        for value in (
            f"python:3.13-slim@sha256:{digest}",
            f"ghcr.io/ne-tort/plugins/hello@sha256:{digest}",
            f"registry.example.com:5000/team/app@sha256:{digest}",
            f"registry.example.com:5000/team/app:v1.2.3@sha256:{digest}",
        ):
            self.assertIsNotNone(builder._REF_RE.fullmatch(value), value)
        for value in (
            "python:3.13-slim",
            f"python@sha256:{'A' * 64}",
            "python:3.13-slim@sha256:short",
            f"bad name@sha256:{digest}",
        ):
            self.assertIsNone(builder._REF_RE.fullmatch(value), value)
        # A tag is allowed only because the digest decides identity; :latest
        # stays banned by the self-reference/mutable check.
        with self.assertRaises(builder.ManifestError):
            builder._assert_no_forbidden_keys({"backend": {"image": f"python:latest@sha256:{digest}"}})

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

class DockerfileBaseArgResolution(unittest.TestCase):
    """FROM ${ARG} — штатная форма; отвергать её значит ломать корректный Dockerfile."""

    DIGEST = "python:3.13-slim@sha256:" + "a" * 64

    def test_arg_default_is_resolved_to_the_pinned_reference(self) -> None:
        self.assertEqual(builder._resolve_arg_reference("${BASE}", {"BASE": self.DIGEST}), self.DIGEST)

    def test_arg_without_default_is_left_unresolved_and_thus_rejected(self) -> None:
        # База, задаваемая только при сборке, статически не доказуема.
        self.assertEqual(builder._resolve_arg_reference("${PYTHON_BASE_IMAGE}", {}), "${PYTHON_BASE_IMAGE}")

    def test_literal_and_scratch_are_untouched(self) -> None:
        self.assertEqual(builder._resolve_arg_reference(self.DIGEST, {}), self.DIGEST)
        self.assertEqual(builder._resolve_arg_reference("scratch", {}), "scratch")

    def test_a_tag_only_arg_default_is_still_rejected_by_the_validator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text("ARG BASE=python:3.13-slim\nFROM ${BASE}\n", encoding="utf-8")
            with self.assertRaises(builder.ManifestError):
                builder._validate_dockerfile_bases(dockerfile)

    def test_a_digest_pinned_arg_default_passes_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text(f"ARG BASE={self.DIGEST}\nFROM ${{BASE}}\n", encoding="utf-8")
            builder._validate_dockerfile_bases(dockerfile)


class WorkflowPassesRequiredBuildArgs(unittest.TestCase):
    """Dockerfile объявляет ARG без дефолта — значения обязаны прийти build-arg."""

    def test_every_docker_build_passes_the_declared_args(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        builds = [line for line in workflow.splitlines() if "docker buildx build" in line]
        self.assertGreaterEqual(len(builds), 2, "ожидаются dry-run и release сборки")
        for line in builds:
            self.assertIn('--build-arg "PYTHON_BASE_IMAGE=', line)
            self.assertIn('--build-arg "LIQUIBASE_SHA256=', line)

    def test_the_arg_values_come_from_variables_not_hardcoded(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        # Digest и checksum — входы supply-chain, не константы в workflow.
        self.assertIn("PLUGIN_PYTHON_BASE_IMAGE", workflow)
        self.assertIn("PLUGIN_LIQUIBASE_SHA256", workflow)
        self.assertNotIn("python:3.13-slim@sha256:", workflow)

    def test_prepare_receives_the_base_image_argument(self) -> None:
        # prepare валидирует Dockerfile, а база приходит ARG-ом без дефолта:
        # без этого флага CI падал бы на INVALID_LAYOUT при любом build-arg.
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        prepare_lines = [
            line for line in workflow.splitlines()
            if "plugin_manifest_builder.py\" prepare" in line
        ]
        self.assertEqual(len(prepare_lines), 2, "ожидаются dry-run и release вызовы")
        for line in prepare_lines:
            self.assertIn("--base-image-arg", line)
            self.assertIn("$PYTHON_BASE_IMAGE", line)

    def test_release_job_passes_the_repository_id_the_trust_gate_compares(self) -> None:
        # ReleaseTrust сверяет EXPECTED_REPOSITORY_ID с GITHUB_REPOSITORY_ID;
        # без второго значения release падает до любой записи.
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("GITHUB_REPOSITORY_ID: ${{ github.repository_id }}", workflow)
        self.assertIn("EXPECTED_REPOSITORY_ID:", workflow)

    def test_jobs_run_where_a_docker_daemon_exists(self) -> None:
        # Оба job'а вызывают docker buildx, которому нужен живой daemon.
        # На чистом ubuntu-latest его нет — это проверено падением прогона.
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        runner_lines = [line for line in workflow.splitlines() if line.strip().startswith("runs-on:")]
        self.assertEqual(len(runner_lines), 2)
        for line in runner_lines:
            self.assertIn("[self-hosted, linux, docker]", line)
            self.assertNotIn("ubuntu-latest", line)


class WorkflowProvenanceIdentity(unittest.TestCase):
    def test_backend_build_binds_provenance_to_the_trusted_workflow(self) -> None:
        """BuildKit emits builder.id="" unless builder-id is passed explicitly."""

        import yaml

        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        step = next(
            step
            for step in workflow["jobs"]["release"]["steps"]
            if step.get("name", "").startswith("Build and push backend only")
        )
        run = step["run"]
        self.assertIn(
            '--provenance="mode=max,builder-id=https://github.com/$TRUSTED_BUILDER_ID"',
            run,
        )
        self.assertNotIn("--provenance=mode=max --sbom", run)

class WorkflowBundlePushPaths(unittest.TestCase):
    def test_bundle_file_references_are_relative_to_the_prepare_parent(self) -> None:
        """ORAS rejects absolute file refs; keep path validation enabled instead."""

        import yaml

        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        step = next(
            step
            for step in workflow["jobs"]["release"]["steps"]
            if step.get("name", "").startswith("Build and push canonical OCI bundle")
        )
        run = step["run"]
        self.assertIn('cd "$(dirname "$CANONICAL_MANIFEST")/.."', run)
        for relative in (
            "plugin-prepare/canonical-manifest.json",
            "plugin-prepare/frontend-dist.tar.gz",
            "plugin-prepare/migrations.tar.gz",
        ):
            self.assertIn(relative, run)
        self.assertNotIn("--disable-path-validation", run)

class WorkflowProvenanceIdentity(unittest.TestCase):
    def test_backend_provenance_accepts_both_invocation_id_casings(self) -> None:
        """The real cosign attestation carries buildInvocationID (capital ID)."""

        predicate = {
        "buildType": "https://agentscale.dev/plugin-backend-buildx/v1",
        "builder": {"id": "https://github.com/ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@refs/heads/main"},
        "invocation": {
            "configSource": {
                "uri": "git+https://github.com/ne-tort/agentscale@" + "1" * 40,
                "digest": {"sha1": "1" * 40},
            },
            "parameters": {"buildxProvenance": {"builder": {"id": "x"}, "buildType": "y"}},
        },
        "metadata": {},
    }
        kwargs = dict(
            kind="backend",
            source_repository="ne-tort/agentscale",
            source_sha="1" * 40,
            builder_identity="ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@refs/heads/main",
            manifest_digest="sha256:" + "0" * 64,
        )
        with self.assertRaises(verifier.VerifyError):
            verifier._validate_provenance_predicate(predicate, **kwargs)
        predicate["metadata"]["buildInvocationId"] = "run-1"
        verifier._validate_provenance_predicate(predicate, **kwargs)
        predicate["metadata"] = {"buildInvocationID": "run-1"}
        verifier._validate_provenance_predicate(predicate, **kwargs)

if __name__ == "__main__":
    unittest.main()


def test_release_job_is_unreachable_while_auto_merge_suppresses_push() -> None:
    """Release-путь не исполняется ни разу: merge через GITHUB_TOKEN не даёт push.

    У workflow 39 прогонов `pull_request` и один `workflow_dispatch`, ни одного
    `push`. Причина в `auto-merge.yml`: squash-merge токеном GITHUB_TOKEN не
    порождает событие `push`, поэтому условие release-job
    (`event_name == 'push'` на default branch) за всё время не было истинным.
    Тест фиксирует это как факт: пока условие не изменено, ветка
    quarantine→sign→promote→publication остаётся непроверенной, и зелёный
    прогон PR не должен читаться как проверка публикации.
    """

    import yaml

    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    release = workflow["jobs"]["release"]

    # Release объявлен с environment и правами записи: он способен публиковать.
    assert release["environment"] == "plugin-release"
    assert release["permissions"]["packages"] == "write"
    assert release["permissions"]["id-token"] == "write"

    # Но запускается только на push в default branch (или dispatch/release-call).
    condition = release["if"]
    # push в default branch — один из путей, но не единственный:
    # `workflow_dispatch` тоже приводит к release и уже срабатывал (отменён).
    assert "github.event_name == 'push'" in condition
    assert "github.event_name == 'workflow_dispatch'" in condition
    assert "github.event_name != 'pull_request'" in condition

    # А push-событие в этом репозитории не появляется: merge делает auto-merge
    # токеном, о чём прямо сказано в его шапке.
    auto_merge = (
        ROOT / ".github" / "workflows" / "auto-merge.yml"
    ).read_text(encoding="utf-8")
    assert "GITHUB_TOKEN merge does not trigger push workflows" in auto_merge
