"""RFC 7807 problem details + AppError mapping."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from prodavan.domain.errors import AppError


def problem_response(
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    trace_id: str | None = None,
    extra: dict | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"https://prodavan.dev/errors/{code}",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
        body["message"] = detail
    if trace_id:
        body["trace_id"] = trace_id
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json")


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return problem_response(
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        trace_id=_trace_id(request),
        extra=exc.extra or None,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return problem_response(
            status=exc.status_code,
            code=str(exc.detail["code"]),
            title=str(exc.detail.get("title", exc.detail["code"])),
            detail=str(exc.detail.get("message") or exc.detail.get("detail") or ""),
            trace_id=_trace_id(request),
        )

    code = "HTTP_ERROR"
    if exc.status_code == 404:
        code = "NOT_FOUND"
    elif exc.status_code == 403:
        code = "FORBIDDEN"
    elif exc.status_code == 401:
        code = "UNAUTHORIZED"
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return problem_response(
        status=exc.status_code, code=code, title=code, detail=detail, trace_id=_trace_id(request)
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return problem_response(
        status=422,
        code="VALIDATION_ERROR",
        title="Validation Error",
        detail="Invalid request",
        trace_id=_trace_id(request),
    )


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    return problem_response(
        status=409,
        code="CONFLICT",
        title="Conflict",
        detail="database integrity constraint violated",
        trace_id=_trace_id(request),
    )


def _trace_id(request: Request) -> str | None:
    """Read the per-request trace id set by TraceIdMiddleware (audit XCUT-P2a).

    Surfaces the trace id in problem responses so a client can quote it when
    debugging a 4xx/5xx without a separate distributed-tracing backend.
    """
    return getattr(request.state, "trace_id", None)


def register_exception_handlers(app) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
