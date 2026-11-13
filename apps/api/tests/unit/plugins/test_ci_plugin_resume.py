"""Static workflow guards for the B2 same-commit resume preflight.

Stdlib only; no workflow, helper, network or registry execution. The offline
helper itself (plugin_quarantine_resume.py) is validated separately; these
tests pin how plugin-artifacts.yml wires the resume path: placement, skip
conditions, raw ORAS fetches, exact helper arguments and env invariants.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github" / "workflows" / "plugin-artifacts.yml").is_file():
            return parent
    raise RuntimeError("plugin CI repository root not found")


ROOT = _repo_root()
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "plugin-artifacts.yml"
RESUME_HELPER = ROOT / ".github" / "scripts" / "plugin_quarantine_resume.py"


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
resume_helper = _load(RESUME_HELPER, "plugin_quarantine_resume_test")

RELEASE_BACKEND_REPO = "ghcr.io/ne-tort/plugins/demo/backend"
RELEASE_BUNDLE_REPO = "ghcr.io/ne-tort/plugins/demo/bundle"

TRUST = "Validate GitHub OIDC source and workflow trust before any write"
INSTALL = "Install cosign and ORAS by version plus protected checksum"
PREPARE = "Revalidate manifest, layout and protocol before quarantine build"
LOGIN = "Login to GHCR only after trust validation"
PREFLIGHT = "Resume preflight for same-commit quarantine artifacts"
EPHEMERAL_BUILDER = "Create fresh ephemeral release builder"
BACKEND_BUILD = "Build and push backend only to quarantine with buildx evidence"
BACKEND_EVIDENCE = "Extract backend buildx SBOM and provenance from actual image descriptors"
BACKEND_SIGN = "Sign and attest quarantined backend by digest"
BUNDLE_BUILD = "Build and push canonical OCI bundle only to quarantine"
BUNDLE_SIGN = "Generate independent bundle evidence, sign and attest by digest"
QUARANTINE_VERIFY = "Verify signatures and all evidence before any release write"
PROMOTE = "Promote verified backend and bundle with all referrers by digest"
RELEASE_VERIFY = "Reverify actual release refs and copied referrers"

RESUME_CONDITION = "if: steps.resume_preflight.outputs.resume != 'true'"


def _workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _step_block(workflow: str, name: str) -> str:
    start = workflow.index(f"      - name: {name}")
    following = workflow.find("\n      - name:", start + 1)
    return workflow[start:] if following == -1 else workflow[start:following]


class ResumePreflightPlacementTests(unittest.TestCase):
    def test_preflight_is_after_trust_tools_prepare_login_and_before_builds(self) -> None:
        workflow = _workflow()
        ordered = (
            TRUST,
            INSTALL,
            PREPARE,
            LOGIN,
            PREFLIGHT,
            EPHEMERAL_BUILDER,
            BACKEND_BUILD,
        )
        positions = [workflow.index(f"      - name: {name}") for name in ordered]
        self.assertEqual(positions, sorted(positions))

    def test_resume_preflight_step_has_output_id(self) -> None:
        block = _step_block(_workflow(), PREFLIGHT)
        self.assertIn("id: resume_preflight", block)


class ResumeProbeSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.block = _step_block(_workflow(), PREFLIGHT)

    def test_only_exact_same_commit_tags_are_resolved_for_both_artifacts(self) -> None:
        self.assertIn('probe "$QUAR_BACKEND"', self.block)
        self.assertIn('probe "$QUAR_BUNDLE"', self.block)
        self.assertIn("$repository:sha-$GITHUB_SHA", self.block)
        self.assertNotIn(":latest", self.block)

    def test_absence_is_only_explicit_not_found(self) -> None:
        self.assertIn("not found|404|manifest unknown", self.block)
        # Transient/auth/network failures must not be treated as absence.
        self.assertIn("transient/auth/network error is not artifact absence", self.block)

    def test_both_absent_runs_normal_build_and_both_present_resumes(self) -> None:
        self.assertIn('echo "resume=false" >> "$GITHUB_OUTPUT"', self.block)
        self.assertIn('echo "resume=true" >> "$GITHUB_OUTPUT"', self.block)
        self.assertIn('if [[ "$backend_rc" -eq 1 ]]', self.block)

    def test_partial_state_fails_instead_of_rebuilding_one_artifact(self) -> None:
        self.assertIn('if [[ "$backend_rc" -ne "$bundle_rc" ]]', self.block)
        self.assertIn("partial quarantine state", self.block)
        self.assertIn("refusing to rebuild or overwrite only one existing artifact", self.block)

    def test_no_caller_controlled_digest_or_approval_input(self) -> None:
        self.assertNotIn("${{ inputs.", self.block)
        self.assertNotIn("--approval", self.block)
        self.assertNotIn("trusted_workflow_ref", self.block)


class ResumeFetchAndHelperContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.block = _step_block(_workflow(), PREFLIGHT)

    def test_raw_manifest_and_config_blob_use_output_files(self) -> None:
        self.assertIn(
            'oras manifest fetch --output "$RESUME_DIR/bundle-manifest.json" '
            '"$QUAR_BUNDLE@$ARTIFACT_DIGEST"',
            self.block,
        )
        self.assertIn(
            'oras blob fetch --output "$RESUME_DIR/config-blob.json" '
            '"$QUAR_BUNDLE@$CONFIG_DIGEST"',
            self.block,
        )
        self.assertIn('["config"]["digest"]', self.block)

    def test_helper_receives_exact_resume_arguments(self) -> None:
        self.assertIn('python3 "$CI_DIR/plugin_quarantine_resume.py" resume', self.block)
        for argument in (
            '--bundle-manifest "$RESUME_DIR/bundle-manifest.json"',
            '--config-blob "$RESUME_DIR/config-blob.json"',
            # The prepare output path is used directly: GITHUB_ENV does not
            # affect the writer step, and reading CANONICAL_MANIFEST there is
            # forbidden by the workflow timing guard.
            '--local-canonical-manifest "$RUNNER_TEMP/plugin-prepare/canonical-manifest.json"',
            '--backend-digest "$BACKEND_DIGEST"',
            '--bundle-digest "$ARTIFACT_DIGEST"',
            '--release-backend "$RELEASE_BACKEND"',
            '--release-bundle "$RELEASE_BUNDLE"',
            '--output-dir "$RUNNER_TEMP"',
        ):
            with self.subTest(argument=argument):
                self.assertIn(argument, self.block)

    def test_env_exports_quarantine_refs_not_helper_release_refs(self) -> None:
        self.assertIn('handle.write(f"BACKEND_DIGEST={backend_digest}', self.block)
        self.assertIn('handle.write(f"ARTIFACT_DIGEST={artifact_digest}', self.block)
        self.assertIn("BACKEND_REF={os.environ['QUAR_BACKEND']}@", self.block)
        self.assertIn("ARTIFACT_REF={os.environ['QUAR_BUNDLE']}@", self.block)
        self.assertIn("CANONICAL_MANIFEST={result['CANONICAL_MANIFEST']}", self.block)
        # Helper ARTIFACT_REF/BACKEND_REF are release pins for annotation
        # checks only; they must never become the quarantine verify env.
        self.assertNotIn("ARTIFACT_REF={result[", self.block)
        self.assertNotIn("BACKEND_REF={result[", self.block)
        self.assertNotIn("RELEASE_BACKEND_REF=", self.block)
        self.assertNotIn("RELEASE_ARTIFACT_REF=", self.block)


class ResumeSkipConditionTests(unittest.TestCase):
    def test_only_build_steps_are_conditional(self) -> None:
        workflow = _workflow()
        for name in (BACKEND_BUILD, BUNDLE_BUILD):
            with self.subTest(step=name):
                self.assertIn(RESUME_CONDITION, _step_block(workflow, name))

    def test_evidence_and_sign_steps_are_unconditional(self) -> None:
        workflow = _workflow()
        for name in (BACKEND_EVIDENCE, BACKEND_SIGN, BUNDLE_SIGN):
            with self.subTest(step=name):
                block = _step_block(workflow, name)
                self.assertNotIn("resume_preflight", block)
                self.assertIn("if: always()", block)

    def test_quarantine_verify_is_never_skipped(self) -> None:
        block = _step_block(_workflow(), QUARANTINE_VERIFY)
        self.assertNotIn("resume_preflight", block)
        self.assertIn('python3 "$CI_DIR/plugin_verify.py"', block)

    def test_promote_and_release_verify_stay_shared_and_unconditional(self) -> None:
        workflow = _workflow()
        promote = _step_block(workflow, PROMOTE)
        self.assertNotIn("resume_preflight", promote)
        self.assertIn("oras copy --recursive", promote)
        self.assertIn("cosign copy --only=sig,att", promote)
        self.assertIn("RELEASE_BACKEND_REF=$RELEASE_BACKEND@$BACKEND_DIGEST", promote)
        self.assertIn("RELEASE_ARTIFACT_REF=$RELEASE_BUNDLE@$ARTIFACT_DIGEST", promote)
        reverify = _step_block(workflow, RELEASE_VERIFY)
        self.assertNotIn("resume_preflight", reverify)
        self.assertIn('"$RELEASE_ARTIFACT_REF"', reverify)
        self.assertIn('"$RELEASE_BACKEND_REF"', reverify)

    def test_first_run_build_and_sign_commands_are_unchanged(self) -> None:
        workflow = _workflow()
        backend = _step_block(workflow, BACKEND_BUILD)
        self.assertIn("docker buildx build --push", backend)
        self.assertIn('"$QUAR_BACKEND:sha-$GITHUB_SHA"', backend)
        bundle = _step_block(workflow, BUNDLE_BUILD)
        self.assertIn("oras push", bundle)
        self.assertIn('"$QUAR_BUNDLE:sha-$GITHUB_SHA"', bundle)
        self.assertIn("--export-manifest", bundle)
        self.assertIn(
            'cosign_retry cosign sign --yes "$BACKEND_REF"',
            _step_block(workflow, BACKEND_SIGN),
        )
        self.assertIn(
            'cosign_retry cosign sign --yes "$ARTIFACT_REF"',
            _step_block(workflow, BUNDLE_SIGN),
        )


class ResumeCosignRetryTests(unittest.TestCase):
    """Bounded retries exist only on cosign sign/attest, never on copy/verify."""

    def test_retry_helper_is_bounded_and_set_e_safe(self) -> None:
        for name in (BACKEND_SIGN, BUNDLE_SIGN):
            with self.subTest(step=name):
                block = _step_block(_workflow(), name)
                self.assertEqual(block.count("cosign_retry() {"), 1)
                self.assertIn('until "$@"; rc=$?; [[ $rc -eq 0 || $attempt -ge 3 ]]; do', block)
                self.assertIn("local delay=$((attempt * 5))", block)
                self.assertIn('sleep "$delay"', block)
                self.assertIn('return "$rc"', block)

    def test_each_sign_and_attest_uses_retry_helper(self) -> None:
        backend = _step_block(_workflow(), BACKEND_SIGN)
        for expected in (
            'cosign_retry cosign sign --yes "$BACKEND_REF"',
            'cosign_retry cosign attest --yes --predicate "$RUNNER_TEMP/backend-sbom.json"',
            'cosign_retry cosign attest --yes --predicate "$RUNNER_TEMP/backend-provenance.json"',
        ):
            with self.subTest(step=BACKEND_SIGN, command=expected):
                self.assertIn(expected, backend)
        bundle = _step_block(_workflow(), BUNDLE_SIGN)
        for expected in (
            'cosign_retry cosign sign --yes "$ARTIFACT_REF"',
            'cosign_retry cosign attest --yes --predicate "$RUNNER_TEMP/bundle-sbom.json"',
            'cosign_retry cosign attest --yes --predicate "$RUNNER_TEMP/bundle-provenance.json"',
        ):
            with self.subTest(step=BUNDLE_SIGN, command=expected):
                self.assertIn(expected, bundle)

    def test_registry_copy_verify_and_evidence_have_no_cosign_retries(self) -> None:
        workflow = _workflow()
        for name in (BACKEND_EVIDENCE, QUARANTINE_VERIFY, PROMOTE, RELEASE_VERIFY):
            with self.subTest(step=name):
                block = _step_block(workflow, name)
                self.assertNotIn("cosign_retry", block)
                self.assertNotIn("sleep", block)
        promote = _step_block(workflow, PROMOTE)
        self.assertIn("cosign copy --only=sig,att", promote)
        self.assertNotIn("cosign_retry cosign copy", promote)


class ResumeDiscoveryTests(unittest.TestCase):
    def test_dry_run_discovery_includes_resume_tests(self) -> None:
        workflow = _workflow()
        self.assertIn("test_ci_plugin_resume.py", workflow)
        self.assertIn("for TEST_FILE in", workflow)

    def test_resume_helper_is_a_trusted_ci_script(self) -> None:
        self.assertTrue(RESUME_HELPER.is_file())
        workflow = _workflow()
        self.assertIn("plugin_quarantine_resume.py", workflow)
        self.assertIn('"$CI_DIR/plugin_quarantine_resume.py"', workflow)


def _signed_bundle(tmp: Path, release_backend: str, release_bundle: str) -> dict:
    """Minimal helper-valid bundle whose ONLY discriminator is the repo pair.

    Annotations are derived from the supplied release_backend, so the signed
    backend_image_ref check passes even for a malformed repository; rejection
    must therefore come from the fail-fast repository-pair validation.
    """
    backend_digest = "sha256:" + "b" * 64
    canonical = b'{"plugin_id":"demo","canonical":true}\n'
    config_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    manifest = {
        "schemaVersion": 2,
        "mediaType": builder.OCI_MANIFEST_MEDIA_TYPE,
        "config": {
            "mediaType": builder.PLUGIN_MANIFEST_MEDIA_TYPE,
            "digest": config_digest,
            "size": len(canonical),
        },
        "annotations": {
            builder.A_BACKEND_IMAGE_REF: f"{release_backend}@{backend_digest}",
            builder.A_BACKEND_DIGEST: backend_digest,
            builder.A_MANIFEST_DIGEST: config_digest,
        },
    }
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    (tmp / "bundle-manifest.json").write_bytes(raw)
    (tmp / "config-blob.json").write_bytes(canonical)
    (tmp / "local-canonical.json").write_bytes(canonical)
    return {
        "bundle_manifest_path": tmp / "bundle-manifest.json",
        "config_blob_path": tmp / "config-blob.json",
        "local_canonical_manifest_path": tmp / "local-canonical.json",
        "backend_digest": backend_digest,
        "bundle_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "release_backend": release_backend,
        "release_bundle": release_bundle,
        "raw": raw,
        "canonical": canonical,
    }


def _helper_kwargs(fixture: dict) -> dict:
    return {
        key: value for key, value in fixture.items() if key not in {"raw", "canonical"}
    }


class ResumeHelperRepositoryPairTests(unittest.TestCase):
    """Fail-fast repo-pair validation must reject BEFORE any output is staged."""

    def test_valid_pair_stages_expected_and_pushed_from_identical_raw_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _signed_bundle(Path(tmp), RELEASE_BACKEND_REPO, RELEASE_BUNDLE_REPO)
            out = Path(tmp) / "out"
            result = resume_helper.resume_verification_inputs(
                **_helper_kwargs(fixture), output_dir=out
            )
            # resumed expected/pushed are intentionally byte-identical: both
            # are the raw signed manifest, never a local re-serialization.
            # Integrity = sha256(raw bytes) == resolved bundle digest plus the
            # mandatory full quarantine AND release verify below, not a
            # cosmetic expected-vs-pushed diff.
            self.assertEqual((out / "oci-manifest.json").read_bytes(), fixture["raw"])
            self.assertEqual((out / "pushed-manifest.json").read_bytes(), fixture["raw"])
            self.assertEqual(
                (out / "resumed-canonical-manifest.json").read_bytes(),
                fixture["canonical"],
            )
            self.assertEqual(
                result["ARTIFACT_REF"],
                f"{RELEASE_BUNDLE_REPO}@{fixture['bundle_digest']}",
            )
            self.assertEqual(
                result["BACKEND_REF"],
                f"{RELEASE_BACKEND_REPO}@{fixture['backend_digest']}",
            )

    def test_release_backend_without_backend_suffix_is_rejected_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _signed_bundle(
                Path(tmp), "ghcr.io/ne-tort/plugins/demo", RELEASE_BUNDLE_REPO
            )
            out = Path(tmp) / "out"
            with self.assertRaises(resume_helper.ResumeBlocked):
                resume_helper.resume_verification_inputs(
                    **_helper_kwargs(fixture), output_dir=out
                )
            self.assertFalse(out.exists())

    def test_mismatched_release_bundle_repository_is_rejected_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _signed_bundle(
                Path(tmp), RELEASE_BACKEND_REPO, "ghcr.io/ne-tort/plugins/other/bundle"
            )
            out = Path(tmp) / "out"
            with self.assertRaises(resume_helper.ResumeBlocked):
                resume_helper.resume_verification_inputs(
                    **_helper_kwargs(fixture), output_dir=out
                )
            self.assertFalse(out.exists())


class ResumedVerifyIntegrityTests(unittest.TestCase):
    """Both verifies consume the intentionally identical raw staged manifest."""

    def test_both_verifies_consume_the_same_raw_staged_manifest(self) -> None:
        workflow = _workflow()
        for name in (QUARANTINE_VERIFY, RELEASE_VERIFY):
            with self.subTest(step=name):
                block = _step_block(workflow, name)
                self.assertIn('--manifest "$RUNNER_TEMP/oci-manifest.json"', block)
                self.assertIn('--pushed-manifest "$RUNNER_TEMP/pushed-manifest.json"', block)

    def test_quarantine_verify_uses_quarantine_refs_release_verify_uses_release_refs(self) -> None:
        workflow = _workflow()
        quarantine = _step_block(workflow, QUARANTINE_VERIFY)
        self.assertIn('"$ARTIFACT_REF"', quarantine)
        self.assertIn('"$BACKEND_REF"', quarantine)
        self.assertNotIn("$RELEASE_ARTIFACT_REF", quarantine)
        release = _step_block(workflow, RELEASE_VERIFY)
        self.assertIn('"$RELEASE_ARTIFACT_REF"', release)
        self.assertIn('"$RELEASE_BACKEND_REF"', release)


if __name__ == "__main__":
    unittest.main()
