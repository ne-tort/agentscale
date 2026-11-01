"""Reusable MCP attachment fixtures for unit and integration tests."""

from prodavan.application.mcp_attachment import (
    McpAttachment,
    McpPackage,
    McpServerRegistration,
    McpToolAllowlist,
)

PACKAGE_DIGEST = "a" * 64


def package() -> McpPackage:
    return McpPackage(name="acme-weather", version="1.4.2", digest=PACKAGE_DIGEST)


def registration() -> McpServerRegistration:
    return McpServerRegistration(server_id="weather", package=package(), declared_tools=("forecast", "alerts"))


def attachment() -> McpAttachment:
    return McpAttachment(
        attachment_id="attachment-1",
        project_id="project-1",
        profile_id="profile-1",
        server_id="weather",
        package=package(),
        allowlist=McpToolAllowlist(server_id="weather", tools=("forecast",)),
    )
