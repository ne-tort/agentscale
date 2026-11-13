"""Offline network-contract regressions for plugin CI and the hello-plugin image.

Reads repository files only. Never runs Docker, apt, git, curl, SSH or any
network command. Live VM/build behavior is intentionally unverified; these
tests pin the declarative contracts only.
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
DOCKERFILE = (
    ROOT / "packages" / "plugins" / "hello-plugin" / "backend" / "Dockerfile"
)
GAI_PRECEDENCE = "precedence ::ffff:0:0/96 100"


def _logical_commands(text: str) -> list[str]:
    """Fold backslash-continued shell lines; drop full-line comments.

    Comment lines are skipped without flushing a pending continuation because
    both the workflow run blocks and Dockerfile RUN steps interleave comments
    with continued commands.
    """
    commands: list[str] = []
    pending = ""
    for raw in text.splitlines():
        stripped = raw.strip()
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


def _shell_commands(text: str, *, program: str) -> list[str]:
    """Return commands invoking a program after optional environment assignments.

    Diagnostics, echo banners, and comments may mention a tool without running
    it; those strings must not inherit the executable's required CLI flags.
    """
    invocation = re.compile(
        rf"^(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)*{re.escape(program)}(?:\s|$)"
    )
    return [command for command in _logical_commands(text) if invocation.match(command)]


def _unbounded_curl_commands(text: str) -> list[str]:
    return [
        command
        for command in _shell_commands(text, program="curl")
        if "--connect-timeout" not in command or "--max-time" not in command
    ]


class WorkflowNetworkBoundaries(unittest.TestCase):
    """Every network fetch in the workflow must fail fast, not hang."""

    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.commands = _logical_commands(self.text)

    def _commands_matching(self, pattern: str) -> list[str]:
        return [
            command for command in self.commands if re.search(pattern, command)
        ]

    def test_shell_command_extraction_ignores_echo_banners_and_comments(self) -> None:
        text = (
            "          echo '== curl -4/-6 https probes (bounded) =='\n"
            "          GIT_TERMINAL_PROMPT=0 curl --connect-timeout 5 --max-time 15 https://example.test\n"
            "          curl --connect-timeout 10 --max-time 120 --retry 2 https://example.test/tool\n"
        )
        self.assertEqual(
            _shell_commands(text, program="curl"),
            [
                "GIT_TERMINAL_PROMPT=0 curl --connect-timeout 5 --max-time 15 https://example.test",
                "curl --connect-timeout 10 --max-time 120 --retry 2 https://example.test/tool",
            ],
        )
        self.assertEqual(_shell_commands("echo curl --max-time 1\n", program="curl"), [])
        self.assertEqual(_shell_commands("# curl --max-time 1\n", program="curl"), [])

    def test_workflow_has_no_unbounded_curl(self) -> None:
        curls = _shell_commands(self.text, program="curl")
        self.assertGreaterEqual(len(curls), 4)  # probes + cosign + ORAS
        self.assertEqual(_unbounded_curl_commands(self.text), [])

    def test_unbounded_curl_is_rejected_without_treating_echo_as_a_command(self) -> None:
        text = (
            "          echo '== curl -4/-6 https probes (bounded) =='\n"
            "          curl -fsSL \\\n"
            "            https://example.test/tool\n"
        )
        self.assertEqual(
            _shell_commands(text, program="curl"),
            ["curl -fsSL https://example.test/tool"],
        )
        self.assertEqual(
            _unbounded_curl_commands(text),
            ["curl -fsSL https://example.test/tool"],
        )

    def test_every_git_clone_is_bounded_and_non_interactive(self) -> None:
        clones = self._commands_matching(r"\bgit clone\b")
        self.assertGreaterEqual(len(clones), 4)
        for command in clones:
            with self.subTest(command=command[:120]):
                self.assertIn("GIT_HTTP_LOW_SPEED_LIMIT=", command)
                self.assertIn("GIT_HTTP_LOW_SPEED_TIME=", command)
                self.assertIn("GIT_TERMINAL_PROMPT=0", command)

    def test_every_blob_none_checkout_is_bounded_and_non_interactive(self) -> None:
        checkouts = self._commands_matching(r"\bgit -C\b.*\bcheckout --detach\b")
        self.assertEqual(len(checkouts), 4)
        for command in checkouts:
            with self.subTest(command=command[:120]):
                self.assertIn("GIT_HTTP_LOW_SPEED_LIMIT=1024", command)
                self.assertIn("GIT_HTTP_LOW_SPEED_TIME=30", command)
                self.assertIn("GIT_TERMINAL_PROMPT=0", command)

    def test_tool_downloads_keep_strict_tls_requirements(self) -> None:
        downloads = self._commands_matching(r"releases/download")
        self.assertGreaterEqual(len(downloads), 2)  # cosign and ORAS
        for command in downloads:
            with self.subTest(command=command[:120]):
                self.assertIn("--proto '=https'", command)
                self.assertIn("--tlsv1.2", command)


class DockerfileBuildNetwork(unittest.TestCase):
    """The build image itself must prefer IPv4 without disabling IPv6."""

    def setUp(self) -> None:
        self.text = DOCKERFILE.read_text(encoding="utf-8")
        self.base_stage = self.text[
            self.text.index("AS python-base") : self.text.index("AS builder")
        ]

    def test_ipv4_precedence_lands_in_base_stage_before_network_steps(self) -> None:
        # python-base is inherited by builder/liquibase/runtime, so one guarded
        # append here covers pip, both apt-get runs and the liquibase curl.
        self.assertIn(GAI_PRECEDENCE, self.base_stage)
        precedence_index = self.text.index(GAI_PRECEDENCE)
        self.assertLess(precedence_index, self.text.index("pip install"))
        self.assertLess(precedence_index, self.text.index("apt-get update"))

    def test_base_stage_append_is_guarded_and_download_free(self) -> None:
        # Idempotency guard: an already-active rule is kept, not duplicated.
        self.assertIn("grep -Eq", self.base_stage)
        self.assertIn(">> /etc/gai.conf", self.base_stage)
        # The base stage itself must not perform any network fetch.
        self.assertNotRegex(self.base_stage, r"\bcurl\b")
        self.assertNotIn("apt-get", self.base_stage)

    def test_ipv6_is_not_disabled(self) -> None:
        self.assertNotIn("disable_ipv6", self.text)
        self.assertNotIn("sysctl", self.text)

    def test_debian_apt_sources_stay_on_https(self) -> None:
        # The plain-HTTP egress block is handled by rewriting Debian mirrors
        # before apt-get update in every stage that installs packages.
        rewrites = self.text.count("s|http://deb.debian.org|https://deb.debian.org|g")
        self.assertGreaterEqual(rewrites, 2)


if __name__ == "__main__":
    unittest.main()
