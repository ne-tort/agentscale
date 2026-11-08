"""Маппинг доменных ошибок plugin-surface в HTTP ``problem+json`` (задача 04).

Источники решений:
  * v4 §4.1/§4.4/§11 (кр. 10, 12, 17), ``docs/migration/plugin-migration-plan.md`` §0.5;
  * ``application/plugins/identity`` (задача 02) — ``PluginTokenError`` с ``code``;
  * ``application/plugins/capabilities`` (задача 03) — ``GatewayDecision`` с
    ``.decision`` / ``.code`` (deny НЕ бросает исключение, а возвращает решение);
  * ``api/exception_handlers.py`` — формат RFC 7807, который использует родитель.

Fail-closed: любая неопределённость (нет сервиса, нет keyring, неизвестный код
решения) трактуется как отказ, а не как «разрешить». Коды — стабильные
machine-readable строки; тела ошибок НЕ содержат traceback, токенов, SQL, PII.

Два канала маппинга:
  1. ``problem_from_exception`` — исключения (в т.ч. ``PluginTokenError``);
  2. ``problem_from_decision``  — штатные deny-решения ``PluginCapabilityService``.
     Это важно: квота (429) и отзыв grant'а (403) приходят как ``result.code``,
     а не как exception, иначе они «теряются» и превращаются в 200/500.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"

# Поля, превращающие typed контракт в generic row/action dispatch (ADR-002,
# v4 §4.3, §13.1). Запрещены на границе HTTP-тел; проверяются в bodies.py.
GENERIC_DISPATCH_FIELDS: frozenset[str] = frozenset(
    {"sql", "command", "args", "raw", "params", "payload", "script", "statement"}
)


@dataclass(frozen=True, slots=True)
class PluginProblem:
    """Типизированная HTTP-ошибка plugin-surface."""

    status: int
    code: str
    title: str
    detail: str


class PluginTransportError(StarletteHTTPException):
    """Ошибка транспорта плагинов.

    Наследник ``HTTPException`` намеренно: если родитель подключит роутер в
    приложение БЕЗ наших маршрутных обработчиков, Starlette всё равно вернёт
    корректный статус, а ``register_exception_handlers`` (``main.py``) превратит
    ``detail`` в problem+json с нашим ``code``. Штатно же ошибку перехватывает
    ``PluginRoute`` и сам формирует problem+json — роутер самодостаточен.
    """

    def __init__(self, problem: PluginProblem) -> None:
        super().__init__(
            status_code=problem.status,
            detail={
                "code": problem.code,
                "title": problem.title,
                "message": problem.detail,
            },
        )
        self.problem = problem


def plugin_problem_response(problem: PluginProblem, request: Request | None = None) -> JSONResponse:
    """Собрать RFC 7807 ``application/problem+json`` с обязательным ``code``."""
    body: dict[str, object] = {
        "type": f"https://prodavan.dev/errors/{problem.code}",
        "title": problem.title,
        "status": problem.status,
        "code": problem.code,
        "detail": problem.detail,
        "message": problem.detail,
    }
    if request is not None:
        trace_id = getattr(request.state, "trace_id", None)
        if trace_id:
            body["trace_id"] = trace_id
    return JSONResponse(status_code=problem.status, content=body, media_type=PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Канонизация кодов. Источники используют разные регистры: identity —
# ``QUOTA_EXCEEDED``/``FORBIDDEN_SURFACE`` (верхний), capability_gateway_service —
# ``quota_exceeded`` (нижний), QuotaReason — ``QUOTA_EXCEEDED``. Сравниваем по
# нормализованной форме, чтобы не размножать ветвления.
# ---------------------------------------------------------------------------


def normalize_code(value: Any) -> str:
    """Привести Enum/строку к единому виду: lower + '_' вместо '-' / '.' / пробела."""
    if value is None:
        return ""
    raw = value.value if isinstance(value, Enum) else value
    text = str(raw).strip().lower()
    for char in ("-", ".", " ", "/", ":"):
        text = text.replace(char, "_")
    return text


# Нормализованный код -> (HTTP-статус, канонический код, заголовок).
_CODE_MAP: dict[str, tuple[int, str, str]] = {
    # Квота — 429 (acceptance №10, §4.4).
    "quota_exceeded": (429, "QUOTA_EXCEEDED", "Quota Exceeded"),
    # Отзыв grant'а — 403 (acceptance №12).
    "grant_revoked": (403, "GRANT_REVOKED", "Grant Revoked"),
    "revoked": (403, "GRANT_REVOKED", "Grant Revoked"),
    "revoked_grant": (403, "GRANT_REVOKED", "Grant Revoked"),
    # Адаптер / capability — 503 (fail-closed: нет интеграции ≠ allow).
    "adapter_missing": (503, "ADAPTER_MISSING", "Adapter Missing"),
    "adapter_failed": (503, "ADAPTER_FAILED", "Adapter Failed"),
    "capability_unknown": (503, "CAPABILITY_UNKNOWN", "Capability Unknown"),
    "quota_unavailable": (503, "QUOTA_UNAVAILABLE", "Quota Unavailable"),
    "service_unavailable": (503, "SERVICE_UNAVAILABLE", "Service Unavailable"),
    "identity_not_configured": (503, "IDENTITY_NOT_CONFIGURED", "Identity Not Configured"),
    "revocation_not_configured": (503, "REVOCATION_NOT_CONFIGURED", "Revocation Not Configured"),
    # Валидация typed контрактов — 422.
    "invalid_request": (422, "VALIDATION_ERROR", "Validation Error"),
    "validation_error": (422, "VALIDATION_ERROR", "Validation Error"),
    "forbidden_generic_dispatch": (422, "VALIDATION_ERROR", "Validation Error"),
    "quota_key_invalid": (422, "VALIDATION_ERROR", "Validation Error"),
    "typed_contract_error": (422, "VALIDATION_ERROR", "Validation Error"),
    # Identity — 401/403.
    "identity_denied": (401, "UNAUTHORIZED", "Unauthorized"),
    "unauthorized": (401, "UNAUTHORIZED", "Unauthorized"),
    "invalid_signature": (401, "INVALID_SIGNATURE", "Invalid Signature"),
    "wrong_audience": (401, "WRONG_AUDIENCE", "Wrong Audience"),
    "wrong_typ": (401, "WRONG_TYP", "Wrong Token Type"),
    "wrong_issuer": (401, "WRONG_ISSUER", "Wrong Issuer"),
    "expired": (401, "EXPIRED", "Token Expired"),
    "malformed": (401, "MALFORMED", "Malformed Token"),
    "missing_claim": (401, "MISSING_CLAIM", "Missing Claim"),
    "unknown_kid": (401, "UNKNOWN_KID", "Unknown Key Id"),
    "forbidden_surface": (403, "FORBIDDEN_SURFACE", "Forbidden Surface"),
    "forbidden": (403, "FORBIDDEN", "Forbidden"),
    "scope_denied": (403, "FORBIDDEN", "Forbidden"),
    "capability_not_requested": (403, "FORBIDDEN", "Forbidden"),
    "grant_missing": (403, "FORBIDDEN", "Forbidden"),
    "grant_expired": (403, "FORBIDDEN", "Forbidden"),
    "grant_not_yet_issued": (403, "FORBIDDEN", "Forbidden"),
    "allowed": (200, "ALLOWED", "Allowed"),
}


def problem_from_code(code: Any, detail: str) -> PluginProblem | None:
    """Сопоставить код решения/исключения с HTTP-проблемой. ``None`` = код неизвестен."""
    normalized = normalize_code(code)
    if not normalized:
        return None
    mapped = _CODE_MAP.get(normalized)
    if mapped is None:
        return None
    status, canonical, title = mapped
    return PluginProblem(status=status, code=canonical, title=title, detail=detail)


def problem_from_decision(decision: Any, code: Any, *, detail: str) -> PluginProblem | None:
    """Маппинг штатного результата ``PluginCapabilityService.invoke``.

    Возвращает ``None`` только для однозначного allow. Несовпадение
    ``decision='allow'`` с кодом отказа (напр. ``QUOTA_EXCEEDED``) трактуется как
    отказ: fail-closed важнее «доверия флагу».
    """
    normalized_decision = normalize_code(decision)
    normalized_code = normalize_code(code)
    is_allow = normalized_decision in {"allow", "allowed", "ok", "true", "1"}
    code_is_clean = normalized_code in {"", "allowed", "allow", "ok"}
    if is_allow and code_is_clean:
        return None

    if normalized_code:
        mapped = problem_from_code(normalized_code, detail)
        if mapped is not None and mapped.status >= 400:
            return mapped

    if normalized_decision in {"deny", "denied", "false", "0"}:
        return PluginProblem(403, "DECISION_DENIED", "Decision Denied", detail)
    # Любая иная неоднозначная форма решения — тоже отказ, не 200.
    return PluginProblem(403, "DECISION_DENIED", "Decision Denied", detail)


def problem_from_exception(exc: BaseException) -> PluginProblem | None:
    """Маппинг доменных исключений в HTTP. ``None`` = исключение не наше."""
    if isinstance(exc, PluginTransportError):
        return exc.problem
    if isinstance(exc, RequestValidationError):
        return plugin_validation_problem(exc)

    # Исключения задачи 02: PluginTokenError несёт .code (PluginTokenErrorCode).
    # FORBIDDEN_SURFACE и REVOKED — 403, остальные — 401 (п.1 ТЗ).
    if type(exc).__name__ == "PluginTokenError" and hasattr(exc, "code"):
        mapped = problem_from_code(getattr(exc, "code"), str(getattr(exc, "detail", "")) or "plugin token rejected")
        if mapped is not None:
            return mapped
        return PluginProblem(401, "UNAUTHORIZED", "Unauthorized", "plugin token rejected")

    # Исключения задачи 03 и произвольные имена из будущих реализаций.
    name = type(exc).__name__
    if name in {"AdapterMissingError", "AdapterNotFound", "MissingAdapterError"}:
        return PluginProblem(503, "ADAPTER_MISSING", "Adapter Missing", str(exc))
    if name in {"UnknownCapabilityError", "CapabilityUnknownError"}:
        return PluginProblem(503, "CAPABILITY_UNKNOWN", "Capability Unknown", str(exc))
    if name in {"RevokedGrant", "GrantRevokedError", "RevokedGrantError"}:
        return PluginProblem(403, "GRANT_REVOKED", "Grant Revoked", str(exc))
    if name in {"QuotaExceededError", "QuotaExceeded"}:
        return PluginProblem(429, "QUOTA_EXCEEDED", "Quota Exceeded", str(exc))
    if name in {"TypedContractError", "ValidationError", "PydanticValidationError"}:
        return PluginProblem(422, "VALIDATION_ERROR", "Validation Error", str(exc))

    # Общий случай: у исключения есть атрибут .code (DecisionCode, QuotaReason,
    # ArtifactErrorCode и т.п.). Нормализация снимает разницу регистров.
    code = getattr(exc, "code", None)
    if code is not None:
        mapped = problem_from_code(code, str(exc))
        if mapped is not None and mapped.status >= 400:
            return mapped
    return None


def plugin_validation_problem(exc: RequestValidationError) -> PluginProblem:
    """422 для Pydantic-ошибок; отдельный код для попытки generic dispatch."""
    errors = exc.errors()
    first: Mapping[str, Any] = errors[0] if errors else {}
    message = str(first.get("msg") or "Invalid request")
    loc = ".".join(str(part) for part in first.get("loc", []) if part != "body")
    detail = f"{loc}: {message}" if loc else message
    code = "FORBIDDEN_GENERIC_DISPATCH" if "generic dispatch" in message else "VALIDATION_ERROR"
    return PluginProblem(422, code, "Validation Error", detail)


__all__ = [
    "GENERIC_DISPATCH_FIELDS",
    "PROBLEM_JSON",
    "PluginProblem",
    "PluginTransportError",
    "normalize_code",
    "plugin_problem_response",
    "plugin_validation_problem",
    "problem_from_code",
    "problem_from_decision",
    "problem_from_exception",
]
