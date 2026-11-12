"""Focused stdlib tests for plugin CI; no pytest or external service is needed."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path


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
            "spdxVersion": "SPDX-2.2",
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
    def _verify(
        self,
        fixture: dict,
        tmp: str,
        *,
        release: bool = False,
        tamper: str = "",
        artifact_ref: str | None = None,
        backend_ref: str | None = None,
        backend_digest: str | None = None,
        platform_version: str = "4.0.0",
        granted_capabilities: list[str] | tuple[str, ...] | None = None,
        certificate_identity: str | None = None,
        certificate_oidc_issuer: str | None = None,
        bundle_sbom_output: str | None = None,
        bundle_provenance_output: str | None = None,
        evidence_sink: dict | None = None,
        expected_verify: int | None = None,
        expected_attestations: int | None = None,
    ) -> dict:
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
        if evidence_sink is not None:
            evidence_sink.update(sbom=bundle_sbom, provenance=bundle_provenance)
        backend_ref = fixture["backend_ref"] if backend_ref is None else backend_ref
        artifact_ref = fixture["artifact_ref"] if artifact_ref is None else artifact_ref
        if release:
            backend_ref = backend_ref.replace("/plugins-quarantine/", "/plugins/")
            artifact_ref = artifact_ref.replace("/plugins-quarantine/", "/plugins/")
        if granted_capabilities is None:
            granted_capabilities = fixture["source"]["capabilities"]
        if certificate_identity is None:
            certificate_identity = "https://github.com/" + fixture["builder_id"]
        calls: list[list[str]] = []
        self.cosign_calls = calls

        def fake_cosign(argv: list[str]) -> dict:
            calls.append(argv)
            if argv[1] == "verify":
                if tamper == "signature":
                    return {"returncode": 1, "stderr": "invalid signature"}
                return {"returncode": 0, "stdout": ""}
            self.assertEqual(argv[1], "verify-attestation")
            if tamper == "empty_attestation":
                return {"returncode": 0, "stdout": ""}
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
            if tamper == "provenance_uri" and is_backend and evidence_type == "slsaprovenance":
                predicate["invocation"]["configSource"]["uri"] = (
                    "git+https://github.com/example/untrusted@" + fixture["source_sha"]
                )
            if tamper == "provenance_sha1" and is_backend and evidence_type == "slsaprovenance":
                predicate["invocation"]["configSource"]["digest"]["sha1"] = "0" * 40
            if tamper == "bundle_sbom_checksums" and not is_backend and evidence_type == "spdxjson":
                bundle_package = next(
                    package for package in predicate["packages"]
                    if package["SPDXID"] == "SPDXRef-bundle"
                )
                bundle_package.pop("checksums")
            if (
                tamper == "bundle_provenance_build_type"
                and not is_backend
                and evidence_type == "slsaprovenance"
            ):
                predicate["buildType"] = "https://agentscale.dev/plugin-backend-buildx/v1"
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
            fixture["backend_digest"] if backend_digest is None else backend_digest,
            cosign_runner=fake_cosign,
            artifact_ref=artifact_ref,
            backend_ref=backend_ref,
            source_manifest_path=fixture["resolved"]["manifest_path"],
            canonical_manifest_path=fixture["resolved"]["canonical_manifest"],
            pushed_manifest_path=str(fixture["pushed_path"]),
            source_repository="ne-tort/agentscale",
            source_sha=fixture["source_sha"],
            builder_identity=fixture["builder_id"],
            platform_version=platform_version,
            granted_capabilities=granted_capabilities,
            certificate_identity=certificate_identity,
            certificate_oidc_issuer=certificate_oidc_issuer,
            bundle_sbom_output=bundle_sbom_output,
            bundle_provenance_output=bundle_provenance_output,
        )
        if expected_verify is None:
            expected_verify = 1 if tamper == "signature" else 2
        self.assertEqual(
            len([call for call in calls if call[1] == "verify"]),
            expected_verify,
        )
        if expected_attestations is None:
            expected_attestations = {
                "builder": 2,
                "subject": 1,
                "signature": 0,
                "empty_attestation": 1,
                "provenance_uri": 2,
                "provenance_sha1": 2,
                "bundle_sbom_checksums": 3,
            }.get(tamper, 4)
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

    def test_validated_bundle_evidence_exports_exact_builder_bytes(self) -> None:
        for preexisting in (False, True):
            with self.subTest(preexisting=preexisting):
                with tempfile.TemporaryDirectory() as tmp:
                    fixture = _make_oci(tmp)
                    sbom_output = Path(tmp) / "exported-bundle-sbom.json"
                    provenance_output = Path(tmp) / "exported-bundle-provenance.json"
                    if preexisting:
                        sbom_output.write_bytes(b"stale\n")
                        provenance_output.write_bytes(b"stale\n")
                    else:
                        self.assertFalse(sbom_output.exists())
                        self.assertFalse(provenance_output.exists())
                    evidence: dict = {}
                    result = self._verify(
                        fixture,
                        tmp,
                        bundle_sbom_output=str(sbom_output),
                        bundle_provenance_output=str(provenance_output),
                        evidence_sink=evidence,
                    )
                    self.assertEqual(result["decision"], verifier.DECISION_PUBLISH)
                    expected_sbom = Path(tmp) / "expected-sbom.json"
                    expected_provenance = Path(tmp) / "expected-provenance.json"
                    builder._write_json(str(expected_sbom), evidence["sbom"])
                    builder._write_json(str(expected_provenance), evidence["provenance"])
                    self.assertEqual(sbom_output.read_bytes(), expected_sbom.read_bytes())
                    self.assertEqual(
                        provenance_output.read_bytes(),
                        expected_provenance.read_bytes(),
                    )
                    self.assertEqual(
                        hashlib.sha256(sbom_output.read_bytes()).hexdigest(),
                        hashlib.sha256(expected_sbom.read_bytes()).hexdigest(),
                    )
                    self.assertEqual(
                        hashlib.sha256(provenance_output.read_bytes()).hexdigest(),
                        hashlib.sha256(expected_provenance.read_bytes()).hexdigest(),
                    )

    def test_tampered_evidence_or_quarantine_exports_nothing(self) -> None:
        cases = (
            {"tamper": "builder"},
            {"platform_version": ""},
        )
        for overrides in cases:
            with self.subTest(**overrides):
                with tempfile.TemporaryDirectory() as tmp:
                    fixture = _make_oci(tmp)
                    sbom_output = Path(tmp) / "must-not-exist-sbom.json"
                    provenance_output = Path(tmp) / "must-not-exist-provenance.json"
                    result = self._verify(
                        fixture,
                        tmp,
                        bundle_sbom_output=str(sbom_output),
                        bundle_provenance_output=str(provenance_output),
                        **overrides,
                    )
                    self.assertEqual(result["decision"], verifier.DECISION_QUARANTINE)
                    self.assertFalse(sbom_output.exists())
                    self.assertFalse(provenance_output.exists())

    def test_wrong_bundle_predicate_is_not_exported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            sbom_output = Path(tmp) / "wrong-predicate-sbom.json"
            provenance_output = Path(tmp) / "wrong-predicate-provenance.json"
            result = self._verify(
                fixture,
                tmp,
                tamper="bundle_provenance_build_type",
                bundle_sbom_output=str(sbom_output),
                bundle_provenance_output=str(provenance_output),
            )
            self._assert_quarantine(result, verifier.VerifyErrorCode.INVALID_EVIDENCE)
            self.assertFalse(sbom_output.exists())
            self.assertFalse(provenance_output.exists())

    def _symlink_or_skip(self, target: Path, link: Path) -> None:
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks are unavailable on this host: {exc}")

    def test_symlinked_bundle_output_fails_closed_without_cosign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            target = Path(tmp) / "export-target.json"
            target.write_bytes(b"sentinel\n")
            sbom_output = Path(tmp) / "symlinked-sbom.json"
            provenance_output = Path(tmp) / "must-not-exist-provenance.json"
            self._symlink_or_skip(target, sbom_output)
            result = self._verify(
                fixture,
                tmp,
                bundle_sbom_output=str(sbom_output),
                bundle_provenance_output=str(provenance_output),
                expected_verify=0,
                expected_attestations=0,
            )
            self._assert_quarantine(result, verifier.VerifyErrorCode.MISSING_INPUT)
            self.assertTrue(sbom_output.is_symlink())
            self.assertEqual(target.read_bytes(), b"sentinel\n")
            self.assertFalse(provenance_output.exists())

    def _assert_quarantine(self, result: dict, code: str) -> None:
        self.assertEqual(result["decision"], verifier.DECISION_QUARANTINE, result)
        self.assertEqual(result["code"], code, result)

    def test_pushed_layer_digest_diff_is_invalid_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            pushed = copy.deepcopy(fixture["oci"])
            pushed["layers"][0]["digest"] = "sha256:" + "0" * 64
            fixture["pushed_path"].write_text(
                json.dumps(pushed, sort_keys=True),
                encoding="utf-8",
            )
            result = self._verify(fixture, tmp, expected_attestations=0)
            self._assert_quarantine(result, verifier.VerifyErrorCode.INVALID_MANIFEST)

    def test_canonical_manifest_bytes_mismatch_is_invalid_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            Path(fixture["resolved"]["canonical_manifest"]).write_bytes(b"{}\n")
            result = self._verify(fixture, tmp, expected_attestations=0)
            self._assert_quarantine(result, verifier.VerifyErrorCode.INVALID_MANIFEST)

    def test_failed_signature_stops_before_attestation_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            result = self._verify(fixture, tmp, tamper="signature")
            self._assert_quarantine(result, verifier.VerifyErrorCode.VERIFY_FAILED)
            self.assertEqual([call[1] for call in self.cosign_calls], ["verify"])

    def test_wrong_certificate_identity_or_issuer_is_missing_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            cases = (
                {"certificate_identity": "https://github.com/example/untrusted/workflow@main"},
                {"certificate_oidc_issuer": "https://issuer.example.test"},
            )
            for overrides in cases:
                with self.subTest(**overrides):
                    result = self._verify(
                        fixture,
                        tmp,
                        expected_verify=0,
                        expected_attestations=0,
                        **overrides,
                    )
                    self._assert_quarantine(result, verifier.VerifyErrorCode.MISSING_INPUT)
                    self.assertEqual(self.cosign_calls, [])

    def test_digest_and_quarantine_release_conflicts_are_digest_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            cases = (
                {
                    "name": "identical_refs",
                    "overrides": {"artifact_ref": fixture["backend_ref"]},
                },
                {
                    "name": "identical_digests",
                    "overrides": {
                        "artifact_ref": fixture["artifact_ref"].replace(
                            fixture["artifact_digest"],
                            fixture["backend_digest"],
                        )
                    },
                },
                {
                    "name": "backend_ref_digest_mismatch",
                    "overrides": {"backend_digest": "sha256:" + "a" * 64},
                },
                {
                    "name": "release_bundle_with_quarantine_backend",
                    "overrides": {
                        "artifact_ref": fixture["artifact_ref"].replace(
                            "/plugins-quarantine/",
                            "/plugins/",
                        )
                    },
                },
                {
                    "name": "release_backend_with_quarantine_bundle",
                    "overrides": {
                        "backend_ref": fixture["backend_ref"].replace(
                            "/plugins-quarantine/",
                            "/plugins/",
                        )
                    },
                },
            )
            for case in cases:
                with self.subTest(case=case["name"]):
                    result = self._verify(
                        fixture,
                        tmp,
                        expected_verify=0,
                        expected_attestations=0,
                        **case["overrides"],
                    )
                    self._assert_quarantine(result, verifier.VerifyErrorCode.DIGEST_CONFLICT)
                    self.assertEqual(self.cosign_calls, [])

    def test_missing_capability_grant_has_concrete_incompatible_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            result = self._verify(fixture, tmp, granted_capabilities=("cache",))
            self._assert_quarantine(result, verifier.VerifyErrorCode.INCOMPATIBLE)
            self.assertIn("capability_grant", result["reason"])
            self.assertIn("objects", result["reason"])
            failed = [item for item in result["checks"] if not item["ok"]]
            self.assertEqual([item["name"] for item in failed], ["gate:capability_grant"])
            evidence = result["quarantine_evidence"]
            self.assertEqual(evidence["code"], verifier.VerifyErrorCode.INCOMPATIBLE)
            self.assertEqual(evidence["checks"], result["checks"])

    def test_empty_platform_version_has_concrete_incompatible_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            result = self._verify(fixture, tmp, platform_version="")
            self._assert_quarantine(result, verifier.VerifyErrorCode.INCOMPATIBLE)
            failed = {item["name"] for item in result["checks"] if not item["ok"]}
            self.assertEqual(failed, {"gate:platform_version", "gate:min_platform_version"})
            self.assertIn("platform_version", result["reason"])
            self.assertIn("min_platform_version", result["reason"])
            evidence = result["quarantine_evidence"]
            self.assertEqual(evidence["code"], verifier.VerifyErrorCode.INCOMPATIBLE)
            self.assertEqual(evidence["checks"], result["checks"])

    def test_provenance_config_source_uri_or_sha1_tamper_is_invalid_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            for tamper in ("provenance_uri", "provenance_sha1"):
                with self.subTest(tamper=tamper):
                    result = self._verify(fixture, tmp, tamper=tamper)
                    self._assert_quarantine(result, verifier.VerifyErrorCode.INVALID_EVIDENCE)

    def test_bundle_sbom_missing_required_checksum_is_invalid_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            result = self._verify(fixture, tmp, tamper="bundle_sbom_checksums")
            self._assert_quarantine(result, verifier.VerifyErrorCode.INVALID_EVIDENCE)
            self.assertIn("bundle", result["reason"])

    def test_bundle_provenance_wrong_build_type_is_invalid_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            result = self._verify(fixture, tmp, tamper="bundle_provenance_build_type")
            self._assert_quarantine(result, verifier.VerifyErrorCode.INVALID_EVIDENCE)
            self.assertIn("bundle", result["reason"])

    def test_empty_attestation_stdout_is_invalid_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            result = self._verify(fixture, tmp, tamper="empty_attestation")
            self._assert_quarantine(result, verifier.VerifyErrorCode.INVALID_EVIDENCE)
            self.assertEqual(
                [call[1] for call in self.cosign_calls],
                ["verify", "verify", "verify-attestation"],
            )


class SbomVersionContractTests(unittest.TestCase):
    def _validate_version(self, kind: str, version: str | None) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _make_oci(tmp)
            if kind == "backend":
                predicate, _ = _backend_predicates(tmp, fixture)
                ref = fixture["backend_ref"]
            elif kind == "bundle":
                predicate, _ = builder.build_bundle_evidence(
                    fixture["source"],
                    fixture["oci"],
                    fixture["artifact_digest"],
                    "ne-tort/agentscale",
                    fixture["source_sha"],
                    fixture["builder_id"],
                    "run-1",
                )
                ref = fixture["artifact_ref"]
            else:
                raise ValueError(f"unknown evidence kind: {kind}")
            if version is None:
                predicate.pop("spdxVersion", None)
            else:
                predicate["spdxVersion"] = version
            verifier._validate_sbom_predicate(
                predicate,
                kind=kind,
                ref=ref,
                source_repository="ne-tort/agentscale",
                source_sha=fixture["source_sha"],
                manifest_digest=fixture["oci"]["config"]["digest"],
            )

    def test_native_backend_accepts_spdx_2_2_and_2_3(self) -> None:
        for version in ("SPDX-2.2", "SPDX-2.3"):
            with self.subTest(version=version):
                self._validate_version("backend", version)

    def test_self_generated_bundle_rejects_spdx_2_2(self) -> None:
        with self.assertRaises(verifier.VerifyError) as denied:
            self._validate_version("bundle", "SPDX-2.2")
        self.assertEqual(denied.exception.code, verifier.VerifyErrorCode.INVALID_EVIDENCE)

    def test_missing_and_unsupported_spdx_versions_are_rejected(self) -> None:
        for kind in ("backend", "bundle"):
            for version in (None, "SPDX-2.1", "SPDX-3.0"):
                with self.subTest(kind=kind, version=version):
                    with self.assertRaises(verifier.VerifyError) as denied:
                        self._validate_version(kind, version)
                    self.assertEqual(
                        denied.exception.code,
                        verifier.VerifyErrorCode.INVALID_EVIDENCE,
                    )


class WorkflowStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.publication = PUBLICATION_PATH.read_text(encoding="utf-8")
        cls.dry = cls.workflow[cls.workflow.index("  dry-run:") : cls.workflow.index("  release:")]
        cls.release = cls.workflow[cls.workflow.index("  release:") :]

    def test_pr_has_no_writes_or_persistent_credentials(self) -> None:
        # Dry-run may read from the GHCR base mirror, never write to a registry.
        self.assertIn("packages: read", self.dry)
        self.assertIn("id-token: none", self.dry)
        for value in ("oras push", "--push", "packages: write", "id-token: write"):
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

    def test_release_job_excludes_pr_events_and_scopes_write_permissions(self) -> None:
        import yaml

        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        self.assertIn("dry-run", jobs)
        self.assertIn("release", jobs)
        self.assertEqual(jobs["release"]["permissions"]["packages"], "write")
        self.assertEqual(jobs["release"]["permissions"]["id-token"], "write")
        for permission in ("packages", "id-token"):
            self.assertNotEqual(workflow.get("permissions", {}).get(permission), "write")
        for job_name, job in jobs.items():
            if job_name == "release":
                continue
            with self.subTest(job=job_name):
                permissions = job.get("permissions", {})
                self.assertNotEqual(permissions.get("packages"), "write")
                self.assertNotEqual(permissions.get("id-token"), "write")
        release_if = jobs["release"]["if"]
        self.assertIn("github.event_name != 'pull_request'", release_if)
        self.assertIn("github.event_name != 'pull_request_target'", release_if)
        self.assertIs(workflow["concurrency"]["cancel-in-progress"], False)
        for job_name in ("dry-run", "release"):
            with self.subTest(timeout_job=job_name):
                timeout = jobs[job_name]["timeout-minutes"]
                self.assertIsInstance(timeout, int)
                self.assertNotIsInstance(timeout, bool)
                self.assertGreater(timeout, 0)

    def test_release_verifies_require_nonempty_protected_inputs(self) -> None:
        for step_name in (
            "Verify signatures and all evidence before any release write",
            "Reverify actual release refs and copied referrers",
        ):
            with self.subTest(step=step_name):
                self.assertEqual(self.release.count(step_name), 1)
        for environment_variable in ("PLATFORM_VERSION", "PLUGIN_GRANTED_CAPABILITIES"):
            with self.subTest(environment_variable=environment_variable):
                self.assertEqual(
                    self.release.count(
                        environment_variable + ': ${{ vars.' + environment_variable + " || '' }}"
                    ),
                    2,
                )
                flag = (
                    "--platform-version"
                    if environment_variable == "PLATFORM_VERSION"
                    else "--granted-capabilities"
                )
                self.assertEqual(
                    self.release.count(flag + ' "$' + environment_variable + '"'),
                    2,
                )
        guard = '[[ -n "$PLATFORM_VERSION" && -n "$PLUGIN_GRANTED_CAPABILITIES" ]] || {'
        self.assertEqual(self.release.count(guard), 1)
        self.assertLess(self.release.index(guard), self.release.index("id: promote"))

    def test_push_and_pr_paths_cover_plugin_code_workflow_and_tests(self) -> None:
        import yaml

        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        expected_paths = {
            "packages/plugins/**",
            ".github/workflows/plugin-artifacts.yml",
            ".github/scripts/plugin_*.py",
            "apps/api/tests/unit/plugins/test_ci_plugin_*.py",
        }
        triggers = workflow.get("on", workflow.get(True))
        for trigger in ("push", "pull_request"):
            with self.subTest(trigger=trigger):
                self.assertLessEqual(expected_paths, set(triggers[trigger]["paths"]))

    def test_offline_stdlib_modules_are_discovered_once_they_exist(self) -> None:
        start = self.dry.index("- name: Run stdlib focused and module contract tests")
        step = self.dry[start : self.dry.index("- name: Diagnose runner egress", start)]
        loop = re.search(r"for TEST_FILE in ([^;]+); do", step)
        self.assertIsNotNone(loop)
        discovered = set(loop.group(1).split())
        # Promotion discovery already has a dedicated regression in
        # test_ci_plugin_promotion.py; do not duplicate it here.
        modules = (
            "supply_chain",
            "publication",
            "network",
            "archive_safety",
            "resume",
            "release_evidence",
        )
        for module in modules:
            with self.subTest(module=module):
                test_path = (
                    ROOT / "apps" / "api" / "tests" / "unit" / "plugins"
                    / f"test_ci_plugin_{module}.py"
                )
                self.assertTrue(
                    not test_path.is_file() or f"test_ci_plugin_{module}.py" in discovered,
                    f"{test_path} exists but is not discovered",
                )
        self.assertIn(
            'if [[ -f "$SOURCE_DIR/apps/api/tests/unit/plugins/$TEST_FILE" ]]', step
        )

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


class WorkflowEnvironmentTiming(unittest.TestCase):
    """GITHUB_ENV/ PATH take effect in later steps, never in the writer step."""

    def test_env_exports_have_a_local_value_before_same_step_use(self) -> None:
        import yaml

        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        offenders: list[str] = []
        for job_name, job in workflow["jobs"].items():
            for step in job["steps"]:
                run = step.get("run") or ""
                writes = set(re.findall(r'echo ["\x27]([A-Z][A-Z0-9_]+)=', run))
                writes.update(
                    re.findall(r'handle\.write\(f?["\x27]([A-Z][A-Z0-9_]+)=', run)
                )
                reads = {
                    name
                    for pair in re.findall(
                        r'\$([A-Z][A-Z0-9_]+)\b|\$\{([A-Z][A-Z0-9_]+)\}', run
                    )
                    for name in pair
                    if name
                }
                local = set(
                    re.findall(r'(?m)^\s*([A-Z][A-Z0-9_]+)=', run)
                )
                for name in sorted((writes & reads) - local):
                    offenders.append(f"{job_name}: {step.get('name')}: {name}")
        self.assertEqual(offenders, [])

    def test_backend_manifest_digest_is_read_from_the_computed_value(self) -> None:
        import yaml

        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        step = next(
            step
            for step in workflow["jobs"]["release"]["steps"]
            if step.get("name", "").startswith("Extract backend buildx SBOM")
        )
        run = step["run"]
        self.assertIn('image-manifest-digest.txt", "w"', run)
        self.assertIn(
            'BACKEND_IMAGE_MANIFEST_DIGEST="$(< "$EVIDENCE/image-manifest-digest.txt")"',
            run,
        )
        self.assertLess(
            run.index("BACKEND_IMAGE_MANIFEST_DIGEST=\"$(<"),
            run.index('plugin_manifest_builder.py" backend-evidence'),
        )


class WorkflowTrustedBuilderIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import yaml

        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.step = next(
            step
            for step in workflow["jobs"]["release"]["steps"]
            if step.get("name", "").startswith("Validate GitHub OIDC source")
        )
        cls.trust_run = cls.step["run"]

    def test_direct_push_and_dispatch_use_the_validated_canonical_branch(self) -> None:
        self.assertIn('if [[ "$GITHUB_EVENT_NAME" == "workflow_call" ]]', self.trust_run)
        self.assertIn(
            'TRUSTED_BUILDER_ID="ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@$GITHUB_REF"',
            self.trust_run,
        )
        self.assertIn("EXPECTED_REF", self.step["env"])
        self.assertIn("DEFAULT_BRANCH", self.step["env"])

    def test_workflow_call_uses_the_pinned_input_instead_of_the_caller_ref(self) -> None:
        self.assertIn(
            'EXPECTED_BUILDER_ID="ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@$WORKFLOW_SHA"',
            self.trust_run,
        )
        self.assertIn('[[ "$INPUT_WORKFLOW_REF" == "$EXPECTED_BUILDER_ID" ]]', self.trust_run)
        self.assertIn('TRUSTED_BUILDER_ID="$INPUT_WORKFLOW_REF"', self.trust_run)
        assignments = re.findall(
            r"(?m)^[ \t]*TRUSTED_BUILDER_ID=(\S+)$", self.trust_run
        )
        self.assertEqual(
            assignments,
            [
                '"$INPUT_WORKFLOW_REF"',
                '"ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@$GITHUB_REF"',
            ],
        )

    def test_validate_context_uses_the_identity_exported_by_release_trust(self) -> None:
        self.assertIn("builder = trust.builder_identity", self.trust_run)
        self.assertIn(r'handle.write(f"TRUSTED_BUILDER_ID={builder}\n")', self.trust_run)
        self.assertIn('--builder-identity "$TRUSTED_BUILDER_ID"', self.trust_run)
        self.assertLess(
            self.trust_run.index("TRUSTED_BUILDER_ID="),
            self.trust_run.index('plugin_publication.py" validate-context'),
        )


class WorkflowDockerConfigPath(unittest.TestCase):
    def test_publish_computes_and_checks_the_login_config_without_runner_home(self) -> None:
        import yaml

        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        steps = workflow["jobs"]["release"]["steps"]
        login_index = next(
            index for index, step in enumerate(steps)
            if step.get("name", "").startswith("Login to GHCR")
        )
        publish_index = next(
            index for index, step in enumerate(steps)
            if step.get("name", "").startswith("Verify independent release evidence")
        )
        publish = steps[publish_index]
        run = publish["run"]
        assignment = 'export DOCKER_CONFIG_PATH="${DOCKER_CONFIG:-$HOME/.docker}/config.json"'
        check = 'test -f "$DOCKER_CONFIG_PATH"'

        self.assertLess(login_index, publish_index)
        self.assertNotIn("DOCKER_CONFIG_PATH", publish.get("env", {}))
        self.assertNotIn("runner.home", WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.assertIn(assignment, run)
        self.assertIn(check, run)
        self.assertLess(run.index(assignment), run.index(check))
        self.assertLess(run.index(check), run.index('plugin_release_evidence.py"'))


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

    def test_provenance_run_identity_is_required_under_either_camel_spelling(self) -> None:
        """SLSA builders spell the run key differently; the verifier must accept
        either canonical camelCase form but never treat a missing/empty run
        identity as verified evidence."""

        verify = (ROOT / ".github" / "scripts" / "plugin_verify.py").read_text(encoding="utf-8")
        self.assertIn('metadata.get("buildInvocationId")', verify)
        self.assertIn('metadata.get("buildInvocationID")', verify)
        self.assertIn("provenance run identity is missing", verify)
        # Пустое значение под любым из ключей не должно считаться валидным run identity.
        self.assertNotIn('metadata.get("buildInvocationId", "")', verify)
        self.assertNotIn('metadata.get("buildInvocationID", "")', verify)
        # Не ослабляем остальные проверки provenance.
        self.assertIn("provenance builder identity mismatch", verify)
        self.assertIn("provenance source URI mismatch", verify)
        self.assertIn("provenance source SHA mismatch", verify)
        self.assertIn("wrong {kind} provenance buildType", verify)
        self.assertIn("expected_build_type", verify)
        self.assertIn("SLSA v0.2 invocation.parameters are missing", verify)

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
        self.assertIn('cd "$(dirname "$CANONICAL_MANIFEST")"', run)
        for relative in (
            'canonical-manifest.json:$PLUGIN_MANIFEST_MEDIA_TYPE',
            'frontend-dist.tar.gz:$FRONTEND_MEDIA_TYPE',
            'migrations.tar.gz:$MIGRATIONS_MEDIA_TYPE',
        ):
            self.assertIn(f'"{relative}"', run)
        self.assertNotIn("--disable-path-validation", run)

    def test_builder_pins_created_annotation_for_oras_to_preserve(self) -> None:
        """created is signed content; ORAS must not generate a different value."""

        source = _source_manifest()
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp) / "canonical-manifest.json"
            canonical.write_bytes(builder.canonical_manifest_bytes(source))
            frontend = Path(tmp) / "frontend-dist.tar.gz"
            migrations = Path(tmp) / "migrations.tar.gz"
            frontend.write_bytes(b"frontend")
            migrations.write_bytes(b"migrations")
            manifest = builder.build_bundle_manifest(
                source,
                "ghcr.io/ne-tort/plugins/hello_plugin/backend@sha256:" + "a" * 64,
                str(canonical),
                str(frontend),
                str(migrations),
                "ne-tort/agentscale",
                "c" * 40,
            )
        created = manifest["annotations"]["org.opencontainers.image.created"]
        self.assertRegex(created, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(builder.annotation_file(manifest)["$manifest"]["org.opencontainers.image.created"], created)

        verify = (ROOT / ".github" / "scripts" / "plugin_verify.py").read_text(encoding="utf-8")
        self.assertNotIn("pushed.pop(_ts_key", verify)
        self.assertNotIn("expected.pop(_ts_key", verify)


class WorkflowEgressProbeBounds(unittest.TestCase):
    def test_egress_diagnostics_never_run_without_hard_timeouts(self) -> None:
        """Unbounded -6 probe previously hung the entire dry-run job."""

        import yaml

        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        step = next(
            step
            for step in workflow["jobs"]["dry-run"]["steps"]
            if step.get("name", "").startswith("Diagnose runner egress")
        )
        run = step["run"]
        self.assertNotIn("curl -4 -sS", run)
        self.assertNotIn("curl -6 -sS", run)
        self.assertIn("--connect-timeout 5", run)
        self.assertIn("--max-time 15", run)

if __name__ == "__main__":
    unittest.main()
