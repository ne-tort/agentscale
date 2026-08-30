"""Map k8s exec infrastructure failures to AppError."""

from __future__ import annotations

from prodavan.domain.errors import AppError
from prodavan.infrastructure.k8s.errors import K8sError, K8sNotFoundError, PermanentK8sError, TransientK8sError


def _invalid_status_detail(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        body = getattr(response, "body", b"") or b""
        if body:
            try:
                import json

                message = json.loads(body).get("message")
                if message:
                    return str(message)[:500]
            except Exception:
                pass
        status_code = getattr(response, "status_code", None)
        if status_code:
            return f"kubernetes denied pod exec: HTTP {status_code}"
    return "kubernetes denied pod exec (RBAC or policy)"


def app_error_from_exec_failure(exc: BaseException) -> AppError:
    """Convert exec transport errors to RFC7807-friendly AppError."""
    try:
        from websockets.exceptions import InvalidStatus
    except ImportError:  # pragma: no cover
        InvalidStatus = ()  # type: ignore[misc, assignment]

    if isinstance(exc, InvalidStatus):
        status_code = int(getattr(getattr(exc, "response", None), "status_code", 502) or 502)
        if status_code == 403:
            return AppError(
                code="POD_EXEC_FORBIDDEN",
                title="Forbidden",
                status=403,
                detail=_invalid_status_detail(exc),
            )
        if status_code == 404:
            return AppError(
                code="POD_NOT_FOUND",
                title="Not Found",
                status=404,
                detail=_invalid_status_detail(exc),
            )
        return AppError(
            code="POD_EXEC_UNAVAILABLE",
            title="Bad Gateway",
            status=502,
            detail=f"kubernetes rejected exec handshake: HTTP {status_code}",
        )

    if isinstance(exc, K8sNotFoundError):
        return AppError(
            code="POD_NOT_FOUND",
            title="Not Found",
            status=404,
            detail=str(exc) or "pod not found in kubernetes",
        )
    if isinstance(exc, PermanentK8sError):
        return AppError(
            code="POD_EXEC_FORBIDDEN",
            title="Forbidden",
            status=403,
            detail=str(exc) or "kubernetes exec forbidden",
        )
    if isinstance(exc, TransientK8sError):
        return AppError(
            code="POD_EXEC_UNAVAILABLE",
            title="Bad Gateway",
            status=502,
            detail=str(exc) or "kubernetes exec unavailable",
        )
    if isinstance(exc, K8sError):
        return AppError(
            code="POD_EXEC_UNAVAILABLE",
            title="Bad Gateway",
            status=502,
            detail=str(exc) or "kubernetes exec failed",
        )
    if isinstance(exc, TimeoutError):
        return AppError(
            code="POD_EXEC_UNAVAILABLE",
            title="Bad Gateway",
            status=502,
            detail="kubernetes exec timed out",
        )
    try:
        from websockets.exceptions import ConnectionClosed
    except ImportError:  # pragma: no cover
        ConnectionClosed = ()  # type: ignore[misc, assignment]

    if isinstance(exc, ConnectionClosed):
        return AppError(
            code="POD_EXEC_UNAVAILABLE",
            title="Bad Gateway",
            status=502,
            detail="kubernetes exec connection closed",
        )
    return AppError(
        code="POD_EXEC_UNAVAILABLE",
        title="Bad Gateway",
        status=502,
        detail=str(exc) or "kubernetes exec failed",
    )
