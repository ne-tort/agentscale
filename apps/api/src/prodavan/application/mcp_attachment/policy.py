"""Pure MCP attachment policy evaluation and runtime lock creation."""

from __future__ import annotations

from collections.abc import Iterable

from prodavan.application.mcp_attachment.types import (
    McpAttachment,
    McpDecision,
    McpPolicyCode,
    McpPolicyDecision,
    McpPolicyRequest,
    McpRuntimeLockEntry,
    McpServerRegistration,
)


class McpAttachmentPolicy:
    """Evaluate attachments fail-closed and create locks only after approval."""

    def __init__(self, registrations: Iterable[McpServerRegistration], attachments: Iterable[McpAttachment]) -> None:
        self._registrations = {registration.server_id: registration for registration in registrations}
        self._attachments = {
            (attachment.project_id, attachment.profile_id, attachment.server_id): attachment
            for attachment in attachments
        }

    def decide(self, request: McpPolicyRequest) -> McpPolicyDecision:
        registration = self._registrations.get(request.server_id)
        if registration is None:
            return McpPolicyDecision(McpDecision.DENY, McpPolicyCode.SERVER_NOT_REGISTERED)
        attachment = self._attachments.get((request.project_id, request.profile_id, request.server_id))
        if attachment is None or not attachment.enabled:
            return McpPolicyDecision(McpDecision.DENY, McpPolicyCode.ATTACHMENT_NOT_FOUND)
        if attachment.package != request.package or registration.package != request.package:
            return McpPolicyDecision(McpDecision.DENY, McpPolicyCode.PACKAGE_IDENTITY_MISMATCH, attachment_id=attachment.attachment_id)
        if request.tool not in registration.declared_tools or not attachment.allowlist.allows(request.tool):
            return McpPolicyDecision(McpDecision.DENY, McpPolicyCode.TOOL_NOT_ALLOWLISTED, attachment_id=attachment.attachment_id)
        return McpPolicyDecision(McpDecision.ALLOW, McpPolicyCode.ALLOWED, attachment_id=attachment.attachment_id)

    def lock(self, request: McpPolicyRequest, *, lock_id: str) -> McpRuntimeLockEntry:
        decision = self.decide(request)
        if not decision.allowed:
            raise PermissionError(f"MCP attachment denied: {decision.code.value}")
        attachment = self._attachments[(request.project_id, request.profile_id, request.server_id)]
        return McpRuntimeLockEntry(
            lock_id=lock_id,
            attachment_id=attachment.attachment_id,
            project_id=attachment.project_id,
            profile_id=attachment.profile_id,
            server_id=attachment.server_id,
            package=attachment.package,
            tools=attachment.allowlist.tools,
        )
