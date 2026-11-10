"""Public-safe DTO for employee/public plugin bootstrap."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RpcOperationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}(\.[a-z][a-z0-9_-]{1,63})?$")
    capability_version: str = Field(pattern=r"^[0-9]+\.[0-9]+$")
    operation: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    http_method: str = Field(pattern=r"^(GET|POST)$")
    backend_path: str = Field(pattern=r"^/api/[A-Za-z0-9._/-]+$")
    public: bool = False


class PluginScopeResponse(BaseModel):
    """Server-resolved scope; optional fields are never client/default fallbacks."""

    model_config = ConfigDict(extra="forbid")

    company_id: str
    cabinet_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None


class PluginBootstrapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    manifest_version: str
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    static_origin: str
    plugin_shell_origin: str
    frontend_entry: str
    frontend_base_path: str
    route_path: str
    capabilities: list[str]
    rpc_operations: list[RpcOperationResponse]
    public_pages: list[str]
    scope: PluginScopeResponse
    grant_expires_at: datetime | None = None

__all__ = ["PluginBootstrapResponse", "PluginScopeResponse", "RpcOperationResponse"]
