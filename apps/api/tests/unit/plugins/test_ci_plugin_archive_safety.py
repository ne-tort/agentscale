"""Focused regression tests for plugin payload archiving and SLSA metadata.

Stdlib only: no pytest, registry, network, Docker, cosign or subprocess use.
Covers the fail-closed staging of the migrations layer (symlink escape) and
the canonical provenance metadata key contract shared with plugin_verify.py.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
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


def _load(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load(
    ROOT / ".github" / "scripts" / "plugin_manifest_builder.py",
    "plugin_manifest_builder",
)
sys.modules["plugin_manifest_builder"] = builder
verifier = _load(
    ROOT / ".github" / "scripts" / "plugin_verify.py",
    "plugin_verify_archive_safety_test",
)

PLUGIN_DIR = "packages/plugins/demo-plugin"
SOURCE_REPOSITORY = "ne-tort/agentscale"
SOURCE_SHA = "c" * 40
BUILDER_ID = "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "1" * 40
MANIFEST_DIGEST = "sha256:" + "e" * 64
IMAGE_DIGEST = "sha256:" + "d" * 64
BACKEND_REF = "ghcr.io/ne-tort/plugins/demo/backend@" + IMAGE_DIGEST
BUNDLE_REF = "ghcr.io/ne-tort/plugins/demo/bundle@sha256:" + "f" * 64


def _source_manifest() -> dict:
    return {
        "plugin_id": "demo",
        "version": "1.0.0",
        "protocol": "plugin-rpc/1",
        "frontend": {"entry": "index.html", "base_path": "/demo"},
        "backend": {"port": 8080, "health": "/health"},
        "capabilities": ["cache"],
        "migrations": {"engine": "liquibase", "path": "migrations/liquibase/changelog.xml"},
        "egress_ref": "grant:demo",
        "public_pages": [],
        "webhooks": [],
        "min_platform_version": "1.0.0",
    }


def _make_package(source_root: Path) -> Path:
    package = source_root / PLUGIN_DIR
    frontend = package / "frontend" / "dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
    changelog = package / "migrations" / "liquibase"
    changelog.mkdir(parents=True)
    (changelog / "changelog.xml").write_text("<databaseChangeLog/>", encoding="utf-8")
    (package / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (package / "manifest.json").write_text(json.dumps(_source_manifest()), encoding="utf-8")
    return package


def _symlink_or_skip(
    test: unittest.TestCase, target: Path, link: Path, *, directory: bool
) -> None:
    try:
        os.symlink(str(target), str(link), target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        test.skipTest(f"symlinks are unavailable on this host: {exc}")


def _assert_rejected(test: unittest.TestCase, tmp: str) -> None:
    with test.assertRaises(builder.ManifestError) as ctx:
        builder.prepare_package(
            str(Path(tmp) / "source"), PLUGIN_DIR, "", str(Path(tmp) / "prepared")
        )
    test.assertEqual(ctx.exception.code, builder.ManifestErrorCode.INVALID_LAYOUT)


class MigrationsStagingTests(unittest.TestCase):
    def test_symlinked_migrations_directory_with_external_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _make_package(Path(tmp) / "source")
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "secret.txt").write_text("not-a-migration", encoding="utf-8")
            _symlink_or_skip(self, outside, package / "migrations" / "escape", directory=True)
            _assert_rejected(self, tmp)

    def test_symlinked_migrations_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _make_package(Path(tmp) / "source")
            outside = Path(tmp) / "outside.xml"
            outside.write_text("<changeSet/>", encoding="utf-8")
            _symlink_or_skip(
                self, outside, package / "migrations" / "linked.xml", directory=False
            )
            _assert_rejected(self, tmp)

    def test_symlinked_frontend_directory_is_also_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _make_package(Path(tmp) / "source")
            outside = Path(tmp) / "outside-dist"
            outside.mkdir()
            (outside / "index.html").write_text("<html></html>", encoding="utf-8")
            # Replace the real dist directory: os.symlink cannot shadow an
            # existing path, and a symlinked source root must fail closed too.
            shutil.rmtree(package / "frontend" / "dist")
            _symlink_or_skip(self, outside, package / "frontend" / "dist", directory=True)
            _assert_rejected(self, tmp)

    @unittest.skipUnless(
        hasattr(os, "mkfifo") and os.name == "posix", "requires POSIX FIFO support"
    )
    def test_non_regular_migrations_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _make_package(Path(tmp) / "source")
            os.mkfifo(str(package / "migrations" / "pipe.fifo"))
            _assert_rejected(self, tmp)

    def test_normal_migrations_keep_relative_paths_and_deterministic_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "source"
            package = _make_package(source_root)
            nested = package / "migrations" / "liquibase" / "changes"
            nested.mkdir()
            (nested / "001-init.xml").write_text("<changeSet/>", encoding="utf-8")
            first = builder.prepare_package(
                str(source_root), PLUGIN_DIR, "", str(Path(tmp) / "out-a")
            )
            second = builder.prepare_package(
                str(source_root), PLUGIN_DIR, "", str(Path(tmp) / "out-b")
            )
            archive = Path(first["migrations_archive"])
            with tarfile.open(archive, "r:gz") as bundle:
                members = bundle.getmembers()
            self.assertEqual(
                [member.name for member in members],
                ["liquibase/changelog.xml", "liquibase/changes/001-init.xml"],
            )
            for member in members:
                self.assertTrue(member.isfile())
                self.assertEqual(member.mtime, 0)
                self.assertEqual(member.mode, 0o644)
                self.assertEqual((member.uid, member.gid), (0, 0))
                self.assertEqual((member.uname, member.gname), ("", ""))
            with tarfile.open(archive, "r:gz") as bundle:
                changelog = bundle.extractfile("liquibase/changelog.xml")
                self.assertIsNotNone(changelog)
                self.assertEqual(changelog.read(), b"<databaseChangeLog/>")
            self.assertEqual(
                hashlib.sha256(archive.read_bytes()).hexdigest(),
                hashlib.sha256(Path(second["migrations_archive"]).read_bytes()).hexdigest(),
            )
            self.assertTrue(Path(first["frontend_archive"]).is_file())
            self.assertTrue(Path(first["canonical_manifest"]).is_file())


def _bundle_predicate(metadata: dict) -> dict:
    return {
        "buildType": builder.BUNDLE_BUILD_TYPE,
        "builder": {"id": "https://github.com/" + BUILDER_ID},
        "invocation": {
            "configSource": {
                "uri": f"git+https://github.com/{SOURCE_REPOSITORY}@{SOURCE_SHA}",
                "digest": {"sha1": SOURCE_SHA},
            },
            "parameters": {
                "artifactKind": "agentscale-plugin-bundle",
                "canonicalManifestDigest": MANIFEST_DIGEST,
            },
        },
        "metadata": metadata,
    }


class ProvenanceInvocationKeyTests(unittest.TestCase):
    def test_source_provenance_emits_canonical_build_invocation_id(self) -> None:
        for build_type in (builder.BACKEND_BUILD_TYPE, builder.BUNDLE_BUILD_TYPE):
            provenance = builder._source_provenance(
                BUILDER_ID, build_type, SOURCE_REPOSITORY, SOURCE_SHA, "12345-1"
            )
            self.assertEqual(provenance["metadata"], {"buildInvocationID": "12345-1"})
            self.assertNotIn("buildInvocationId", provenance["metadata"])

    def test_backend_evidence_provenance_uses_canonical_key_and_passes_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "buildx-evidence"
            evidence.mkdir()
            subject = [{"name": "backend-image", "digest": {"sha256": "d" * 64}}]
            statements = {
                "sbom.json": {
                    "predicateType": builder.SPDX_PREDICATE_TYPE,
                    "subject": subject,
                    "predicate": {"packages": [{"name": "backend"}]},
                },
                "provenance.json": {
                    "predicateType": "https://slsa.dev/provenance/v0.2",
                    "subject": subject,
                    "predicate": {
                        "builder": {"id": "https://github.com/docker/buildkit"},
                        "buildType": "https://github.com/docker/buildx/build",
                    },
                },
            }
            for name, statement in statements.items():
                (evidence / name).write_text(json.dumps(statement), encoding="utf-8")
            sbom, provenance = builder.prepare_backend_evidence(
                str(evidence),
                IMAGE_DIGEST,
                SOURCE_REPOSITORY,
                SOURCE_SHA,
                BUILDER_ID,
                "12345-1",
            )
        self.assertEqual(provenance["metadata"], {"buildInvocationID": "12345-1"})
        verifier._validate_provenance_predicate(
            provenance,
            kind="backend",
            source_repository=SOURCE_REPOSITORY,
            source_sha=SOURCE_SHA,
            builder_identity=BUILDER_ID,
            manifest_digest=MANIFEST_DIGEST,
        )

    def test_verifier_accepts_canonical_and_legacy_casing_and_rejects_missing(self) -> None:
        common = dict(
            kind="bundle",
            source_repository=SOURCE_REPOSITORY,
            source_sha=SOURCE_SHA,
            builder_identity=BUILDER_ID,
            manifest_digest=MANIFEST_DIGEST,
        )
        verifier._validate_provenance_predicate(
            _bundle_predicate({"buildInvocationID": "12345-1"}), **common
        )
        verifier._validate_provenance_predicate(
            _bundle_predicate({"buildInvocationId": "12345-1"}), **common
        )
        with self.assertRaises(verifier.VerifyError) as ctx:
            verifier._validate_provenance_predicate(_bundle_predicate({}), **common)
        self.assertEqual(ctx.exception.code, verifier.VerifyErrorCode.INVALID_EVIDENCE)


def _backend_sbom(spdx_version: str) -> dict:
    return {
        "spdxVersion": spdx_version,
        "documentNamespace": "https://example.test/backend-sbom",
        "packages": [{"SPDXID": "SPDXRef-image", "name": "backend"}],
    }


def _bundle_sbom(spdx_version: str) -> dict:
    bundle_digest = BUNDLE_REF.rsplit("@sha256:", 1)[1]
    return {
        "spdxVersion": spdx_version,
        "documentNamespace": (
            f"https://agentscale.dev/spdx/{SOURCE_REPOSITORY}/{SOURCE_SHA}/{bundle_digest}"
        ),
        "comment": (
            f"source_repository={SOURCE_REPOSITORY};source_sha={SOURCE_SHA};artifact=bundle"
        ),
        "packages": [
            {
                "SPDXID": "SPDXRef-bundle",
                "name": "demo-oci-bundle",
                "checksums": [{"algorithm": "SHA256", "checksumValue": bundle_digest}],
            },
            {
                "SPDXID": "SPDXRef-canonical-manifest",
                "name": "demo-canonical-manifest",
                "checksums": [
                    {
                        "algorithm": "SHA256",
                        "checksumValue": MANIFEST_DIGEST.removeprefix("sha256:"),
                    }
                ],
            },
        ],
    }


def _check_sbom(predicate: dict, *, kind: str) -> None:
    verifier._validate_sbom_predicate(
        predicate,
        kind=kind,
        ref=BACKEND_REF if kind == "backend" else BUNDLE_REF,
        source_repository=SOURCE_REPOSITORY,
        source_sha=SOURCE_SHA,
        manifest_digest=MANIFEST_DIGEST,
    )


class SbomSpdxVersionTests(unittest.TestCase):
    def test_backend_accepts_native_spdx_2_2_and_2_3(self) -> None:
        for version in ("SPDX-2.2", "SPDX-2.3"):
            _check_sbom(_backend_sbom(version), kind="backend")

    def test_bundle_accepts_spdx_2_3(self) -> None:
        _check_sbom(_bundle_sbom("SPDX-2.3"), kind="bundle")

    def test_bundle_rejects_native_spdx_2_2(self) -> None:
        with self.assertRaises(verifier.VerifyError) as ctx:
            _check_sbom(_bundle_sbom("SPDX-2.2"), kind="bundle")
        self.assertEqual(ctx.exception.code, verifier.VerifyErrorCode.INVALID_EVIDENCE)

    def test_unsupported_versions_are_rejected_for_both_kinds(self) -> None:
        for kind in ("backend", "bundle"):
            for version in ("SPDX-2.1", "SPDX-3.0"):
                predicate = (
                    _backend_sbom(version) if kind == "backend" else _bundle_sbom(version)
                )
                with self.assertRaises(verifier.VerifyError) as ctx:
                    _check_sbom(predicate, kind=kind)
                self.assertEqual(ctx.exception.code, verifier.VerifyErrorCode.INVALID_EVIDENCE)

    def test_missing_spdx_version_is_rejected_for_both_kinds(self) -> None:
        for kind in ("backend", "bundle"):
            predicate = (
                _backend_sbom("SPDX-2.3") if kind == "backend" else _bundle_sbom("SPDX-2.3")
            )
            del predicate["spdxVersion"]
            with self.assertRaises(verifier.VerifyError) as ctx:
                _check_sbom(predicate, kind=kind)
            self.assertEqual(ctx.exception.code, verifier.VerifyErrorCode.INVALID_EVIDENCE)


if __name__ == "__main__":
    unittest.main()
