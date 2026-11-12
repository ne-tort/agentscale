"""Unit/static регрессии назначенного plugin CI verify-gate.

Тесты НЕ запускаются в migration verification freeze. Динамический импорт
plugin_verify.py не вызывает main, subprocess, cosign или сетевые операции.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import sys
from types import ModuleType

import pytest

from prodavan.application.plugins.artifacts.verification import (
    _semver_key as backend_semver_key,
)
from prodavan.domain.plugins.types import PLUGIN_ID_PATTERN


def _find_repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "apps" / "api" / "src").is_dir() and (
            parent / ".github" / "scripts" / "plugin_verify.py"
        ).is_file():
            return parent
    raise RuntimeError("repository root with plugin CI scripts was not found")


REPO_ROOT = _find_repo_root()
CI_SCRIPT = REPO_ROOT / ".github" / "scripts" / "plugin_verify.py"
BUILDER_SCRIPT = REPO_ROOT / ".github" / "scripts" / "plugin_manifest_builder.py"
PUBLICATION_SCRIPT = REPO_ROOT / ".github" / "scripts" / "plugin_publication.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "plugin-artifacts.yml"
SCHEMA = REPO_ROOT / "packages" / "schemas" / "plugin-manifest" / "v1" / "schema.json"


@pytest.fixture(scope="session")
def ci_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("plugin_verify_under_test", CI_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        ("1.2.0", "1.10.0"),
        ("1.0.0-alpha", "1.0.0-alpha.1"),
        ("1.0.0-alpha.2", "1.0.0-alpha.10"),
        ("1.0.0-2", "1.0.0-alpha"),
        ("1.0.0-alpha.beta", "1.0.0-beta"),
        ("1.0.0-beta.2", "1.0.0-beta.11"),
        ("1.0.0-beta.11", "1.0.0-rc.1"),
        ("1.0.0-rc.1", "1.0.0"),
    ],
)
def test_ci_semver_precedence_matches_backend(
    ci_module: ModuleType, lower: str, higher: str
) -> None:
    assert ci_module._semver_key(lower) < ci_module._semver_key(higher)
    assert backend_semver_key(lower) < backend_semver_key(higher)
    assert ci_module._semver_key(lower) == backend_semver_key(lower)
    assert ci_module._semver_key(higher) == backend_semver_key(higher)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("1.0.0+ci.1", "1.0.0+ci.2"),
        ("1.0.0-rc.1+ci.1", "1.0.0-rc.1+ci.2"),
    ],
)
def test_ci_semver_ignores_build_metadata_like_backend(
    ci_module: ModuleType, left: str, right: str
) -> None:
    assert ci_module._semver_key(left) == ci_module._semver_key(right)
    assert ci_module._semver_key(left) == backend_semver_key(left)
    assert ci_module._semver_key(right) == backend_semver_key(right)


@pytest.mark.parametrize(
    "version",
    ["1.0.0-01", "1.0.0-alpha.01", "1.0.0-1..2", "1.0.0+build..1"],
)
def test_ci_rejects_non_semver_20_identifiers(ci_module: ModuleType, version: str) -> None:
    with pytest.raises(ci_module.VerifyError):
        ci_module._semver_key(version)
    with pytest.raises(ValueError):
        backend_semver_key(version)


@pytest.mark.parametrize(
    ("minimum", "platform", "expected"),
    [
        ("4.0.0-alpha.2", "4.0.0-alpha.10", True),
        ("4.0.0-alpha.10", "4.0.0-alpha.2", False),
        ("4.0.0+minimum", "4.0.0+platform", True),
    ],
)
def test_ci_gate_uses_semver_precedence(
    ci_module: ModuleType, minimum: str, platform: str, expected: bool
) -> None:
    gate = ci_module.CompatibilityGate(
        platform_version=platform,
        granted_capabilities=("cache",),
    )
    result = gate.evaluate(
        {
            "version": "1.0.0",
            "protocol": "plugin-rpc/1",
            "capabilities": (),
            "min_platform_version": minimum,
        }
    )
    assert result.compatible is expected


def test_ci_plugin_id_regex_matches_canonical_schema(ci_module: ModuleType) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    canonical = schema["properties"]["plugin_id"]["pattern"]
    assert canonical == PLUGIN_ID_PATTERN.pattern == "^[a-z][a-z0-9_]*$"
    assert ci_module._PLUGIN_ID_RE.pattern == canonical
    for valid in ("a", "hello_plugin", "mod_equipment2"):
        assert ci_module._PLUGIN_ID_RE.fullmatch(valid)
    for invalid in ("1plugin", "-plugin", "hello-plugin", "Plugin", "plugin.id"):
        assert ci_module._PLUGIN_ID_RE.fullmatch(invalid) is None


@pytest.mark.parametrize("plugin_id", ["1plugin", "-plugin", "hello-plugin", "Plugin"])
def test_ci_verify_rejects_legacy_builder_plugin_ids(
    ci_module: ModuleType, monkeypatch: pytest.MonkeyPatch, plugin_id: str
) -> None:
    class FakeManifestError(Exception):
        pass

    class FakeBuilder:
        ManifestError = FakeManifestError

        @staticmethod
        def validate_signed_manifest(manifest, *, backend_digest: str):
            return {"plugin_id": plugin_id}

    monkeypatch.setattr(ci_module, "_load_manifest_module", lambda: FakeBuilder)
    with pytest.raises(ci_module.VerifyError) as exc_info:
        ci_module.validate_manifest({}, backend_digest="sha256:" + "a" * 64)
    assert exc_info.value.code == ci_module.VerifyErrorCode.INVALID_MANIFEST
    assert "^[a-z][a-z0-9_]*$" in exc_info.value.reason


def test_ci_verify_accepts_canonical_plugin_id(
    ci_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeManifestError(Exception):
        pass

    class FakeBuilder:
        ManifestError = FakeManifestError

        @staticmethod
        def validate_signed_manifest(manifest, *, backend_digest: str):
            return {"plugin_id": "hello_plugin"}

    monkeypatch.setattr(ci_module, "_load_manifest_module", lambda: FakeBuilder)
    assert ci_module.validate_manifest({}, backend_digest="sha256:" + "a" * 64) == {
        "plugin_id": "hello_plugin"
    }


def _workflow_sections() -> tuple[str, str, str]:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    dry = workflow[workflow.index("  dry-run:") : workflow.index("  release:")]
    release = workflow[workflow.index("  release:") :]
    return workflow, dry, release


def test_builder_uses_canonical_plugin_id_and_semver_grammar() -> None:
    builder = BUILDER_SCRIPT.read_text(encoding="utf-8")
    assert '_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")' in builder
    assert "[a-z0-9][a-z0-9_-]{0,62}" not in builder
    assert "d*[a-zA-Z-][0-9a-zA-Z-]*" in builder


def test_pr_dry_run_has_no_registry_or_oidc_write_capability() -> None:
    _, dry, _ = _workflow_sections()
    # Dry-run может читать private GHCR base mirror, но не имеет write-capabilities.
    assert "packages: read" in dry
    assert "id-token: none" in dry
    for forbidden in (
        "packages: write", "id-token: write", "oras push",
        "cosign sign", "--push", "GHCR_PUSH_TOKEN", "agentscale-ci",
    ):
        assert forbidden not in dry
    assert "--provenance=false --sbom=false" in dry
    assert "Remove dry-run builder" in dry


def test_release_is_protected_and_oidc_validated_before_credentials() -> None:
    _, _, release = _workflow_sections()
    publication = PUBLICATION_SCRIPT.read_text(encoding="utf-8")
    assert "github.event_name == 'workflow_dispatch'" in release
    assert "github.event_name == 'workflow_call'" in release
    assert "github.event.repository.default_branch" in release
    assert "environment: plugin-release" in release
    assert release.index("Validate GitHub OIDC source and workflow trust before any write") < release.index(
        "Login to GHCR only after trust validation"
    )
    for claim in (
        '"repository"', '"repository_id"', '"ref"', '"sha"',
        '"event_name"', '"job_workflow_ref"', '"ref_protected"',
    ):
        assert claim in publication
    assert "agentscale-plugin-ci" in publication
    assert '[[ "$WORKFLOW_SHA" =~ ^[0-9a-f]{40}$ ]]' in release
    assert 'SHA_RE = re.compile(r"^[0-9a-f]{40}$")' in publication


def test_quarantine_verify_promote_release_verify_order() -> None:
    _, _, release = _workflow_sections()
    quarantine = release.index("Build and push backend only to quarantine")
    verify = release.index("Verify signatures and all evidence before any release write")
    promote = release.index("Promote verified backend and bundle with all referrers by digest")
    release_verify = release.index("Reverify actual release refs and copied referrers")
    assert quarantine < verify < promote < release_verify
    assert "plugins-quarantine" in release
    assert "oras copy --recursive" in release
    assert "QUAR_BACKEND@" in release and "RELEASE_BACKEND@" in release


def test_full_source_manifest_is_canonical_config_and_blob() -> None:
    workflow, _, _ = _workflow_sections()
    builder = BUILDER_SCRIPT.read_text(encoding="utf-8")
    assert "--config" in workflow and "CANONICAL_MANIFEST" in workflow
    assert '"$CANONICAL_MANIFEST:$PLUGIN_MANIFEST_MEDIA_TYPE"' in workflow
    assert "manifest must be both config and an identical blob" in builder
    assert "A_MANIFEST_DIGEST" in builder
    assert "len(layers) != 3" not in builder
    assert "ровно 3" not in builder


def test_attestations_target_actual_refs_and_backend_bundle_evidence_differs() -> None:
    workflow, _, _ = _workflow_sections()
    verifier = CI_SCRIPT.read_text(encoding="utf-8")
    assert 'cosign attest --yes --predicate "$RUNNER_TEMP/backend-sbom.json"' in workflow
    assert 'cosign attest --yes --predicate "$RUNNER_TEMP/bundle-sbom.json"' in workflow
    assert '"$BACKEND_REF"' in workflow and '"$ARTIFACT_REF"' in workflow
    assert "verify-attestation" in verifier
    assert "plugin-backend-buildx/v1" in verifier
    assert "plugin-oci-bundle/v1" in verifier
    assert "buildxProvenance" in verifier
    assert "canonicalManifestDigest" in verifier


def test_no_mutable_actions_shared_token_or_persistent_builder() -> None:
    workflow, _, _ = _workflow_sections()
    assert not re.search(r"^\s*uses:", workflow, flags=re.MULTILINE)
    assert not re.search(r"@[vV][0-9]+(?:\.|\b)", workflow)
    for forbidden in (
        "GHCR_PUSH_TOKEN", "agentscale-ci", "sigstore/cosign-installer@",
        "oras-project/setup-oras@",
    ):
        assert forbidden not in workflow
    assert "COSIGN_LINUX_AMD64_SHA256" in workflow
    assert "ORAS_LINUX_AMD64_SHA256" in workflow
    assert "sha256sum -c" in workflow
    assert "BUILDKIT_IMAGE" in workflow
    assert '--driver-opt "image=$BUILDKIT_IMAGE"' in workflow


def test_layout_and_resolved_identity_contract() -> None:
    workflow, _, _ = _workflow_sections()
    builder = BUILDER_SCRIPT.read_text(encoding="utf-8")
    assert 'frontend_dist = package / "frontend" / "dist"' in builder
    assert 'frontend_static = package / "frontend"' in builder
    assert 'package / "backend" / "Dockerfile"' in builder
    assert '"docker_context": str(package)' in builder
    assert "plugin_dir" in workflow and 'resolved["plugin_id"]' in workflow
    assert "inputs.version" not in workflow


def test_platform_register_is_real_fail_closed_publication() -> None:
    workflow, _, release = _workflow_sections()
    assert "publish through platform OIDC" in release
    assert '"$CI_DIR/plugin_publication.py" publish' in release
    assert "if: ${{ false }}" not in workflow
    assert "backend contract exists" not in workflow
    assert release.index("Reverify actual release refs and copied referrers") < release.index(
        "Verify independent release evidence and publish through platform OIDC"
    )
