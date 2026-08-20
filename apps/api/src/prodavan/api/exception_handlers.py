"""RFC 7807 problem details."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_FIELD_LABELS_RU = {
    "email": "email",
    "password": "пароль",
    "login_id": "ID компании",
    "company_name": "название компании",
    "contact_person": "контактное лицо",
    "phone": "телефон",
    "current_password": "текущий пароль",
    "new_password": "новый пароль",
}


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


def _humanize_validation_error(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Некорректные данные запроса"
    first = errors[0]
    loc = [str(p) for p in first.get("loc", ()) if p not in {"body", "query", "path"}]
    field = loc[-1] if loc else None
    label = _FIELD_LABELS_RU.get(field or "", field or "поле")
    err_type = str(first.get("type", ""))
    if "email" in err_type or field == "email":
        return f"Некорректный {label}"
    if err_type in {"missing", "value_error.missing"}:
        return f"Укажите {label}"
    if "string_too_short" in err_type or "min_length" in err_type:
        return f"Слишком короткое значение: {label}"
    if "string_pattern" in err_type or "pattern" in err_type:
        return f"Недопустимый формат: {label}"
    msg = str(first.get("msg", "")).strip()
    if msg and not msg.startswith("Value error"):
        return f"{label}: {msg}"
    return f"Некорректное значение: {label}"


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return problem_response(
            status=exc.status_code,
            code=str(exc.detail["code"]),
            title=str(exc.detail["code"]),
            detail=str(exc.detail.get("message", "")),
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
        detail=_humanize_validation_error(exc),
    )


def register_exception_handlers(app) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
