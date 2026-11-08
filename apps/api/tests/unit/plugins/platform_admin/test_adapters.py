"""Тесты адаптеров PlatformAdmin (написаны, НЕ запускались: verification freeze).

Покрытие по ТЗ:
  * нет токена → отказ (401);
  * невалидный токен → отказ (401, AppError пробрасывается как есть);
  * валидный platform-admin → OK (AdminIdentity);
  * нет роли platform-admin → отказ (403);
  * ``attach_admin_runtime`` кладёт оба ключа;
  * ``require_admin_runtime`` на пустом app → 503;
  * адаптер НЕ дублирует проверку JWT: вызывает существующую функцию
    ``get_jwt_validator()`` / ``JwtValidator.validate`` (инъекция-шпион + проверка
    исходного кода на отсутствие ``jwt.decode``/``PyJWKClient``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import AdminIdentity
from prodavan.application.plugins.admin.ports import PluginAdminPorts
from prodavan.application.plugins.admin.service import PluginAdminService
from prodavan.application.plugins.platform_admin import (
    ADMIN_RUNTIME_UNAVAILABLE_CODE,
    STATE_KEYS,
    BearerPlatformAdminAdapter,
    PlatformAdminContext,
    PluginAdminRuntimeNotAttached,
    Principal,
    attach_admin_runtime,
    detach_admin_runtime,
    require_admin_runtime,
)
from prodavan.application.plugins.platform_admin import adapter as adapter_module
from prodavan.domain.errors import AppError
from prodavan.domain.identity import ROLE_PLATFORM_ADMIN

# --------------------------------------------------------------------------- фейки


@dataclass(frozen=True, slots=True)
class FakeDomainPrincipal:
    """Структурная копия ``domain.identity.Principal`` (без импорта чужого типа)."""

    sub: str
    roles: frozenset[str] = field(default_factory=frozenset)


class SpyValidator:
    """Шпион: фиксирует вызовы ``validate`` и возвращает заданный результат.

    Подменяет **существующий** ``JwtValidator`` через инъекцию в конструктор
    адаптера — этим доказывается, что адаптер вызывает внешнюю функцию
    верификации, а не разбирает JWT сам.
    """

    def __init__(self, principal: FakeDomainPrincipal | None = None) -> None:
        self.principal = principal
        self.calls: list[str] = []
        self.raises: Exception | None = None

    def validate(self, token: str) -> object:
        self.calls.append(token)
        if self.raises is not None:
            raise self.raises
        if self.principal is None:
            raise AssertionError("SpyValidator: principal не задан")
        return self.principal


class FakeRequest:
    """Минимальный ``fastapi.Request``: только ``headers``."""

    def __init__(self, authorization: str | None) -> None:
        headers = {} if authorization is None else {"Authorization": authorization}
        self.headers = SimpleNamespace(get=lambda key: headers.get(key))


class FakeApp:
    def __init__(self) -> None:
        self.state = SimpleNamespace()


def _bearer(token: str = "valid.jwt.token") -> str:
    return f"Bearer {token}"


def _admin_principal() -> FakeDomainPrincipal:
    return FakeDomainPrincipal(sub="test-platform-admin", roles=frozenset({ROLE_PLATFORM_ADMIN}))


def _run(coro: Any) -> Any:
    """Синхронный запуск coroutine без pytest-asyncio-фикстур (тесты не запускаются)."""

    return asyncio.run(coro)


# --------------------------------------------------------------------------- ports (доменные типы)


def test_principal_is_frozen_dataclass_without_pydantic() -> None:
    principal = Principal(
        subject="admin-1",
        company_id="co_a",
        scopes=frozenset({ROLE_PLATFORM_ADMIN}),
        authenticated=True,
    )
    assert principal.subject == "admin-1"
    assert principal.company_id == "co_a"
    assert principal.is_platform_admin is True
    with pytest.raises((TypeError, AttributeError)):
        principal.subject = "other"  # type: ignore[misc]
    # Домен не импортирует Pydantic/FastAPI/Starlette: в docstring эти слова
    # встречаются в отрицании («не зависит от Pydantic»), поэтому смотрим
    # только на строки импортов. Читаем ports.py — модуль, где ОПРЕДЕЛЁН
    # Principal (ранее здесь использовалась неопределённая переменная source).
    from prodavan.application.plugins.platform_admin import ports as ports_module

    source = Path(ports_module.__file__).read_text(encoding="utf-8")
    imports = [
        line for line in source.splitlines()
        if line.startswith(("import ", "from ")) and "prodavan.application.plugins.platform_admin" not in line
    ]
    joined = "\n".join(imports).lower()
    assert "pydantic" not in joined
    assert "fastapi" not in joined
    assert "starlette" not in joined


def test_platform_admin_context_is_fail_closed_by_default() -> None:
    context = PlatformAdminContext()
    assert context.authenticated is False
    assert context.is_platform_admin is False
    assert context.scopes == frozenset()
    # Нельзя собрать «успешный» контекст с неаутентифицированным principal.
    with pytest.raises(ValueError):
        PlatformAdminContext(authenticated=True)
    with pytest.raises(ValueError):
        PlatformAdminContext(company_id="   ")


def test_platform_admin_role_matches_repository_constant() -> None:
    from prodavan.application.plugins.platform_admin.ports import PLATFORM_ADMIN_ROLE

    assert PLATFORM_ADMIN_ROLE == ROLE_PLATFORM_ADMIN == "platform.admin"


# --------------------------------------------------------------------------- адаптер: нет токена → отказ


@pytest.mark.parametrize(
    "authorization",
    [None, "", "   ", "Basic dXNlcjpwYXNz", "Token abc", "Bearer", "Bearer    ", "bearer a b"],
)
def test_no_or_malformed_bearer_is_unauthorized(authorization: str | None) -> None:
    adapter = BearerPlatformAdminAdapter(validator=SpyValidator(_admin_principal()))
    with pytest.raises(AppError) as ei:
        _run(adapter.require_platform_admin(authorization=authorization))
    assert ei.value.status == 401
    assert ei.value.code == "UNAUTHORIZED"


def test_bearer_from_request_extracts_token() -> None:
    adapter = BearerPlatformAdminAdapter(validator=SpyValidator(_admin_principal()))
    assert adapter.bearer_from_request(FakeRequest(_bearer("abc.def.ghi"))) == "abc.def.ghi"
    with pytest.raises(AppError) as ei:
        adapter.bearer_from_request(FakeRequest(None))
    assert ei.value.status == 401


# --------------------------------------------------------------------------- адаптер: невалидный токен → отказ


def test_invalid_token_app_error_is_propagated_not_swallowed() -> None:
    original = AppError(
        code="UNAUTHORIZED", title="Unauthorized", status=401, detail="Invalid or expired token"
    )
    spy = SpyValidator(_admin_principal())
    spy.raises = original
    adapter = BearerPlatformAdminAdapter(validator=spy)
    with pytest.raises(AppError) as ei:
        _run(adapter.require_platform_admin(authorization=_bearer("expired.jwt")))
    assert ei.value is original  # пробрасываем как есть, не «пустой успех»
    assert spy.calls == ["expired.jwt"]


def test_validator_failure_is_unauthorized_not_success() -> None:
    spy = SpyValidator(_admin_principal())
    spy.raises = RuntimeError("JWKS unavailable")
    adapter = BearerPlatformAdminAdapter(validator=spy)
    with pytest.raises(AppError) as ei:
        _run(adapter.require_platform_admin(authorization=_bearer("whatever")))
    assert ei.value.status == 401
    assert ei.value.code == "UNAUTHORIZED"


def test_validator_returning_malformed_principal_is_unauthorized() -> None:
    class BrokenValidator:
        def validate(self, token: str) -> object:
            return object()  # нет ни sub, ни roles

    adapter = BearerPlatformAdminAdapter(validator=BrokenValidator())  # type: ignore[arg-type]
    with pytest.raises(AppError) as ei:
        _run(adapter.require_platform_admin(authorization=_bearer()))
    assert ei.value.status == 401


def test_validator_returning_none_is_unauthorized() -> None:
    class NoneValidator:
        def validate(self, token: str) -> object:
            return None

    adapter = BearerPlatformAdminAdapter(validator=NoneValidator())  # type: ignore[arg-type]
    with pytest.raises(AppError) as ei:
        _run(adapter.require_platform_admin(authorization=_bearer()))
    assert ei.value.status == 401


# --------------------------------------------------------------------------- адаптер: валидный platform-admin → OK


def test_valid_platform_admin_returns_admin_identity() -> None:
    spy = SpyValidator(_admin_principal())
    adapter = BearerPlatformAdminAdapter(validator=spy)
    identity = _run(adapter.require_platform_admin(authorization=_bearer("admin.jwt")))
    assert isinstance(identity, AdminIdentity)
    assert identity.actor_id == "test-platform-admin"
    assert identity.actor_id.strip()
    assert identity.is_platform_admin is True
    assert ROLE_PLATFORM_ADMIN in identity.scopes
    assert spy.calls == ["admin.jwt"]


def test_adapter_implements_platform_admin_port_protocol() -> None:
    # ``PlatformAdminPort`` — обычный (не runtime_checkable) Protocol, поэтому
    # проверяем структурно: нужный async-метод с той же сигнатурой.
    import inspect

    adapter = BearerPlatformAdminAdapter(validator=SpyValidator(_admin_principal()))
    method = getattr(adapter, "require_platform_admin", None)
    assert callable(method)
    assert inspect.iscoroutinefunction(method)
    assert list(inspect.signature(method).parameters) == ["authorization"]
    # Порт объявлен keyword-only: совпадает имя и «звёздочка».
    param = inspect.signature(method).parameters["authorization"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_valid_token_without_platform_admin_role_is_forbidden() -> None:
    spy = SpyValidator(FakeDomainPrincipal(sub="employee-1", roles=frozenset({"employee"})))
    adapter = BearerPlatformAdminAdapter(validator=spy)
    with pytest.raises(AppError) as ei:
        _run(adapter.require_platform_admin(authorization=_bearer("employee.jwt")))
    assert ei.value.status == 403
    assert ei.value.code == "FORBIDDEN"


# --------------------------------------------------------------------------- адаптер НЕ дублирует проверку JWT


def test_adapter_reuses_existing_get_jwt_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без инъекции адаптер вызывает ровно существующий ``get_jwt_validator()``."""

    spy = SpyValidator(_admin_principal())
    called: list[str] = []

    def fake_get_jwt_validator() -> SpyValidator:
        called.append("get_jwt_validator")
        return spy

    monkeypatch.setattr(adapter_module, "get_jwt_validator", fake_get_jwt_validator)
    adapter = BearerPlatformAdminAdapter()  # validator=None → репозиторский синглтон
    identity = _run(adapter.require_platform_admin(authorization=_bearer("real.jwt")))
    assert called == ["get_jwt_validator"]
    assert spy.calls == ["real.jwt"]
    assert identity.is_platform_admin is True


