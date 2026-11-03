"""Stable error codes for the canonical Resource API.

Every failure on the resource HTTP surface resolves to exactly one code below.
The codes are part of the public contract: a client switches on ``code`` and must
never parse ``detail``, which is a human-readable hint only. Adding a code is a
compatible change; renaming or reusing one is not.

``reason`` carries a machine-readable sub-code (for example the payload validator
reason from ``lifecycle_validation.validate_resource_payload``) and never carries
resource payload, secret material or provider output.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NoReturn

from prodavan.domain.errors import AppError


class ResourceApiErrorCode(StrEnum):
    """One stable code per rejected resource operation."""

    # identity / read
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_ALREADY_EXISTS = "RESOURCE_ALREADY_EXISTS"
    RESOURCE_SLUG_CONFLICT = "RESOURCE_SLUG_CONFLICT"
    RESOURCE_VERSION_NOT_FOUND = "RESOURCE_VERSION_NOT_FOUND"

    # scope
    RESOURCE_SCOPE_FORBIDDEN = "RESOURCE_SCOPE_FORBIDDEN"
    RESOURCE_SCOPE_UNSUPPORTED = "RESOURCE_SCOPE_UNSUPPORTED"
    RESOURCE_BINDING_CROSS_TENANT = "RESOURCE_BINDING_CROSS_TENANT"

    # payload / version integrity
    RESOURCE_PAYLOAD_INVALID = "RESOURCE_PAYLOAD_INVALID"
    RESOURCE_VERSION_IMMUTABLE = "RESOURCE_VERSION_IMMUTABLE"
    RESOURCE_VERSION_STALE = "RESOURCE_VERSION_STALE"
    RESOURCE_VERSION_NOT_PUBLISHED = "RESOURCE_VERSION_NOT_PUBLISHED"

    # lifecycle
    RESOURCE_LIFECYCLE_FORBIDDEN = "RESOURCE_LIFECYCLE_FORBIDDEN"
    RESOURCE_NOT_BINDABLE = "RESOURCE_NOT_BINDABLE"
    RESOURCE_NOT_MATERIALIZABLE = "RESOURCE_NOT_MATERIALIZABLE"

    # bindings
    RESOURCE_BINDING_NOT_FOUND = "RESOURCE_BINDING_NOT_FOUND"
    RESOURCE_BINDING_EXISTS = "RESOURCE_BINDING_EXISTS"
    RESOURCE_BINDING_TARGET_UNSUPPORTED = "RESOURCE_BINDING_TARGET_UNSUPPORTED"
    RESOURCE_WORKSPACE_PATH_UNSUPPORTED = "RESOURCE_WORKSPACE_PATH_UNSUPPORTED"

    # materialization
    RESOURCE_BINDING_MISSING = "RESOURCE_BINDING_MISSING"
    RESOURCE_PLAN_STALE = "RESOURCE_PLAN_STALE"
    RESOURCE_APPLY_BLOCKED = "RESOURCE_APPLY_BLOCKED"
    RESOURCE_APPLY_CONFLICT = "RESOURCE_APPLY_CONFLICT"
    RESOURCE_APPLY_UNAVAILABLE = "RESOURCE_APPLY_UNAVAILABLE"

    # transport-level guarantees
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    IDEMPOTENCY_REPLAY = "IDEMPOTENCY_REPLAY"
    CABINET_MISMATCH = "CABINET_MISMATCH"
    RESOURCE_STORE_UNAVAILABLE = "RESOURCE_STORE_UNAVAILABLE"


_STATUS_AND_TITLE: dict[ResourceApiErrorCode, tuple[int, str]] = {
    ResourceApiErrorCode.RESOURCE_NOT_FOUND: (404, "Resource not found"),
    ResourceApiErrorCode.RESOURCE_ALREADY_EXISTS: (409, "Resource already exists"),
    ResourceApiErrorCode.RESOURCE_SLUG_CONFLICT: (409, "Resource slug conflict"),
    ResourceApiErrorCode.RESOURCE_VERSION_NOT_FOUND: (404, "Resource version not found"),
    ResourceApiErrorCode.RESOURCE_SCOPE_FORBIDDEN: (404, "Resource scope not found"),
    ResourceApiErrorCode.RESOURCE_SCOPE_UNSUPPORTED: (422, "Resource scope unsupported"),
    ResourceApiErrorCode.RESOURCE_BINDING_CROSS_TENANT: (404, "Binding target not found"),
    ResourceApiErrorCode.RESOURCE_PAYLOAD_INVALID: (422, "Resource payload invalid"),
    ResourceApiErrorCode.RESOURCE_VERSION_IMMUTABLE: (409, "Resource version immutable"),
    ResourceApiErrorCode.RESOURCE_VERSION_STALE: (409, "Resource version stale"),
    ResourceApiErrorCode.RESOURCE_VERSION_NOT_PUBLISHED: (409, "Resource version not published"),
    ResourceApiErrorCode.RESOURCE_LIFECYCLE_FORBIDDEN: (409, "Resource lifecycle transition forbidden"),
    ResourceApiErrorCode.RESOURCE_NOT_BINDABLE: (409, "Resource cannot be bound"),
    ResourceApiErrorCode.RESOURCE_NOT_MATERIALIZABLE: (422, "Resource cannot be materialized"),
    ResourceApiErrorCode.RESOURCE_BINDING_NOT_FOUND: (404, "Resource binding not found"),
    ResourceApiErrorCode.RESOURCE_BINDING_EXISTS: (409, "Resource binding exists"),
    ResourceApiErrorCode.RESOURCE_BINDING_TARGET_UNSUPPORTED: (422, "Resource binding target unsupported"),
    ResourceApiErrorCode.RESOURCE_WORKSPACE_PATH_UNSUPPORTED: (422, "Per-binding workspace path unsupported"),
    ResourceApiErrorCode.RESOURCE_BINDING_MISSING: (409, "No resource binding for project"),
    ResourceApiErrorCode.RESOURCE_PLAN_STALE: (409, "Materialization plan is stale"),
    ResourceApiErrorCode.RESOURCE_APPLY_BLOCKED: (409, "Materialization apply blocked"),
    ResourceApiErrorCode.RESOURCE_APPLY_CONFLICT: (409, "Materialization apply conflict"),
    ResourceApiErrorCode.RESOURCE_APPLY_UNAVAILABLE: (503, "Materialization apply unavailable"),
    ResourceApiErrorCode.IDEMPOTENCY_KEY_REQUIRED: (400, "Idempotency key required"),
    ResourceApiErrorCode.IDEMPOTENCY_REPLAY: (409, "Idempotency key replayed with another request"),
    ResourceApiErrorCode.CABINET_MISMATCH: (400, "Cabinet scope mismatch"),
    ResourceApiErrorCode.RESOURCE_STORE_UNAVAILABLE: (503, "Resource store unavailable"),
}


class ResourceApiError(AppError):
    """``AppError`` carrying a stable resource code and an optional typed reason."""

    def __init__(
        self,
        code: ResourceApiErrorCode,
        *,
        detail: str | None = None,
        reason: str | None = None,
        extra: dict | None = None,
    ) -> None:
        status, title = _STATUS_AND_TITLE[code]
        payload: dict[str, object] = dict(extra or {})
        if reason:
            payload["reason"] = reason
        super().__init__(
            code=code.value,
            title=title,
            status=status,
            detail=detail,
            extra=payload or None,
        )

    @property
    def reason(self) -> str | None:
        value = self.extra.get("reason")
        return value if isinstance(value, str) else None


def fail(
    code: ResourceApiErrorCode,
    *,
    detail: str | None = None,
    reason: str | None = None,
    extra: dict | None = None,
) -> NoReturn:
    """Raise the typed resource error; never returns."""
    raise ResourceApiError(code, detail=detail, reason=reason, extra=extra)


class StoreConflict(Exception):
    """Store-level conflict; the service maps it to a stable resource code.

    Raised by a persistence adapter that lost a unique-constraint race or found
    a compare-and-set mismatch inside its own transaction. It never carries row
    content, so it is safe to surface as a reason code.
    """

    def __init__(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("conflict reason is required")
        self.reason = reason
        super().__init__(reason)


class StaleResourceState(StoreConflict):
    """Observed active version differs from the caller's expectation."""

    def __init__(self, reason: str = "active_version_stale") -> None:
        super().__init__(reason)


__all__ = [
    "ResourceApiError",
    "ResourceApiErrorCode",
    "StaleResourceState",
    "StoreConflict",
    "fail",
]
