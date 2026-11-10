"""Authenticated scoped provider routes mounted on the existing gateway router."""
from __future__ import annotations

import json
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ValidationError

from prodavan.application.plugins.runtime.provider_api import (
    ArtifactMetadataRequest,
    EgressDeclarationRequest,
    MAX_PROVIDER_REQUEST_BYTES,
    MAX_PROVIDER_RESPONSE_BYTES,
    PodActionRequest,
    ProviderError,
    ProviderModel,
    SecretMetadataRequest,
)
from prodavan.application.plugins.runtime.providers import (
    ScopedRuntimeProvider,
    get_scoped_runtime_provider,
)
from prodavan.application.plugins.transport.deps import PluginClaimsDep
from prodavan.application.plugins.transport.errors import (
    PluginProblem,
    plugin_problem_response,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_PROVIDER_BASE = "/capabilities/v1/runtime"


def _problem(request: Request, error: ProviderError) -> Response:
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
    return plugin_problem_response(
        PluginProblem(
            status=error.status_code,
            code=error.code,
            title=titles[error.status_code],
            detail=error.code.lower(),
        ),
        request,
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json_budget(raw: bytes) -> None:
    depth = 0
    commas = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                in_string = False
            continue
        if byte == 34:
            in_string = True
        elif byte in (123, 91):
            depth += 1
            if depth > 16:
                raise ProviderError(422, "PROVIDER_JSON_DEPTH_EXCEEDED")
        elif byte in (125, 93):
            depth -= 1
        elif byte == 44:
            commas += 1
            if commas > 512:
                raise ProviderError(422, "PROVIDER_JSON_NODE_LIMIT_EXCEEDED")


async def _typed_body(request: Request, model: type[_ModelT]) -> _ModelT:
    if request.query_params:
        raise ProviderError(422, "PROVIDER_QUERY_NOT_ALLOWED")
    media_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise ProviderError(415, "PROVIDER_JSON_REQUIRED")
    raw_length = request.headers.get("Content-Length")
    if raw_length is not None:
        if not raw_length.isascii() or not raw_length.isdigit():
            raise ProviderError(400, "PROVIDER_CONTENT_LENGTH_INVALID")
        if int(raw_length, 10) > MAX_PROVIDER_REQUEST_BYTES:
            raise ProviderError(413, "PROVIDER_REQUEST_TOO_LARGE")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_PROVIDER_REQUEST_BYTES:
            raise ProviderError(413, "PROVIDER_REQUEST_TOO_LARGE")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw:
        raise ProviderError(422, "PROVIDER_BODY_REQUIRED")
    _json_budget(raw)
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
        return model.model_validate(payload)
    except (ValidationError, UnicodeError, ValueError, TypeError):
        raise ProviderError(422, "PROVIDER_SCHEMA_MISMATCH") from None


def _success(request: Request, model: ProviderModel) -> Response:
    del request
    try:
        raw = json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ProviderError(503, "PROVIDER_RESPONSE_INVALID") from None
    if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
        raise ProviderError(503, "PROVIDER_RESPONSE_TOO_LARGE")
    return Response(content=raw, media_type="application/json", status_code=200)


async def _invoke(request: Request, call: Any) -> Response:
    try:
        model = await call()
        return _success(request, model)
    except ProviderError as exc:
        return _problem(request, exc)
    except Exception:
        return _problem(request, ProviderError(503, "RUNTIME_PROVIDER_UNAVAILABLE"))


def install_plugin_runtime_routes(router: APIRouter) -> APIRouter:
    """Install only fixed typed operations; no generic action/exec dispatch."""
    if getattr(router, "_scoped_runtime_routes_installed", False):
        return router
    setattr(router, "_scoped_runtime_routes_installed", True)

    @router.post(_PROVIDER_BASE + "/declarations/egress")
    async def runtime_egress_declaration(
        request: Request,
        claims: PluginClaimsDep,
        provider: ScopedRuntimeProvider = Depends(get_scoped_runtime_provider),
    ) -> Response:
        async def call() -> ProviderModel:
            body = await _typed_body(request, EgressDeclarationRequest)
            return await provider.egress_declaration(claims=claims, request=body)

        return await _invoke(request, call)

    @router.post(_PROVIDER_BASE + "/artifacts/metadata")
    async def runtime_artifact_metadata(
        request: Request,
        claims: PluginClaimsDep,
        provider: ScopedRuntimeProvider = Depends(get_scoped_runtime_provider),
    ) -> Response:
        async def call() -> ProviderModel:
            body = await _typed_body(request, ArtifactMetadataRequest)
            return await provider.artifact_metadata(claims=claims, request=body)

        return await _invoke(request, call)

    @router.post(_PROVIDER_BASE + "/secrets/metadata")
    async def runtime_secret_metadata(
        request: Request,
        claims: PluginClaimsDep,
        provider: ScopedRuntimeProvider = Depends(get_scoped_runtime_provider),
    ) -> Response:
        async def call() -> ProviderModel:
            body = await _typed_body(request, SecretMetadataRequest)
            return await provider.secret_metadata(claims=claims, request=body)

        return await _invoke(request, call)

    @router.post(_PROVIDER_BASE + "/pods/status")
    async def runtime_pod_status(
        request: Request,
        claims: PluginClaimsDep,
        provider: ScopedRuntimeProvider = Depends(get_scoped_runtime_provider),
    ) -> Response:
        async def call() -> ProviderModel:
            body = await _typed_body(request, PodActionRequest)
            return await provider.read_status(claims=claims, request=body)

        return await _invoke(request, call)

    @router.post(_PROVIDER_BASE + "/pods/logs")
    async def runtime_pod_logs(
        request: Request,
        claims: PluginClaimsDep,
        provider: ScopedRuntimeProvider = Depends(get_scoped_runtime_provider),
    ) -> Response:
        async def call() -> ProviderModel:
            body = await _typed_body(request, PodActionRequest)
            return await provider.read_logs(claims=claims, request=body)

        return await _invoke(request, call)

    @router.post(_PROVIDER_BASE + "/pods/messages")
    async def runtime_pod_message(
        request: Request,
        claims: PluginClaimsDep,
        provider: ScopedRuntimeProvider = Depends(get_scoped_runtime_provider),
    ) -> Response:
        async def call() -> ProviderModel:
            body = await _typed_body(request, PodActionRequest)
            return await provider.send_agent_message(claims=claims, request=body)

        return await _invoke(request, call)

    return router


__all__ = ["install_plugin_runtime_routes"]