def test_adapter_source_does_not_duplicate_jwt_verification() -> None:
    """В адаптере нет jwt.decode / PyJWKClient / проверки exp/aud/issuer в КОДЕ.

    Комментарии намеренно упоминают запрещённые механизмы («никакого
    jwt.decode / PyJWKClient»), поэтому проверяем только исполняемые строки:
    комментарии и docstring-строки отбрасываются.
    """

    source = (Path(__file__).parents[4] / "src" / "prodavan" / "application" / "plugins"
              / "platform_admin" / "adapter.py").read_text(encoding="utf-8")
    # Репозиторский механизм — единственная точка верификации: в КОДЕ нет
    # импорта PyJWT/JWKS-клиента и нет вызова decode. Упоминания в docstring
    # («никакого jwt.decode / PyJWKClient») — это документация, не код.
    code_lines = [line.split("#", 1)[0] for line in source.splitlines()]
    code = "\n".join(code_lines)
    assert "get_jwt_validator()" in code
    assert "validator.validate(token)" in code
    for forbidden in ("import jwt", "from jwt", "PyJWKClient(", "jwt.decode(", "jwt.encode("):
        assert forbidden not in code, f"адаптер дублирует верификацию JWT: {forbidden}"
    # Репозиторский механизм упомянут явно (не «своя» реализация).
    assert "infrastructure/auth/jwt.py" in source


