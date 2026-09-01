"""Agent / pod-runtime domain error codes and helpers."""

from __future__ import annotations

from prodavan.domain.errors import AppError

POD_NOT_RUNNING = "POD_NOT_RUNNING"
PROJECT_PAUSED = "PROJECT_PAUSED"
AGENT_RUNTIME_UNAVAILABLE = "AGENT_RUNTIME_UNAVAILABLE"
AGENT_ADAPTER_DISABLED = "AGENT_ADAPTER_DISABLED"
AGENT_STUB_RESPONSE = "AGENT_STUB_RESPONSE"
AGENT_CREDENTIAL_MISSING = "AGENT_CREDENTIAL_MISSING"
BRIDGE_SEND_FAILED = "BRIDGE_SEND_FAILED"
BRIDGE_EMPTY_STREAM = "BRIDGE_EMPTY_STREAM"
BRIDGE_UNREACHABLE = "BRIDGE_UNREACHABLE"

_BRIDGE_ERROR_CODES = frozenset(
    {
        BRIDGE_SEND_FAILED,
        BRIDGE_EMPTY_STREAM,
        BRIDGE_UNREACHABLE,
        POD_NOT_RUNNING,
        AGENT_STUB_RESPONSE,
        AGENT_CREDENTIAL_MISSING,
    }
)


def pod_not_running(*, detail: str | None = None) -> AppError:
    return AppError(
        code=POD_NOT_RUNNING,
        title="Conflict",
        status=409,
        detail=detail or "pod is not running",
    )


def project_paused(*, detail: str | None = None) -> AppError:
    return AppError(
        code=PROJECT_PAUSED,
        title="Conflict",
        status=409,
        detail=detail or "project is paused",
    )


def agent_runtime_unavailable(*, detail: str | None = None) -> AppError:
    return AppError(
        code=AGENT_RUNTIME_UNAVAILABLE,
        title="Service Unavailable",
        status=503,
        detail=detail or "agent runtime is unavailable",
    )


def agent_adapter_disabled(*, detail: str | None = None) -> AppError:
    return AppError(
        code=AGENT_ADAPTER_DISABLED,
        title="Not Implemented",
        status=501,
        detail=detail or "in-process agent adapters are disabled",
    )


def app_error_from_bridge_event(data: dict) -> AppError:
    code = str(data.get("code") or AGENT_RUNTIME_UNAVAILABLE)
    message = str(data.get("message") or "agent runtime error")
    status = 503
    if code == POD_NOT_RUNNING:
        status = 409
    elif code == BRIDGE_SEND_FAILED:
        status = 502 if not data.get("retryable") else 503
    elif code in _BRIDGE_ERROR_CODES:
        status = 503
    return AppError(code=code, title="Agent Error", status=status, detail=message[:2000])
