"""POST /internal/plugin-artifacts — trusted GitHub Actions publication.

The router is intentionally NOT mounted by this task. The composition root must
inject a fully built PluginCiPublicationService at app.state attribute named by
PLUGIN_CI_PUBLICATION_SERVICE_STATE. Authentication here is the dedicated
GitHub Actions OIDC validator, never the global Keycloak employee validator.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from prodavan.application.plugins.artifacts.oci import parse_oci_ref

from prodavan.application.plugins.artifacts.publication import ArtifactPublicationCommand
from prodavan.application.plugins.artifacts.publication import PublicationErrorCode
from prodavan.application.plugins.artifacts.publication import PublicationEvidence
from prodavan.application.plugins.artifacts.publication import PublicationResponse

PLUGIN_CI_PUBLICATION_SERVICE_STATE = "plugin_ci_publication_service"
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_PLUGIN_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
_SOURCE_ID_PATTERN = r"^\S{1,80}$"

router = APIRouter(tags=["internal-plugin-artifacts"])


class PluginArtifactEvidenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_verified: bool
    csp_verified: bool
    sbom_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    provenance_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)


class PluginArtifactPublicationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=_SOURCE_ID_PATTERN, max_length=80)
    plugin_id: str = Field(pattern=_PLUGIN_ID_PATTERN, max_length=160)
    version: str = Field(min_length=1, max_length=128)
    bundle_ref: str = Field(min_length=3, max_length=2048)
    bundle_digest: str = Field(pattern=_DIGEST_PATTERN)
    backend_ref: str = Field(min_length=3, max_length=2048)
    backend_digest: str = Field(pattern=_DIGEST_PATTERN)
    manifest: dict[str, Any]
    commit_sha: str = Field(pattern=_SHA1_PATTERN)
    ref: str = Field(min_length=1, max_length=512)
    run_url: str = Field(min_length=1, max_length=2048)
    evidence: PluginArtifactEvidenceBody

    @field_validator("bundle_ref", "backend_ref")
    @classmethod
    def _digest_pinned_refs(cls, value: str) -> str:
        # This also rejects :latest and every mutable/tag-only OCI reference.
        parse_oci_ref(value)
        return value

    @model_validator(mode="after")
    def _refs_match_digests(self) -> PluginArtifactPublicationBody:
        if parse_oci_ref(self.bundle_ref).digest != self.bundle_digest:
            raise ValueError("bundle_ref digest differs from bundle_digest")
        if parse_oci_ref(self.backend_ref).digest != self.backend_digest:
            raise ValueError("backend_ref digest differs from backend_digest")
        return self

    def to_command(self) -> ArtifactPublicationCommand:
        return ArtifactPublicationCommand(
            source_id=self.source_id,
            plugin_id=self.plugin_id,
            version=self.version,
            bundle_ref=self.bundle_ref,
            bundle_digest=self.bundle_digest,
            backend_ref=self.backend_ref,
            backend_digest=self.backend_digest,
            manifest=self.manifest,
            commit_sha=self.commit_sha,
            ref=self.ref,
            run_url=self.run_url,
            evidence=PublicationEvidence(
                scan_verified=self.evidence.scan_verified,
                csp_verified=self.evidence.csp_verified,
                sbom_digest=self.evidence.sbom_digest,
                provenance_digest=self.evidence.provenance_digest,
            ),
        )


class PluginArtifactPublicationResponse(BaseModel):
    status: Literal["PUBLISHED", "QUARANTINED"]
    code: str
    plugin_id: str
    version: str
    bundle_digest: str
    backend_digest: str
    source_id: str

    @classmethod
    def from_result(cls, result: PublicationResponse) -> PluginArtifactPublicationResponse:
        return cls(
            status=result.status,
            code=result.code,
            plugin_id=result.plugin_id,
            version=result.version,
            bundle_digest=result.bundle_digest,
            backend_digest=result.backend_digest,
            source_id=result.source_id,
        )


def get_plugin_artifact_publication_service(request: Request) -> object | None:
    """Composition seam only; no global service instance is created on import."""
    return getattr(request.app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE, None)


def _quarantine(
    body: PluginArtifactPublicationBody,
    *,
    code: str,
) -> PluginArtifactPublicationResponse:
    return PluginArtifactPublicationResponse(
        status="QUARANTINED",
        code=code,
        plugin_id=body.plugin_id,
        version=body.version,
        bundle_digest=body.bundle_digest,
        backend_digest=body.backend_digest,
        source_id=body.source_id,
    )


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator:
        return None
    if not token or token != token.strip() or any(char.isspace() for char in token):
        return None
    return token


_UNAVAILABLE_CODES = frozenset(
    {
        PublicationErrorCode.SOURCE_REPOSITORY_UNAVAILABLE.value,
        PublicationErrorCode.ARTIFACT_LOADER_UNAVAILABLE.value,
        PublicationErrorCode.REGISTRY_UNAVAILABLE.value,
        PublicationErrorCode.OIDC_VALIDATION_UNAVAILABLE.value,
    }
)


@router.post(
    "/internal/plugin-artifacts",
    response_model=PluginArtifactPublicationResponse,
    status_code=201,
)
async def publish_plugin_artifact(
    response: Response,
    body: PluginArtifactPublicationBody,
    service: Annotated[object | None, Depends(get_plugin_artifact_publication_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> PluginArtifactPublicationResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        command = body.to_command()
    except (TypeError, ValueError):
        response.status_code = 422
        return _quarantine(body, code=PublicationErrorCode.INPUT_INVALID.value)

    token = _bearer_token(authorization)
    if token is None:
        response.status_code = 401
        return _quarantine(body, code="OIDC_TOKEN_MISSING")
    publish = getattr(service, "publish", None)
    if not callable(publish):
        response.status_code = 503
        return _quarantine(body, code="PUBLICATION_INTEGRATION_UNAVAILABLE")

    try:
        result = await publish(command, oidc_token=token)
    except Exception:  # noqa: BLE001 — never leak integration details or URLs
        response.status_code = 503
        return _quarantine(body, code="PUBLICATION_INTEGRATION_UNAVAILABLE")
    if result.status == "PUBLISHED":
        response.status_code = 201
    elif result.code in _UNAVAILABLE_CODES:
        response.status_code = 503
    elif result.code.startswith("OIDC_"):
        response.status_code = 401
    elif result.code == PublicationErrorCode.REGISTRY_CONFLICT.value:
        response.status_code = 409
    else:
        response.status_code = 422
    return PluginArtifactPublicationResponse.from_result(result)


__all__ = [
    "PLUGIN_CI_PUBLICATION_SERVICE_STATE",
    "PluginArtifactEvidenceBody",
    "PluginArtifactPublicationBody",
    "PluginArtifactPublicationResponse",
    "get_plugin_artifact_publication_service",
    "publish_plugin_artifact",
    "router",
]
