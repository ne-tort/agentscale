"""Durable, idempotent registration of typed gateway-only MCP tools."""
from __future__ import annotations

from typing import Any, Protocol

from prodavan.application.plugins.capabilities.contracts import (
    McpInvokeRequest,
    McpInvokeResponse,
    McpRegisterRequest,
    McpRegisterResponse,
)
from ._shared import (
    CloseOnce,
    CapabilityAccessDenied,
    CapabilityBackendError,
    CapabilityConfigurationError,
    usage,
    await_io,
    canonical_digest,
    canonical_json,
    plugin_id,
    request_envelope,
)


class McpRegistrationStore(Protocol):
    async def get(
        self, *, company_id: str, plugin_id: str, plugin_instance_id: str, server_id: str
    ) -> str | None: ...

    async def put_if_absent(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        server_id: str,
        definitions: str,
    ) -> bool: ...


def digest_bound_server_id(server_id: str, artifact_digest: str) -> str:
    """Namespace one logical server by immutable artifact without changing Redis."""
    return f"{len(server_id.encode('utf-8'))}:{server_id}:{artifact_digest}"


class McpBrokerPort(Protocol):
    async def invoke(self, request: McpInvokeRequest) -> McpInvokeResponse: ...


class DurableMcpAdapter(CloseOnce):
    def __init__(
        self,
        *,
        store: McpRegistrationStore,
        max_tools: int = 64,
        timeout_seconds: float = 3.0,
        owns_store: bool = False,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            owned_resources=(store,) if owns_store else (),
        )
        for method in ("get", "put_if_absent"):
            if not callable(getattr(store, method, None)):
                raise CapabilityConfigurationError("mcp_durable_store_required")
        if not 1 <= max_tools <= 256:
            raise CapabilityConfigurationError("mcp_tool_cap_invalid")
        self._store = store
        self._max_tools = max_tools
        self._broker: McpBrokerPort | None = None

    def attach_broker(self, broker: McpBrokerPort) -> None:
        """Attach one manifest-scoped backend broker during composition."""
        self._ensure_open()
        if not callable(getattr(broker, "invoke", None)):
            raise CapabilityConfigurationError("mcp_broker_required")
        if self._broker is not None and self._broker is not broker:
            raise CapabilityConfigurationError("mcp_broker_already_attached")
        self._broker = broker

    @property
    def registration_store(self) -> McpRegistrationStore:
        """The same durable reader used by downstream discovery; never a second registry."""
        return self._store

    @staticmethod
    def _definitions(request: McpRegisterRequest) -> str:
        return canonical_json([
            {
                "tool_name": tool.tool_name,
                "tool_version": tool.tool_version,
                "capability": tool.capability.value,
                "operation": tool.operation,
                "input_schema_digest": tool.input_schema_digest,
            }
            for tool in request.tools
        ]).decode("utf-8")

    def quota_usage(self, request: McpRegisterRequest | McpInvokeRequest) -> Any:
        if isinstance(request, McpRegisterRequest):
            return usage(
                rows=len(request.tools),
                bytes=len(self._definitions(request).encode("utf-8")),
                keys=1,
            )
        if isinstance(request, McpInvokeRequest):
            arguments_bytes = len(request.arguments_json.encode("utf-8"))
            response_bound = getattr(self._broker, "_max_response_bytes", 64 * 1024)
            return usage(
                rows=1,
                bytes=arguments_bytes,
                keys=1,
                egress_bytes=arguments_bytes + int(response_bound),
            )
        raise CapabilityAccessDenied("typed_request_required")

    async def register(self, request: McpRegisterRequest) -> McpRegisterResponse:
        envelope = request_envelope(request, McpRegisterRequest)
        self._ensure_open()
        if len(request.tools) > self._max_tools:
            raise CapabilityAccessDenied("mcp_tool_cap_exceeded")
        for tool in request.tools:
            if tool.capability.value != "mcp" or tool.operation != "mcp.invoke":
                raise CapabilityAccessDenied("mcp_privilege_remap_denied")
        tenant = {
            "company_id": envelope.company_id,
            "plugin_id": plugin_id(envelope),
            "plugin_instance_id": envelope.plugin_instance_id,
            # Legacy registrations had no digest scope and are intentionally not
            # discovered. Re-registering the same artifact remains idempotent;
            # a new artifact gets a new registration scope and cannot reuse stale tools.
            "server_id": digest_bound_server_id(
                request.server_id, envelope.artifact_digest
            ),
        }
        definitions = self._definitions(request)
        if len(definitions.encode("utf-8")) > 256 * 1024:
            raise CapabilityAccessDenied("mcp_definitions_too_large")
        existing = await await_io(
            self._store.get(**tenant),
            timeout=self._timeout_seconds,
            code="mcp_registry_read_failed",
        )
        if existing is None:
            created = await await_io(
                self._store.put_if_absent(**tenant, definitions=definitions),
                timeout=self._timeout_seconds,
                code="mcp_registry_write_failed",
            )
            if created not in (True, False):
                raise CapabilityBackendError("mcp_registry_write_not_confirmed")
            # False can be an idempotent concurrent insert. Always re-read and
            # compare instead of overwriting a different definition.
            existing = await await_io(
                self._store.get(**tenant),
                timeout=self._timeout_seconds,
                code="mcp_registry_verify_failed",
            )
        if existing != definitions:
            raise CapabilityAccessDenied("mcp_registration_conflict")
        return McpRegisterResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest({**tenant, "definitions": definitions}),
            server_id=request.server_id,
            registered=len(request.tools),
        )

    async def invoke(self, request: McpInvokeRequest) -> McpInvokeResponse:
        request_envelope(request, McpInvokeRequest)
        self._ensure_open()
        if self._broker is None:
            raise CapabilityConfigurationError("mcp_broker_required")
        response = await await_io(
            self._broker.invoke(request),
            timeout=max(self._timeout_seconds, float(getattr(
                self._broker, "timeout_seconds", self._timeout_seconds
            ))),
            code="mcp_broker_failed",
        )
        if not isinstance(response, McpInvokeResponse):
            raise CapabilityBackendError("mcp_broker_response_invalid")
        return response


__all__ = [
    "DurableMcpAdapter", "McpBrokerPort", "McpRegistrationStore",
    "digest_bound_server_id",
]