# --------------------------------------------------------------------------- state: attach_admin_runtime


def _ports_stub(adapter: BearerPlatformAdminAdapter) -> PluginAdminPorts:
    """Собрать связку портов, не трогая чужие каталоги (все 10 полей обязательны)."""

    stub = object()
    return PluginAdminPorts(
        tenant_db=stub,  # type: ignore[arg-type]
        object_store=stub,  # type: ignore[arg-type]
        artifact_registry=stub,  # type: ignore[arg-type]
        grant_store=stub,  # type: ignore[arg-type]
        revocation=stub,  # type: ignore[arg-type]
        mint=stub,  # type: ignore[arg-type]
        activation=stub,  # type: ignore[arg-type]
        routes=stub,  # type: ignore[arg-type]
        audit=stub,  # type: ignore[arg-type]
        platform_admin=adapter,
    )


def test_attach_admin_runtime_puts_both_required_keys() -> None:
    app = FakeApp()
    adapter = BearerPlatformAdminAdapter(validator=SpyValidator(_admin_principal()))
    ports = _ports_stub(adapter)
    service = PluginAdminService(ports)

    attach_admin_runtime(app, ports=ports, service=service)

    assert app.state.plugin_admin_ports is ports
    assert app.state.plugin_admin_service is service
    assert STATE_KEYS == ("plugin_admin_ports", "plugin_admin_service")
    for key in STATE_KEYS:
        assert hasattr(app.state, key), f"app.state.{key} отсутствует"
    # Роутер plugin_admin.py::_ports / _service примет эти значения.
    assert isinstance(app.state.plugin_admin_ports, PluginAdminPorts)
    assert isinstance(app.state.plugin_admin_service, PluginAdminService)


