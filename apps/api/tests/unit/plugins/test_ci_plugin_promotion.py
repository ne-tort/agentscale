"""Offline regressions for quarantine -> release promotion in plugin CI.

Statically checks the promote step of plugin-artifacts.yml: ORAS recursive
referrer copies plus cosign --only=sig,att copies for tag-based .sig/.att
manifests, digest-qualified refs only, and the verify -> copy -> reverify ->
publish ordering. Reads repository files only; never executes cosign, ORAS,
Docker, git or any network command. Live promotion is intentionally unverified.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github" / "workflows" / "plugin-artifacts.yml").is_file():
            return parent
    raise RuntimeError("plugin CI repository root not found")


ROOT = _repo_root()
WORKFLOW = ROOT / ".github" / "workflows" / "plugin-artifacts.yml"
# Every existing stdlib unittest module under apps/api/tests/unit/plugins that
# the dry-run TEST_FILE loop must discover, exactly once each. Historical
# (trusted) trees may lack newer modules, which the [[ -f ]] guard tolerates.
# test_ci_plugin_artifacts.py is DELIBERATELY NOT in this list: it uses pytest
# fixtures/monkeypatch and runs under the ci-api "pytest tests/unit/" suite.
# It must never be added to the stdlib unittest discovery loop.
TEST_MODULES = (
    "test_ci_plugin_supply_chain.py",
    "test_ci_plugin_publication.py",
    "test_ci_plugin_network.py",
    "test_ci_plugin_archive_safety.py",
    "test_ci_plugin_promotion.py",
    "test_ci_plugin_resume.py",
    "test_ci_plugin_release_evidence.py",
)
PYTEST_ONLY_MODULE = "test_ci_plugin_artifacts.py"


def _step(text: str, anchor: str) -> str:
    start = text.index(anchor)
    following = text.find("\n      - name:", start + len(anchor))
    return text[start:] if following == -1 else text[start:following]


def _logical_commands(text: str) -> list[str]:
    """Fold backslash-continued shell lines; drop full-line comments."""
    commands: list[str] = []
    pending = ""
    for raw in text.splitlines():
        # Collapse internal runs of spaces too: the workflow aligns continued
        # arguments with wide padding, which must not affect comparisons.
        stripped = " ".join(raw.split())
        if stripped.startswith("#"):
            continue
        if not stripped:
            if pending:
                commands.append(pending.rstrip())
            pending = ""
            continue
        pending = f"{pending} {stripped}" if pending else stripped
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        commands.append(pending)
        pending = ""
    if pending:
        commands.append(pending.rstrip())
    return commands


class PromotionCopies(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.promote = _step(self.text, "- name: Promote verified backend")
        self.commands = _logical_commands(self.promote)

    def _cosign_copies(self) -> list[str]:
        return [cmd for cmd in self.commands if cmd.startswith("cosign copy")]

    def test_both_cosign_copies_use_only_sig_att_and_exact_digest_refs(self) -> None:
        copies = self._cosign_copies()
        self.assertEqual(len(copies), 2)
        expected = (
            'cosign copy --only=sig,att "$QUAR_BACKEND@$BACKEND_DIGEST" '
            '"$RELEASE_BACKEND@$BACKEND_DIGEST"',
            'cosign copy --only=sig,att "$QUAR_BUNDLE@$ARTIFACT_DIGEST" '
            '"$RELEASE_BUNDLE@$ARTIFACT_DIGEST"',
        )
        for command, ref_pair in zip(copies, expected):
            with self.subTest(command=command):
                self.assertEqual(command, ref_pair)

    def test_no_force_and_no_tag_only_references(self) -> None:
        for command in self.commands:
            with self.subTest(command=command[:120]):
                self.assertNotIn("--force", command)
        for command in self._cosign_copies():
            with self.subTest(command=command):
                # sha-$GITHUB_SHA tag refs are forbidden in promotion copies:
                # only digest-qualified quarantine -> release refs are allowed.
                self.assertNotIn(":sha-", command)
                self.assertRegex(command, r"@\$[A-Z_]+DIGEST\b")

    def test_oras_recursive_referrer_copies_are_preserved(self) -> None:
        self.assertIn(
            'oras copy --recursive "$QUAR_BACKEND@$BACKEND_DIGEST"', self.promote
        )
        self.assertIn(
            'oras copy --recursive "$QUAR_BUNDLE@$ARTIFACT_DIGEST"', self.promote
        )

    def test_oras_copies_run_before_cosign_copies(self) -> None:
        last_oras = self.promote.rindex("oras copy --recursive")
        first_cosign = self.promote.index("cosign copy")
        self.assertLess(last_oras, first_cosign)

    def test_digest_resolve_checks_follow_all_copies(self) -> None:
        last_copy = self.promote.rindex("cosign copy")
        backend_check = self.promote.index(
            'oras resolve "$RELEASE_BACKEND@$BACKEND_DIGEST"'
        )
        bundle_check = self.promote.index(
            'oras resolve "$RELEASE_BUNDLE@$ARTIFACT_DIGEST"'
        )
        self.assertLess(last_copy, backend_check)
        self.assertLess(last_copy, bundle_check)


class PromotionOrdering(unittest.TestCase):
    def test_verify_then_copy_then_reverify_then_publish(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        quarantine_verify = text.index("id: quarantine_verify")
        promote = text.index("id: promote")
        release_verify = text.index("id: release_verify")
        publication = text.index(
            "- name: Verify independent release evidence and publish through platform OIDC"
        )
        self.assertLess(quarantine_verify, promote)
        self.assertLess(promote, release_verify)
        self.assertLess(release_verify, publication)
        # The cosign promotion must live between the two verify steps, never
        # before quarantine verification or after release reverification.
        self.assertLess(quarantine_verify, text.index("cosign copy"))
        self.assertLess(text.rindex("cosign copy"), release_verify)


class DryRunTestDiscovery(unittest.TestCase):
    def test_offline_modules_are_discovered_with_safe_file_guard(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        step = _step(text, "- name: Run stdlib focused and module contract tests")
        loop = re.search(r"for TEST_FILE in ([^;]+); do", step)
        self.assertIsNotNone(loop)
        entries = loop.group(1).split()
        for module in TEST_MODULES:
            with self.subTest(module=module):
                self.assertEqual(entries.count(module), 1)
        # Historical/trusted trees may lack newer modules: the guard must skip
        # them instead of failing the whole dry-run.
        self.assertIn(
            'if [[ -f "$SOURCE_DIR/apps/api/tests/unit/plugins/$TEST_FILE" ]]', step
        )

    def test_pytest_only_module_is_never_in_the_stdlib_loop(self) -> None:
        # test_ci_plugin_artifacts.py relies on pytest fixtures and monkeypatch,
        # so "unittest discover" over it is not a stdlib-safe run. It is covered
        # by ci-api's "pytest tests/unit/" instead; keep it out of this loop on
        # purpose (a deliberate pin, not an accidental omission).
        artifacts = (ROOT / "apps/api/tests/unit/plugins" / PYTEST_ONLY_MODULE).read_text(
            encoding="utf-8"
        )
        self.assertIn("import pytest", artifacts)
        text = WORKFLOW.read_text(encoding="utf-8")
        step = _step(text, "- name: Run stdlib focused and module contract tests")
        loop = re.search(r"for TEST_FILE in ([^;]+); do", step)
        self.assertIsNotNone(loop)
        self.assertNotIn(PYTEST_ONLY_MODULE, loop.group(1))
        self.assertNotIn(PYTEST_ONLY_MODULE, set(TEST_MODULES))


if __name__ == "__main__":
    unittest.main()
