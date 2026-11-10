"""Strict, bounded contracts for the trusted plugin runtime provider API.

Tenant identity is never accepted in request bodies: it comes from verified
PluginClaims. Request bodies contain only operation arguments and the
backend-digest assertion that is absent from capability claims. Responses are
closed models with metadata and digests only; no plaintext secret, DSN, token,
log body, command, or generic exec field is part of this contract.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from prodavan.application.plugins.transport.errors import PluginProblem, PluginTransportError

MAX_PROVIDER_REQUEST_BYTES = 16 * 1024
MAX_PROVIDER_RESPONSE_BYTES = 128 * 1024
MAX_PROVIDER_ARTIFACT_BYTES = 16 * 1024 * 1024
EMPTY_DIGEST = "sha256:" + "0" * 64

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$", min_length=71, max_length=71)]
SafeText = Annotated[str, Field(min_length=1, max_length=256)]
SecretRef = Annotated[str, Field(min_length=4, max_length=2048)]


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def _safe_strings(cls, value: Any) -> Any:
        if isinstance(value, str) and (
            value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("unsafe provider text")
        return value


class ProviderError(PluginTransportError):
    """Sanitized failure with an explicit HTTP mapping and safe machine code."""

    def __init__(self, status_code: int, code: str) -> None:
        if status_code not in {400, 401, 403, 404, 409, 413, 415, 422, 502, 503}:
            raise ValueError("unsupported provider error status")
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", code) is None:
            raise ValueError("unsafe provider error code")
        titles = {
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            409: "Conflict",
            413: "Payload Too Large",
            415: "Unsupported Media Type",
            422: "Validation Error",
            502: "Bad Gateway",
            503: "Service Unavailable",
        }
        self.code = code
        super().__init__(
            PluginProblem(
                status=status_code,
                code=code,
                title=titles[status_code],
                detail=code.lower(),
            )
        )


class ProviderRequest(ProviderModel):
    backend_digest: Digest
    request_id: SafeText


class EgressDeclarationRequest(ProviderRequest):
    pass


class PodActionRequest(ProviderRequest):
    job_id: SafeText
    target_ref: Annotated[str, Field(min_length=1, max_length=2048)]
    arguments_digest: Digest
    max_result_bytes: StrictInt = Field(gt=0, le=MAX_PROVIDER_ARTIFACT_BYTES)


class ArtifactMetadataRequest(PodActionRequest):
    pass


class SecretMetadataRequest(ProviderRequest):
    secret_ref: SecretRef


class ProviderIdentity(ProviderModel):
    company_id: SafeText
    plugin_id: SafeText
    plugin_instance_id: SafeText
    artifact_digest: Digest
    backend_digest: Digest
    grant_id: SafeText
    request_id: SafeText


class EgressRule(ProviderModel):
    host: Annotated[str, Field(min_length=1, max_length=253)]
    path_prefix: Annotated[str, Field(min_length=1, max_length=2048)]
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]


class EgressDeclarationResponse(ProviderIdentity):
    rules: list[EgressRule] = Field(min_length=1, max_length=256)
    max_egress_bytes: StrictInt = Field(gt=0, le=MAX_PROVIDER_ARTIFACT_BYTES)
    secret_refs: list[SecretRef] = Field(default_factory=list, max_length=32)

    @field_validator("rules")
    @classmethod
    def _unique_rules(cls, value: list[EgressRule]) -> list[EgressRule]:
        keys = [(item.host, item.path_prefix, item.method) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate egress rules")
        return value

    @field_validator("secret_refs")
    @classmethod
    def _unique_refs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate secret refs")
        return value


class PodActionIdentity(ProviderIdentity):
    project_id: SafeText
    pod_id: SafeText
    job_id: SafeText
    target_ref: Annotated[str, Field(min_length=1, max_length=2048)]
    arguments_digest: Digest
    max_result_bytes: StrictInt = Field(gt=0, le=MAX_PROVIDER_ARTIFACT_BYTES)


class ArtifactMetadataResponse(PodActionIdentity):
    action: Literal["read_artifact"] = "read_artifact"
    byte_count: StrictInt = Field(ge=0, le=MAX_PROVIDER_ARTIFACT_BYTES)


class StatusResult(ProviderModel):
    status: Literal["ready", "degraded", "unavailable"]
    ready: bool


class LogsResult(ProviderModel):
    job_id: SafeText
    content_digest: Digest
    byte_count: StrictInt = Field(ge=0, le=MAX_PROVIDER_ARTIFACT_BYTES)


class MessageResult(ProviderModel):
    job_id: SafeText
    arguments_digest: Digest
    accepted: Literal[True]


class StatusProviderResponse(PodActionIdentity):
    action: Literal["read_status"] = "read_status"
    result: StatusResult


class LogsProviderResponse(PodActionIdentity):
    action: Literal["read_logs"] = "read_logs"
    result: LogsResult


class MessageProviderResponse(PodActionIdentity):
    action: Literal["send_agent_message"] = "send_agent_message"
    result: MessageResult


class SecretMetadataResponse(ProviderIdentity):
    secret_ref: SecretRef
    value_digest: Digest
    metadata_digest: Digest


__all__ = [
    "ArtifactMetadataRequest",
    "ArtifactMetadataResponse",
    "EMPTY_DIGEST",
    "EgressDeclarationRequest",
    "EgressDeclarationResponse",
    "EgressRule",
    "LogsProviderResponse",
    "LogsResult",
    "MAX_PROVIDER_ARTIFACT_BYTES",
    "MAX_PROVIDER_REQUEST_BYTES",
    "MAX_PROVIDER_RESPONSE_BYTES",
    "MessageProviderResponse",
    "MessageResult",
    "PodActionIdentity",
    "PodActionRequest",
    "ProviderError",
    "ProviderIdentity",
    "ProviderModel",
    "ProviderRequest",
    "SecretMetadataRequest",
    "SecretMetadataResponse",
    "StatusProviderResponse",
    "StatusResult",
]