@pytest.mark.parametrize("ports_none,service_none", [(True, False), (False, True), (True, True)])
def test_attach_admin_runtime_rejects_partial_integration(
    ports_none: bool, service_none: bool
) -> None:
    app = FakeApp()
    adapter = BearerPlatformAdminAdapter(validator=SpyValidator(_admin_principal()))
    ports = None if ports_none else _ports_stub(adapter)
    service = None if service_none else PluginAdminService(_ports_stub(adapter))
    with pytest.raises(PluginAdminRuntimeNotAttached):
        attach_admin_runtime(app, ports=ports, service=service)
    # Атомарность: ни одного ключа не записано до отказа.
    for key in STATE_KEYS:
        assert not hasattr(app.state, key)


def test_attach_admin_runtime_rejects_wrong_types() -> None:
    app = FakeApp()
    with pytest.raises(PluginAdminRuntimeNotAttached):
        attach_admin_runtime(app, ports=object(), service=object())  # type: ignore[arg-type]


def test_detach_admin_runtime_removes_only_its_keys() -> None:
    app = FakeApp()
    app.state.unrelated = "keep-me"  # type: ignore[attr-defined]
    adapter = BearerPlatformAdminAdapter(validator=SpyValidator(_admin_principal()))
    ports = _ports_stub(adapter)
    attach_admin_runtime(app, ports=ports, service=PluginAdminService(ports))
    detach_admin_runtime(app)
    for key in STATE_KEYS:
        assert not hasattr(app.state, key)
    assert app.state.unrelated == "keep-me"  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- state: require_admin_runtime → 503


def test_require_admin_runtime_on_empty_app_is_503() -> None:
    request = SimpleNamespace(app=FakeApp())
    with pytest.raises(AppError) as ei:
        require_admin_runtime(request)
    assert ei.value.status == 503
    assert ei.value.code == ADMIN_RUNTIME_UNAVAILABLE_CODE == "PLUGIN_ADMIN_NOT_CONFIGURED"


def test_require_admin_runtime_on_missing_state_is_503() -> None:
    with pytest.raises(AppError) as ei:
        require_admin_runtime(SimpleNamespace(app=object()))
    assert ei.value.status == 503


def test_require_admin_runtime_rejects_partial_or_wrong_type_state() -> None:
    app = FakeApp()
    app.state.plugin_admin_ports = object()  # type: ignore[attr-defined]
    with pytest.raises(AppError) as ei:
        require_admin_runtime(SimpleNamespace(app=app))
    assert ei.value.status == 503

    app2 = FakeApp()
    app2.state.plugin_admin_ports = _ports_stub(
        BearerPlatformAdminAdapter(validator=SpyValidator(_admin_principal()))
    )
    # service отсутствует → 503, а не «порты есть, значит пустим».
    with pytest.raises(AppError) as ei2:
        require_admin_runtime(SimpleNamespace(app=app2))
    assert ei2.value.status == 503


def test_require_admin_runtime_returns_both_when_attached() -> None:
    app = FakeApp()
    adapter = BearerPlatformAdminAdapter(validator=SpyValidator(_admin_principal()))
    ports = _ports_stub(adapter)
    service = PluginAdminService(ports)
    attach_admin_runtime(app, ports=ports, service=service)
    got_ports, got_service = require_admin_runtime(SimpleNamespace(app=app))
    assert got_ports is ports
    assert got_service is service


# --------------------------------------------------------------------------- интеграция с роутером (контракт ключей)


def test_router_expects_exactly_these_state_keys() -> None:
    """Сверка с ``api/v1/plugin_admin.py``: `_ports`/`_service` читают эти ключи."""

    source = (Path(__file__).parents[4] / "src" / "prodavan" / "api" / "v1"
              / "plugin_admin.py").read_text(encoding="utf-8")
    for key in STATE_KEYS:
        assert f'"{key}"' in source, f"роутер не ждёт app.state.{key}"
