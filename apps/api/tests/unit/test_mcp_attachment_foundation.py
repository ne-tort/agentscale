"""Contract tests for the MCP attachment foundation.

These tests are intentionally not executed during the migration verification freeze.
"""

from dataclasses import FrozenInstanceError

import pytest

from prodavan.application.mcp_attachment import (
    McpAttachmentPolicy,
    McpDecision,
    McpPackage,
    McpPolicyCode,
    McpPolicyRequest,
)
from tests.fixtures.mcp_attachment import attachment, package, registration


def request(*, tool: str = "forecast", package_override: McpPackage | None = None) -> McpPolicyRequest:
    return McpPolicyRequest(
        project_id="project-1",
        profile_id="profile-1",
        server_id="weather",
        package=package_override or package(),
        tool=tool,
    )


def policy() -> McpAttachmentPolicy:
    return McpAttachmentPolicy((registration(),), (attachment(),))


def test_allowlist_and_package_are_captured_in_runtime_lock() -> None:
    result = policy().decide(request())
    lock = policy().lock(request(), lock_id="lock-1")

    assert result.decision is McpDecision.ALLOW
    assert result.code is McpPolicyCode.ALLOWED
    assert lock.tools == ("forecast",)
    assert lock.package.qualified_digest == f"sha256:{'a' * 64}"
    assert lock.attachment_id == "attachment-1"


def test_unknown_server_and_unallowlisted_tool_deny() -> None:
    unknown = McpPolicyRequest("project-1", "profile-1", "missing", package(), "forecast")
    assert policy().decide(unknown).code is McpPolicyCode.SERVER_NOT_REGISTERED
    assert policy().decide(request(tool="alerts")).code is McpPolicyCode.TOOL_NOT_ALLOWLISTED


def test_package_digest_or_version_mismatch_denies_and_cannot_lock() -> None:
    mismatched = McpPackage("acme-weather", "1.4.3", "b" * 64)
    result = policy().decide(request(package_override=mismatched))

    assert result.code is McpPolicyCode.PACKAGE_IDENTITY_MISMATCH
    with pytest.raises(PermissionError, match="package_identity_mismatch"):
        policy().lock(request(package_override=mismatched), lock_id="lock-1")


def test_package_digest_accepts_sha256_prefix_but_rejects_invalid_values() -> None:
    assert McpPackage("pkg", "1", f"sha256:{'A' * 64}").digest == "a" * 64
    with pytest.raises(ValueError, match="sha256"):
        McpPackage("pkg", "1", "not-a-digest")


def test_domain_objects_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        package().version = "2"  # type: ignore[misc]
