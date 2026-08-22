"""RFC 7807 problem details + AppError mapping."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from prodavan.domain.errors import AppError


def problem_response(
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    trace_id: str | None = None,
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
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json")


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return problem_response(
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
    )


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return problem_response(
            status=exc.status_code,
            code=str(exc.detail["code"]),
            title=str(exc.detail.get("title", exc.detail["code"])),
            detail=str(exc.detail.get("message") or exc.detail.get("detail") or ""),
        )

    code = "HTTP_ERROR"
    if exc.status_code == 404:
        code = "NOT_FOUND"
    elif exc.status_code == 403:
        code = "FORBIDDEN"
    elif exc.status_code == 401:
        code = "UNAUTHORIZED"
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return problem_response(status=exc.status_code, code=code, title=code, detail=detail)


async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return problem_response(
        status=422,
        code="VALIDATION_ERROR",
        title="Validation Error",
        detail="Invalid request",
    )


def register_exception_handlers(app) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
