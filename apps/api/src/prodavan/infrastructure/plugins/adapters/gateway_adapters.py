"""Реальные адаптеры портов Plugin Gateway (задача 11, дизайн v4 §7.2/§7.4/§7.5).

Здесь собраны 8 реализаций 8 ``Protocol`` из
``application/plugins/gateway/ports.py``:

===========================  ==========================================
Порт задачи 11                Адаптер
===========================  ==========================================
``SessionPort``               :class:`SessionAdapter`
``GrantPort``                 :class:`GrantAdapter`
``SecretPort``                :class:`SecretAdapter`
``HmacPort``                  :class:`Sha256HmacAdapter`
``RateLimitPort``             :class:`RedisRateLimitAdapter`
``ActivationPort``            :class:`ActivationAdapter`
``BackendHttpPort``           :class:`BackendHttpAdapter`
``AuditPort``                 :class:`AuditAdapter`
===========================  ==========================================

Адаптеры НЕ читают ``settings`` и не имеют dev-фолбэков: все внешние зависимости
(валидатор JWT, каталог сотрудников, secret store, Redis-клиент, activator,
httpx-клиент, append-only sink) приходят **явно** через конструктор. Отсутствие
зависимости = отказ, а не «тихое разрешение» (fail-closed, §0.5 плана миграции).

.. danger:: **РИСК СТЫКА ЗАДАЧ 11 / 02 / 09 — ИСПРАВЛЕН ЗДЕСЬ.**

   ``gateway/headers.py::sanitize_incoming`` отбрасывает входящий
   ``Authorization`` (внешний клиент не имеет права его задавать), а
   ``build_trusted_headers`` кладёт свежий capability-токен ТОЛЬКО в
   ``X-Plugin-Capability-Token``. При этом задачи 02 (``transport/deps.py``)
   и 09 (``activation/state.py::WakeResult``) ожидают JWT именно в
   ``Authorization: Bearer <WakeResult.token>``. Без явной подстановки backend
   плагина получил бы ``401`` на каждом запросе.

   :func:`with_trusted_bearer` / :class:`BearerAuthorizationInjector` /
   :class:`BackendHttpAdapter` ЯВНО добавляют доверенный
   ``Authorization: Bearer <WakeResult.token>`` к уже доверенному
   ``X-Plugin-Capability-Token``. **Оба заголовка присутствуют**; клиентский
   ``Authorization``/``Cookie`` при этом безусловно отбрасываются. Статика и
   anon/public_pages capability-токена не получают → ``Authorization`` там
   тоже отсутствует (§7.2, §5.2).

Кластерные оговорки: ``InMemoryGrantStore`` / ``InMemoryCookieSessionStore`` /
in-flight счётчик ротации секретов — только dev/тесты; в кластере заменяются на
БД/Redis (**задача A10** родителя). См. также :mod:`stores.py`.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Final, Protocol, runtime_checkable

from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord
from prodavan.application.plugins.artifacts.verification import manifest_fingerprint
from prodavan.application.plugins.gateway.canonical import canonical_raw_query
from prodavan.application.plugins.gateway.errors import BackendResponseTooLargeError
from prodavan.application.plugins.gateway.headers import HEADER_CAPABILITY_TOKEN, TRUSTED_HEADERS
from prodavan.application.plugins.gateway.ports import (
    ANON_SCOPES,
    BackendRequest,
    BackendResponse,
    DeliveryStatus,
    DeliveryVerdict,
    GatewayAuditEvent,
    GatewayGrant,
    GatewayOperation,
    GatewaySession,
    WakeLease,
    is_valid_artifact_digest,
)
from prodavan.domain.dynamic_apps import AppLifecycleStatus
from prodavan.domain.plugins.types import PluginRpcOperations, RpcOperationDeclaration

from .stores import CLUSTER_BACKEND_NOTE

# ---------------------------------------------------------------------------
# Общие константы / ошибки
# ---------------------------------------------------------------------------

HEADER_AUTHORIZATION: Final[str] = "Authorization"
HEADER_GRANT_ID: Final[str] = "X-Plugin-Grant-ID"
BEARER_PREFIX: Final[str] = "Bearer "
_TRUSTED_HEADER_NAMES: Final[frozenset[str]] = (
    frozenset(TRUSTED_HEADERS) | {HEADER_GRANT_ID}
)

#: Имена, которые клиент не имеет права передавать downstream даже если
#: ``build_forward_headers`` по какой-то причине их пропустил (defense-in-depth).
_UNTRUSTED_REQUEST_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "proxy-authenticate",
        "cookie",
        "set-cookie",
        "host",
        "content-length",
        # X-Prodavan-* уже удалены sanitize_incoming. Сюда proxy возвращает
        # только VerifiedWebhook после HMAC; adapter обязан их сохранить для hello.
    }
)

#: Hop-by-hop заголовки ответа (RFC 7230 §6.1) + служебные.
_HOP_BY_HOP_RESPONSE_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "proxy-authenticate",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "content-length",
        "content-encoding",
        "host",
        "www-authenticate",
        "set-cookie",
    }
)

_DIGEST_64_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_CANONICAL_RE: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
# Идентификаторы репозитория (``co_123``, ``mod_equipment``, ``plugin_wake_up``)
# содержат подчёркивание — оно явно допустимо в канонических audit-полях.
_SAFE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._:@\-/]{1,160}$")


class GatewayAdapterError(RuntimeError):
    """Отказ адаптера (fail-closed). Пробрасывается, а не глушится.

    ``GatewayAuth``/``GatewayProxy`` оборачивают любое исключение порта в
    типизированный ``GatewayError`` (``UPSTREAM_ERROR`` / ``AUDIT_APPEND_FAILED``
    и т.д.), поэтому «тихо вернуть None/False» здесь недопустимо.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _header_get(headers: Mapping[str, str], name: str) -> str | None:
    """Регистронезависимое чтение заголовка (HTTP-имена регистронезависимы)."""
    target = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target and isinstance(value, str):
            return value
    return None


# ---------------------------------------------------------------------------
# КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: доверенный Authorization: Bearer <WakeResult.token>
# ---------------------------------------------------------------------------


def with_trusted_bearer(
    headers: Mapping[str, str],
    *,
    capability_token: str | None = None,
) -> dict[str, str]:
    """Добавить ЯВНО доверенный ``Authorization: Bearer <capability_token>``.

    Это единственная точка, закрывающая разрыв между §7.2 (``X-Plugin-*
    заголовки``) и задачами 02/09 (JWT в ``Authorization``):

    * клиентские ``Authorization`` / ``Proxy-Authorization`` / ``Cookie`` /
      ``Host`` / ``Content-Length`` **безусловно удаляются** — доверенный
      заголовок не может быть подменён внешним значением (§6.4);
    * если ``capability_token`` пуст (anon/public_pages/static-origin),
      ``Authorization`` НЕ выставляется вовсе — урезанный anon-scope не
      получает capability-токена (§7.2, §12 вопрос 1);
    * ``X-Plugin-Capability-Token`` и остальные доверенные ``X-Plugin-*``
      (``X-Plugin-ID``/``X-Company-ID``/``X-Project-ID``) **сохраняются**:
      их выставил сам шлюз из claims/grant; capability-токен при этом
      перезаписывается свежим значением. Оба заголовка (токен и Authorization)
      должны присутствовать в исходящем запросе к backend'у плагина;

    Возвращается НОВЫЙ dict: исходные заголовки не мутируются, поэтому
    вызывающий не может случайно «протащить» клиентское значение обратно.
    """
    grant_id = _header_get(headers, HEADER_GRANT_ID)
    if grant_id is not None and _SAFE_ID_RE.fullmatch(grant_id) is None:
        raise GatewayAdapterError("trusted grant id is malformed")
    out: dict[str, str] = {}
    trusted_lower = HEADER_CAPABILITY_TOKEN.lower()
    trusted_names_lower = {name.lower() for name in _TRUSTED_HEADER_NAMES}
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str) or not value:
            continue
        lowered = name.lower()
        if lowered in _UNTRUSTED_REQUEST_HEADERS:
            continue
        # Defense-in-depth: даже при прямом вызове адаптера (минуя
        # build_forward_headers) клиентские X-Plugin-* не проходят downstream.
        # ИСКЛЮЧЕНИЕ — TRUSTED_HEADERS + canonical server-side grant header:
        # tenant/plugin/instance/artifact/capability/grant значения выставил
        # сам шлюз из GatewayClaims/GatewayGrant/WakeLease. Неизвестные X-Plugin-* и
        # client-supplied digest никогда не признаются доверенными.
        # Клиентские значения этих имён уже удалены sanitize_incoming.
        if lowered.startswith("x-plugin-") and lowered not in trusted_names_lower:
            continue
        out[name] = value

    # Перестраховка от регистроразличимых дублей: убираем ЛЮБОЕ написание
    # ``Authorization`` и ``X-Plugin-Capability-Token`` (в т.ч. клиентское
    # ``x-plugin-capability-token``), чтобы downstream получил ровно по одному
    # каноническому заголовку, а не два значения с разным регистром. Остальные
    # доверенные заголовки (X-Plugin-ID и т.д.) уже каноничны: их выставил
    # build_trusted_headers, дубли с другим регистром удалены ещё там.
    for key in [
        k
        for k in out
        if k.lower() in {HEADER_AUTHORIZATION.lower(), trusted_lower}
        or (k.lower() in trusted_names_lower and k not in _TRUSTED_HEADER_NAMES)
    ]:
        out.pop(key, None)

    token = (capability_token or "").strip()
    if token:
        # Заголовок не должен содержать CRLF/управляющих символов (header
        # injection / request splitting), иначе отказываем, а не отправляем.
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in token):
            raise GatewayAdapterError("capability token contains control characters")
        out[HEADER_CAPABILITY_TOKEN] = token
        out[HEADER_AUTHORIZATION] = f"{BEARER_PREFIX}{token}"
    else:
        # Нет capability-токена → ни одного из двух заголовков (они уже
        # удалены циклом выше). Явная перестраховка для будущих правок.
        out.pop(HEADER_AUTHORIZATION, None)
    return out


@runtime_checkable
class BackendSender(Protocol):
    """Структурный минимум ``BackendHttpPort`` для обёрток."""

    async def send(self, request: BackendRequest) -> BackendResponse: ...


class BearerAuthorizationInjector:
    """Обёртка над ЛЮБЫМ ``BackendHttpPort``: гарантирует доверенный Bearer.

    ``build_gateway_runtime`` автоматически оборачивает чужой
    ``BackendHttpPort`` в этот injector, чтобы исправление стыка 02/09/11
    не зависело от того, какой именно транспорт подключил интегратор.
    Значение берётся **только** из доверенного
    ``X-Plugin-Capability-Token`` (его выставляет ``build_forward_headers``
    из свежего ``WakeLease.capability_token``), никогда из входящего
    ``Authorization``.
    """

    def __init__(self, inner: BackendSender) -> None:
        if inner is None or not callable(getattr(inner, "send", None)):
            raise GatewayAdapterError("BearerAuthorizationInjector requires BackendHttpPort")
        self._inner = inner

    @property
    def inner(self) -> BackendSender:
        return self._inner

    async def send(self, request: BackendRequest) -> BackendResponse:
        token = _header_get(request.headers, HEADER_CAPABILITY_TOKEN)
        safe_headers = with_trusted_bearer(request.headers, capability_token=token)
        return await self._inner.send(replace(request, headers=safe_headers))

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result


# ---------------------------------------------------------------------------
# SessionPort
# ---------------------------------------------------------------------------


@runtime_checkable
class EmployeeDirectoryPort(Protocol):
    """Каталог сотрудников/членств (БД ``employees``/``memberships``)."""

    async def employee_by_subject(self, sub: str) -> str | None: ...

    async def company_ids_for_employee(self, employee_id: str) -> frozenset[str]: ...


@runtime_checkable
class CookieSessionPort(Protocol):
    """Хранилище opaque cookie ``session`` (не JWT). Fail-closed при отсутствии."""

    async def resolve(self, token: str) -> GatewaySession | None: ...


class InMemoryCookieSessionStore:
    """Opaque cookie ``session`` → сессия. Только dev/тесты (кластер → Redis, A10).

    Никаких дефолтных «тестовых» сессий: неизвестный маркер → ``None``.
    """

    def __init__(self, sessions: Mapping[str, GatewaySession] | None = None) -> None:
        self._sessions: dict[str, GatewaySession] = dict(sessions or {})

    async def resolve(self, token: str) -> GatewaySession | None:
        return self._sessions.get(token)

    def register(self, token: str, session: GatewaySession) -> None:
        if not isinstance(session, GatewaySession):
            raise TypeError("register expects GatewaySession")
        self._sessions[token] = session


class SqlEmployeeDirectory:
    """Реальный каталог через SQLAlchemy-модели репозитория.

    Использует ``EmployeeRow.keycloak_sub`` (проверенный OIDC ``sub`` из
    ``JwtValidator``) и ``MembershipRow.company_id`` — ровно те же таблицы,
    что ``api/deps.py::get_current_employee`` /
    ``EntitlementService.ensure_active_employee``. Отключённые/удалённые
    сотрудники не резолвятся (fail-closed).
    """

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        if session_factory is None:
            raise GatewayAdapterError("SqlEmployeeDirectory requires an async session factory")
        self._session_factory = session_factory

    async def employee_by_subject(self, sub: str) -> str | None:
        from sqlalchemy import select

        from prodavan.infrastructure.persistence.models.identity import EmployeeRow

        if not isinstance(sub, str) or not sub.strip():
            return None
        async with self._session_factory() as session:
            result = await session.execute(
                select(EmployeeRow.id).where(
                    EmployeeRow.keycloak_sub == sub,
                    EmployeeRow.deleted_at.is_(None),
                    EmployeeRow.status != "disabled",
                )
            )
            row = result.scalars().first()
            return str(row) if row else None

    async def company_ids_for_employee(self, employee_id: str) -> frozenset[str]:
        from sqlalchemy import select

        from prodavan.infrastructure.persistence.models.identity import MembershipRow

        if not isinstance(employee_id, str) or not employee_id.strip():
            return frozenset()
        async with self._session_factory() as session:
            result = await session.execute(
                select(MembershipRow.company_id).where(
                    MembershipRow.employee_id == employee_id
                )
            )
            return frozenset(str(value) for value in result.scalars().all() if value)


class SessionAdapter:
    """``SessionPort``: Bearer JWT (JWKS репозитория) или cookie ``session``.

    ``GatewayAuth._session_token`` отдаёт «сырой» маркер без признака источника,
    поэтому адаптер пытается разрешить оба варианта в одном и том же порядке:

    1. **Bearer / JWT в cookie** — ``JwtValidator.validate`` (существующий
       auth-механизм: ``infrastructure/auth/jwt.py``, JWKS Keycloak; режим
       ``auth_mode=test`` тоже поддерживается валидатором). Успех даёт
       проверенный ``principal.sub``;
    2. **opaque cookie** — явно подключённый :class:`CookieSessionPort`.
       Если он НЕ подключён, cookie-сессия отклоняется (fail-closed), а не
       «пустим без проверки».

    ``session_id`` — sha256-отпечаток маркера, а НЕ сам маркер: токены и
    секреты не попадают ни в аудит, ни в логи (§4.5). ``employee_in_company``
    всегда ходит в каталог членств, даже если ``GatewaySession.company_ids``
    уже заполнен: проверка ``employee ∈ company`` обязана быть актуальной
    на момент запроса (§7.2).
    """

    def __init__(
        self,
        *,
        validator: Any = None,
        directory: EmployeeDirectoryPort,
        cookie_sessions: CookieSessionPort | None = None,
    ) -> None:
        if directory is None or not callable(getattr(directory, "employee_by_subject", None)):
            raise GatewayAdapterError("SessionAdapter requires an employee directory")
        self._validator = validator  # None → ленивый get_jwt_validator()
        self._directory = directory
        self._cookie_sessions = cookie_sessions
        self.resolve_calls: list[str | None] = []
        self.membership_calls: list[tuple[str, str]] = []

    def _jwt_validator(self) -> Any:
        if self._validator is not None:
            return self._validator
        from prodavan.infrastructure.auth.jwt import get_jwt_validator

        return get_jwt_validator()

    async def resolve(self, *, session_token: str | None) -> GatewaySession | None:
        self.resolve_calls.append(session_token)
        if not isinstance(session_token, str) or not session_token.strip():
            return None

        # --- 1) проверенный JWT (Bearer или JWT, лежащий в cookie) ----------
        try:
            principal = self._jwt_validator().validate(session_token)
            sub = str(getattr(principal, "sub", "") or "").strip()
            if sub:
                employee_id = await self._directory.employee_by_subject(sub)
                if employee_id:
                    company_ids = await self._directory.company_ids_for_employee(employee_id)
                    return GatewaySession(
                        session_id=hashlib.sha256(session_token.encode("utf-8")).hexdigest(),
                        employee_id=employee_id,
                        subject_id=f"employee:{employee_id}",
                        company_ids=frozenset(company_ids),
                    )
        except Exception:  # noqa: BLE001 — невалидный/истёкший токен ≠ 500
            # Ошибка валидатора (в т.ч. JWKS недоступен) — это отказ, а не
            # «пропустим и посмотрим на cookie как на успех». Продолжаем только
            # к ЯВНО подключённому cookie-хранилищу.
            pass

        # --- 2) непрозрачная cookie session ---------------------------------
        if self._cookie_sessions is None:
            return None  # fail-closed: канал не настроен
        try:
            session = await self._cookie_sessions.resolve(session_token)
        except Exception:  # noqa: BLE001 — сбой хранилища ≠ успех
            return None
        if not isinstance(session, GatewaySession):
            return None
        # Подменяем session_id на отпечаток: исходный маркер не должен уйти
        # ни в доверенные заголовки, ни в аудит (§4.5).
        return GatewaySession(
            session_id=hashlib.sha256(session_token.encode("utf-8")).hexdigest(),
            employee_id=session.employee_id,
            subject_id=session.subject_id,
            company_ids=frozenset(session.company_ids),
        )

    async def employee_in_company(self, *, employee_id: str, company_id: str) -> bool:
        self.membership_calls.append((employee_id, company_id))
        if not employee_id or not company_id:
            return False
        try:
            companies = await self._directory.company_ids_for_employee(employee_id)
        except Exception:  # noqa: BLE001 — недоступный каталог = False, не allow
            return False
        return company_id in companies


# ---------------------------------------------------------------------------
# GrantPort
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GrantRecord:
    """Активный ``CapabilityGrant`` из хранилища (БД в кластере, задача A10).

    Egress-политика живёт ЗДЕСЬ, в долгоживущем grant'е, а не в кратком JWT
    (§4.2, §6.1, критерий №22). ``capability_token`` намеренно отсутствует:
    свежий токен приходит из ``WakeLease`` при каждом wake-up, а не из БД.
    """

    grant_id: str
    plugin_id: str
    company_id: str
    plugin_instance_id: str
    active: bool = True
    operations: frozenset[str] = frozenset()
    public_pages: frozenset[str] = frozenset()
    egress_mode: str = "none"
    egress_allowed_hosts: tuple[str, ...] = ()
    egress_allowed_paths: tuple[str, ...] = ()
    egress_approved_for_full: bool = False
    artifact_digest: str = ""
    manifest_version: str = ""
    #: None = manifest routes недоступны; пустой tuple = webhook не объявлен.
    webhooks: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not self.grant_id.strip():
            raise ValueError("grant_id must not be empty")
        if not self.plugin_instance_id.strip():
            raise ValueError("plugin_instance_id must not be empty")


@runtime_checkable
class GrantStorePort(Protocol):
    """Хранилище grant'ов: in-memory (dev/тесты) или БД (кластер, A10)."""

    async def active_grant(self, *, plugin_id: str, company_id: str) -> GrantRecord | None: ...


class InMemoryGrantStore:
    """In-memory хранилище grant'ов. **Кластер → БД (задача A10).**

    Неизвестная пара ``(plugin, company)`` → ``None`` (fail-closed).
    """

    def __init__(self, grants: Iterable[GrantRecord] = ()) -> None:
        self._grants: dict[tuple[str, str], GrantRecord] = {}
        for record in grants:
            self.put(record)

    def put(self, record: GrantRecord) -> None:
        if not isinstance(record, GrantRecord):
            raise TypeError("put expects GrantRecord")
        self._grants[(record.plugin_id, record.company_id)] = record

    def revoke(self, *, plugin_id: str, company_id: str) -> None:
        existing = self._grants.get((plugin_id, company_id))
        if existing is not None:
            self._grants[(plugin_id, company_id)] = replace(existing, active=False)

    async def active_grant(self, *, plugin_id: str, company_id: str) -> GrantRecord | None:
        record = self._grants.get((plugin_id, company_id))
        if record is None or not record.active:
            return None
        if record.plugin_id != plugin_id or record.company_id != company_id:
            return None  # защита от перепутанной записи
        return record


class GrantAdapter:
    """``GrantPort``: активный grant + ``public_pages`` + egress-политика.

    Важно: ``GatewayGrant.capability_token`` оставляем ПУСТЫМ. Шлюз обязан
    подставить свежий ``WakeLease.capability_token`` (``proxy.py`` делает это
    в ``forward``), а устаревший токен из БД не должен никуда уйти. Для
    статики/anon токен не выдаётся вовсе.
    """

    def __init__(self, store: GrantStorePort, artifact_registry: Any | None = None) -> None:
        if store is None or not callable(getattr(store, "active_grant", None)):
            raise GatewayAdapterError("GrantAdapter requires a GrantStorePort")
        self._store = store
        self._artifact_registry = artifact_registry
        self.grant_calls: list[tuple[str, str]] = []
        self.public_calls: list[tuple[str, str]] = []
        self.operation_calls: list[tuple[str, str]] = []

    async def _load_bound_published_artifact(
        self, record: GrantRecord
    ) -> PluginArtifactRecord | None:
        """Load only the published bundle bound to this exact durable grant."""
        if self._artifact_registry is None or not record.manifest_version:
            return None
        getter = getattr(self._artifact_registry, "get_async", None)
        if not callable(getter):
            return None
        try:
            artifact = await getter(
                plugin_id=record.plugin_id, version=record.manifest_version
            )
        except Exception:  # noqa: BLE001 — unavailable artifact evidence = deny static
            return None
        if not self._is_bound_published_artifact(artifact, record):
            return None
        return artifact

    async def active_grant(self, *, plugin_id: str, company_id: str) -> GatewayGrant | None:
        self.grant_calls.append((plugin_id, company_id))
        try:
            record = await self._store.active_grant(
                plugin_id=plugin_id, company_id=company_id
            )
        except Exception:  # noqa: BLE001 — store/registry failure = no active grant
            return None
        if (
            not isinstance(record, GrantRecord)
            or not record.active
            or record.plugin_id != plugin_id
            or record.company_id != company_id
            or not record.plugin_instance_id.strip()
            or not is_valid_artifact_digest(record.artifact_digest)
        ):
            return None
        published_artifact = await self._load_bound_published_artifact(record)
        return GatewayGrant(
            grant_id=record.grant_id,
            plugin_instance_id=record.plugin_instance_id,
            active=True,
            capability_token="",  # свежий токен → из WakeLease, не из БД
            operations=frozenset(record.operations),
            artifact_digest=record.artifact_digest,
            # Exact PluginArtifactRecord.digest (bundle), never publication.backend_digest.
            # Absence stays explicit; forward_static turns it into typed unavailable.
            published_artifact_digest=(
                published_artifact.digest if published_artifact is not None else None
            ),
        )

    async def public_pages(self, *, plugin_id: str, company_id: str) -> frozenset[str]:
        self.public_calls.append((plugin_id, company_id))
        try:
            record = await self._store.active_grant(
                plugin_id=plugin_id, company_id=company_id
            )
        except Exception:  # noqa: BLE001 — no public fallback on store failure
            return frozenset()
        if (
            not isinstance(record, GrantRecord)
            or not record.active
            or record.plugin_id != plugin_id
            or record.company_id != company_id
        ):
            # Пустой набор = публичных страниц нет, анонимный доступ запрещён.
            return frozenset()
        return frozenset(record.public_pages)

    async def rpc_operations(
        self, *, plugin_id: str, company_id: str
    ) -> tuple[GatewayOperation, ...] | None:
        """Resolve declarations only from the grant-bound signed artifact record.

        The bootstrap DTO and GrantRecord.operations are not manifest proof.
        Every lookup revalidates tenant/plugin/instance binding, immutable digest,
        publication/quarantine state and the verification evidence fingerprint.
        Registry failures and malformed declarations deny the anonymous registry
        by returning None; this method never creates or activates a grant.
        """
        self.operation_calls.append((plugin_id, company_id))
        try:
            record = await self._store.active_grant(
                plugin_id=plugin_id, company_id=company_id
            )
        except Exception:  # noqa: BLE001 — unavailable registry/grant = no public operations
            return None
        if (
            not isinstance(record, GrantRecord)
            or not record.active
            or record.plugin_id != plugin_id
            or record.company_id != company_id
            or not record.plugin_instance_id.strip()
            or not record.manifest_version
            or not record.artifact_digest
            or self._artifact_registry is None
        ):
            return None
        artifact = await self._load_bound_published_artifact(record)
        if artifact is None:
            return None
        return self._map_signed_operations(artifact.manifest)

    @staticmethod
    def _is_bound_published_artifact(
        artifact: Any, record: GrantRecord
    ) -> bool:
        if not isinstance(artifact, PluginArtifactRecord):
            return False
        if (
            artifact.plugin_id != record.plugin_id
            or artifact.version != record.manifest_version
            or artifact.digest != record.artifact_digest
            or artifact.status is not AppLifecycleStatus.PUBLISHED
            or artifact.outcome is not RegistryOutcome.APPLIED
            or artifact.quarantine is not None
            or artifact.published_at is None
            or artifact.publication is None
        ):
            return False
        manifest = artifact.manifest
        if not isinstance(manifest, Mapping) or (
            manifest.get("plugin_id") != record.plugin_id
            or manifest.get("version") != record.manifest_version
            or manifest.get("protocol") != "plugin-rpc/1"
        ):
            return False
        evidence = artifact.verification
        if (
            evidence is None
            or not evidence.verified
            or evidence.artifact_digest != artifact.digest
            or evidence.image_ref != artifact.image_ref
            or not evidence.identity.strip()
            or not evidence.issuer.strip()
        ):
            return False
        try:
            fingerprint = manifest_fingerprint(manifest)
        except Exception:  # noqa: BLE001 — malformed signed record = deny
            return False
        return (
            artifact.manifest_fingerprint == fingerprint
            and evidence.manifest_fingerprint == fingerprint
        )

    @staticmethod
    def _map_signed_operations(
        manifest: Mapping[str, Any]
    ) -> tuple[GatewayOperation, ...] | None:
        raw = manifest.get("rpc_operations", [])
        if not isinstance(raw, list) or len(raw) > 64:
            return None
        declarations: list[RpcOperationDeclaration] = []
        try:
            for item in raw:
                if not isinstance(item, dict) or set(item) - {
                    "capability", "operation", "http_method", "backend_path", "public"
                }:
                    return None
                if not {"capability", "operation", "http_method", "backend_path"} <= set(item):
                    return None
                capability = item["capability"]
                if not isinstance(capability, dict) or set(capability) != {"id", "version"}:
                    return None
                declarations.append(
                    RpcOperationDeclaration(
                        capability_id=capability["id"],
                        capability_version=capability["version"],
                        operation=item["operation"],
                        http_method=item["http_method"],
                        backend_path=item["backend_path"],
                        public=item.get("public", False),
                    )
                )
            signed = PluginRpcOperations(tuple(declarations))
            return tuple(
                GatewayOperation(
                    capability_id=item.capability_id,
                    capability_version=item.capability_version,
                    operation=item.operation,
                    http_method=item.http_method,
                    backend_path=item.backend_path,
                    public=item.public,
                )
                for item in signed.operations
            )
        except (TypeError, ValueError):
            return None

    async def webhook_routes(
        self, *, plugin_id: str, company_id: str
    ) -> frozenset[str] | None:
        """Manifest webhook allowlist, если store действительно его содержит."""
        record = await self._store.active_grant(plugin_id=plugin_id, company_id=company_id)
        if record is None or not record.active or record.webhooks is None:
            return None
        return frozenset(record.webhooks)

    async def egress_policy(self, *, plugin_id: str, company_id: str) -> dict[str, Any]:
        """Egress-правила из grant'а в БД для контроллера §6.3.

        Явно НЕ из JWT: истечение короткого токена не должно пересобирать
        сетевые правила посреди запроса (критерий №22). Нет grant'а →
        fail-closed ``mode="none"`` с пустыми allowlist'ами.
        """
        record = await self._store.active_grant(plugin_id=plugin_id, company_id=company_id)
        if record is None or not record.active:
            return {
                "mode": "none",
                "allowed_hosts": (),
                "allowed_paths": (),
                "approved_for_full": False,
                "grant_id": "",
            }
        return {
            "mode": record.egress_mode,
            "allowed_hosts": tuple(record.egress_allowed_hosts),
            "allowed_paths": tuple(record.egress_allowed_paths),
            "approved_for_full": record.egress_approved_for_full,
            "grant_id": record.grant_id,
        }


# ---------------------------------------------------------------------------
# SecretPort + HmacPort (ротация пары ключей)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SecretKeyRing:
    """Ссылки на ключи подписи webhook'а для пары ``(plugin, company)``.

    ``active_ref`` — текущий ключ; ``previous_refs`` — старые ключи, которые
    живут **до исчерпания in-flight доставок** (§7.5). Счётчик in-flight
    ведётся явно (в кластере — Redis/БД, задача A10): старый ref удаляется
    только через :meth:`SecretAdapter.retire_drained`, когда счётчик нулевой.
    """

    active_ref: str
    previous_refs: tuple[str, ...] = ()
    in_flight: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.active_ref.strip():
            raise ValueError("active_ref must not be empty")
        self.previous_refs = tuple(dict.fromkeys(r for r in self.previous_refs if r and r != self.active_ref))

    def rotate(self, new_ref: str) -> None:
        if not new_ref.strip() or new_ref == self.active_ref:
            raise ValueError("rotation requires a new, non-empty secret ref")
        self.previous_refs = tuple(dict.fromkeys((*self.previous_refs, self.active_ref)))
        self.active_ref = new_ref

    def all_refs(self) -> tuple[str, ...]:
        """Активный первым, затем старые (порядок важен для ``webhook_secret``)."""
        return (self.active_ref, *self.previous_refs)


SecretReader = Callable[[str], str]
RefResolver = Callable[[str, str], SecretKeyRing | None | Awaitable[SecretKeyRing | None]]


class InMemoryWebhookSecretRefs:
    """Реестр ``secret_ref`` для webhook'ов. Кластер → БД (задача A10)."""

    def __init__(self, rings: Mapping[tuple[str, str], SecretKeyRing] | None = None) -> None:
        self._rings: dict[tuple[str, str], SecretKeyRing] = dict(rings or {})

    def put(self, *, plugin_id: str, company_id: str, ring: SecretKeyRing) -> None:
        self._rings[(plugin_id, company_id)] = ring

    def get(self, *, plugin_id: str, company_id: str) -> SecretKeyRing | None:
        return self._rings.get((plugin_id, company_id))

    async def resolve(self, plugin_id: str, company_id: str) -> SecretKeyRing | None:
        return self.get(plugin_id=plugin_id, company_id=company_id)


class SecretAdapter:
    """``SecretPort``: webhook-секрет из secret store с парой ключей ротации.

    Берёт secret **только** через существующий ``RoutingSecretStore``
    (``infrastructure/secrets/store.py``), а не из БД/настроек/кода (§7.5).
    Ротация: ``webhook_secret`` возвращает активный ключ, но через
    :data:`_VERIFICATION_KEYS` (``ContextVar``, изолирован для каждой
    асинхронной задачи/запроса) передаёт :class:`Sha256HmacAdapter` **все**
    живые ключи. Старый ключ продолжает проверять подпись до тех пор, пока
    явно не retired после исчерпания in-flight (§7.5).

    Fail-closed: нет ring / нет секрета / store недоступен → ``None`` →
    ``GatewayAuth`` отдаёт ``503 SECRET_UNAVAILABLE``, а не «принять без
    подписи».
    """

    def __init__(
        self,
        *,
        refs: Any,
        secret_reader: SecretReader | None = None,
    ) -> None:
        if refs is None:
            raise GatewayAdapterError("SecretAdapter requires a secret-ref resolver")
        self._refs = refs
        self._secret_reader = secret_reader
        self.calls: list[tuple[str, str]] = []

    def _reader(self) -> SecretReader:
        if self._secret_reader is not None:
            return self._secret_reader
        from prodavan.infrastructure.secrets.store import get_secret_store

        return get_secret_store().get

    async def _ring(self, plugin_id: str, company_id: str) -> SecretKeyRing | None:
        resolver = getattr(self._refs, "resolve", None)
        if callable(resolver):
            value = resolver(plugin_id, company_id)
        else:
            value = self._refs(plugin_id, company_id)
        if inspect.isawaitable(value):
            value = await value
        return value if isinstance(value, SecretKeyRing) else None

    async def webhook_secrets(self, *, plugin_id: str, company_id: str) -> tuple[str, ...]:
        """Все живые секреты: активный + старые (без дублей и пустых)."""
        ring = await self._ring(plugin_id, company_id)
        if ring is None:
            return ()
        reader = self._reader()
        secrets: list[str] = []
        for ref in ring.all_refs():
            try:
                value = reader(ref)
            except Exception as exc:  # noqa: BLE001 — сбой store = канал недоступен
                raise GatewayAdapterError(
                    f"webhook secret store unavailable for ref: {type(exc).__name__}"
                ) from exc
            if isinstance(value, str) and value.strip() and value not in secrets:
                secrets.append(value)
        return tuple(secrets)

    async def webhook_secret(self, *, plugin_id: str, company_id: str) -> str | None:
        self.calls.append((plugin_id, company_id))
        # ContextVar всегда переустанавливается: иначе ключи прошлого запроса
        # могли бы «протечь» в следующий в том же контексте.
        _VERIFICATION_KEYS.set(())
        secrets = await self.webhook_secrets(plugin_id=plugin_id, company_id=company_id)
        if not secrets:
            _VERIFICATION_KEYS.set(())
            return None
        _VERIFICATION_KEYS.set(secrets)
        return secrets[0]  # активный ключ

    # -- явное управление in-flight (интегратор/outbox, §7.5) -----------------

    async def begin_in_flight(self, *, plugin_id: str, company_id: str, ref: str) -> None:
        ring = await self._ring(plugin_id, company_id)
        if ring is not None:
            ring.in_flight[ref] = ring.in_flight.get(ref, 0) + 1

    async def end_in_flight(self, *, plugin_id: str, company_id: str, ref: str) -> None:
        ring = await self._ring(plugin_id, company_id)
        if ring is not None and ring.in_flight.get(ref, 0) > 0:
            ring.in_flight[ref] -= 1

    async def retire_drained(self, *, plugin_id: str, company_id: str) -> tuple[str, ...]:
        """Удалить старые ref с НУЛЕВЫМ in-flight. Вернуть список удалённых.

        Вызывается только после подтверждения, что доставки, подписанные
        старым ключом, исчерпаны (§7.5). Автоматического TTL нет: удаление
        по времени оборвало бы in-flight вебхуки.
        """
        ring = await self._ring(plugin_id, company_id)
        if ring is None:
            return ()
        drained = tuple(ref for ref in ring.previous_refs if ring.in_flight.get(ref, 0) == 0)
        ring.previous_refs = tuple(ref for ref in ring.previous_refs if ref not in drained)
        for ref in drained:
            ring.in_flight.pop(ref, None)
        return drained


#: Ключи подписи текущего запроса. Заполняется ``SecretAdapter.webhook_secret``,
#: читается ``Sha256HmacAdapter.verify``. ContextVar имеет значение по умолчанию
#: ``()`` → адаптер работает и без ротации (ровно один секрет из ``secret``).
_VERIFICATION_KEYS: ContextVar[tuple[str, ...]] = ContextVar(
    "plugin_gateway_webhook_verification_keys", default=()
)


class Sha256HmacAdapter:
    """``HmacPort``: HMAC-SHA256 по ``raw_body``, ``compare_digest``, 64 hex.

    Формат ``X-Prodavan-Signature: sha256=<64 lowercase hex>`` (as-built
    ``docs/external-api.md:51`` / ``domain/projects/webhook_hmac.py``).
    Строгая валидация длины/алфавита ДО сравнения: иначе ``compare_digest``
    «проваливается» по длине и это утекает через тайминг.

    Ротация секретов (§7.5): если :class:`SecretAdapter` подготовил
    :data:`_VERIFICATION_KEYS`, проверяем подпись против КАЖДОГО живого ключа
    (активный + старый до исчерпания in-flight). Результаты объединяются
    через побитовое ИЛИ без раннего ``return`` — сравнение не становится
    короче для «первого же подошедшего» ключа.
    """

    def verify(
        self,
        *,
        secret: str,
        signing_bytes: bytes,
        signature_header: str | None,
    ) -> bool:
        if not isinstance(signature_header, str):
            return False
        header = signature_header.strip()
        if not header.startswith("sha256="):
            return False
        supplied = header[len("sha256=") :]
        if len(supplied) != 64 or any(c not in "0123456789abcdef" for c in supplied):
            return False
        if not isinstance(signing_bytes, (bytes, bytearray, memoryview)):
            return False
        body = bytes(signing_bytes)

        candidates: list[str] = []
        for value in (secret, *_VERIFICATION_KEYS.get(())):
            if isinstance(value, str) and value and value not in candidates:
                candidates.append(value)
        if not candidates:
            return False

        matched = False
        for candidate in candidates:
            expected = hmac.new(
                candidate.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            # Накапливаем, а не выходим раньше: постоянное время на фиксированном
            # наборе ключей; утечки «какой ключ подошёл» через тайминг нет.
            matched = hmac.compare_digest(expected, supplied) or matched
        return matched

    @staticmethod
    def sign(*, secret: str, body: bytes) -> str:
        """Сформировать заголовок подписи (для исходящих вебхуков/тестов)."""
        digest = hmac.new(secret.encode("utf-8"), bytes(body), hashlib.sha256).hexdigest()
        return f"sha256={digest}"


# ---------------------------------------------------------------------------
# RateLimitPort (Redis SET NX + отпечаток первой доставки)
# ---------------------------------------------------------------------------


_INCR_WITH_EXPIRE_LUA: Final[str] = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return current
"""

_CLAIM_DELIVERY_LEASE_LUA: Final[str] = """
local current = redis.call('GET', KEYS[1])
local token = ARGV[1]
local lease_ms = tonumber(ARGV[2])
local clock = redis.call('TIME')
local now_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
if not current then
  redis.call('SET', KEYS[1], 'processing:' .. (now_ms + lease_ms) .. ':' .. token, 'PX', lease_ms)
  return {'first', '', 0}
end
if string.sub(current, 1, 5) == 'done:' then
  return {'duplicate', string.sub(current, 6), 0}
end
if string.sub(current, 1, 11) == 'processing:' then
  local rest = string.sub(current, 12)
  local separator = string.find(rest, ':', 1, true)
  if separator then
    local expires_at = tonumber(string.sub(rest, 1, separator - 1))
    local previous = string.sub(rest, separator + 1)
    if expires_at and expires_at <= now_ms then
      redis.call('SET', KEYS[1], 'processing:' .. (now_ms + lease_ms) .. ':' .. token, 'PX', lease_ms)
      return {'first', previous, 0}
    end
    local retry = math.max(1, math.ceil((expires_at - now_ms) / 1000))
    return {'in_progress', previous, retry}
  end
end
return {'duplicate', current, 0}
"""

_COMMIT_DELIVERY_LUA: Final[str] = """
local current = redis.call('GET', KEYS[1])
local token = ARGV[1]
local ttl_seconds = tonumber(ARGV[2])
if current == 'done:' .. token then
  redis.call('EXPIRE', KEYS[1], ttl_seconds)
  return 1
end
if string.sub(current or '', 1, 11) == 'processing:' then
  local rest = string.sub(current, 12)
  local separator = string.find(rest, ':', 1, true)
  if separator and string.sub(rest, separator + 1) == token then
    redis.call('SET', KEYS[1], 'done:' .. token, 'EX', ttl_seconds)
    return 1
  end
end
return 0
"""

_RELEASE_DELIVERY_LUA: Final[str] = """
local current = redis.call('GET', KEYS[1])
local token = ARGV[1]
if string.sub(current or '', 1, 11) == 'processing:' then
  local rest = string.sub(current, 12)
  local separator = string.find(rest, ':', 1, true)
  if separator and string.sub(rest, separator + 1) == token then
    return redis.call('DEL', KEYS[1])
  end
end
return 0
"""

#: Namespace анонимных/публичных запросов: гарантирует ОТДЕЛЬНЫЙ bucket даже
#: если вызывающий забыл добавить subject (§7.2 «отдельный rate limit»).
ANON_BUCKET_PREFIX: Final[str] = "plugin-gateway:rl:anon"


class RedisRateLimitAdapter:
    """``RateLimitPort`` на Redis: INCR + processing/done Lua lifecycle.

    GatewayAuth использует ``claim_delivery_lease``: processing истекает по
    короткому TTL, а done создаётся только после backend 2xx (кроме 202).
    Legacy ``claim_delivery`` оставлен для прямых старых вызовов, но новый
    webhook flow его не использует.

    КЛЮЧЕВОЕ (риск повтора вебхука с чужим телом): ``claim_delivery``
    сохраняет **отпечаток ПЕРВОЙ доставки** (``token`` из
    ``GatewayAuth._replay_token``) в значении ключа. Простой ``SET NX`` без
    значения вернул бы ``DUPLICATE`` для любого повтора, и повтор с другим
    телом прошёл бы как «уже обработано» → ``200`` без обработки. Здесь
    повтор возвращает ``previous_token``, а ``GatewayAuth`` сравнивает его с
    новым отпечатком и при расхождении даёт ``401 REPLAY_DETECTED`` (§7.5).

    Fail-closed (в отличие от as-built ``core/infra/cache.py::rate_limit_allow``,
    который возвращает ``True`` при недоступном Redis): любая ошибка Redis
    даёт ``allow=False`` (429/503) или исключение из ``claim_delivery``.
    Доступность Redis — обязательное условие приёма вебхука.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        key_prefix: str = "plugin-gateway",
        anon_bucket_prefix: str = ANON_BUCKET_PREFIX,
    ) -> None:
        self._client = client  # None → ленивый get_redis_manager().client
        self._key_prefix = key_prefix
        self._anon_prefix = anon_bucket_prefix
        self.allow_calls: list[dict[str, Any]] = []
        self.claim_calls: list[dict[str, Any]] = []

    def _redis(self) -> Any:
        if self._client is not None:
            return self._client
        from prodavan.core.infra.redis_manager import get_redis_manager

        manager = get_redis_manager()
        if manager is None or not getattr(manager, "enabled", False):
            raise GatewayAdapterError("Redis is not available: rate limiting is fail-closed")
        return manager.client

    @staticmethod
    def _decode(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="strict")
        return str(value)

    def _namespaced(self, bucket: str) -> str:
        if not isinstance(bucket, str) or not bucket.strip():
            raise GatewayAdapterError("rate limit bucket must be a non-empty string")
        lowered = bucket.lower()
        anon = (
            ":anon" in lowered
            or ":public_page" in lowered
            or lowered.endswith("anon")
            or lowered.startswith(self._anon_prefix.lower())
        )
        namespace = self._anon_prefix if anon else f"{self._key_prefix}:rl:auth"
        # Отдельный namespace для anon даже при совпадении хвоста ключа с
        # авторизованным: публичная страница не может «съесть» чужую квоту.
        return f"{namespace}:{bucket}"

    async def allow(self, *, bucket: str, limit: int, window_seconds: int) -> bool:
        self.allow_calls.append(
            {"bucket": bucket, "limit": limit, "window_seconds": window_seconds}
        )
        if limit < 1 or window_seconds < 1:
            return False  # некорректная конфигурация ≠ «без лимита»
        key = self._namespaced(bucket)
        try:
            count = await self._redis().eval(
                _INCR_WITH_EXPIRE_LUA, 1, key, int(window_seconds) * 1000
            )
            return int(count) <= int(limit)
        except GatewayAdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 — Redis недоступен → deny
            raise GatewayAdapterError(
                f"rate limit backend unavailable: {type(exc).__name__}"
            ) from exc

    async def claim_delivery(
        self, *, key: str, ttl_seconds: int, token: str | None = None
    ) -> DeliveryVerdict:
        self.claim_calls.append({"key": key, "ttl_seconds": ttl_seconds, "token": token})
        if not isinstance(key, str) or not key.strip():
            raise GatewayAdapterError("delivery key must be a non-empty string")
        if ttl_seconds < 1:
            raise GatewayAdapterError("delivery ttl_seconds must be positive")
        # Значение ключа — ОТПЕЧАТОК ПЕРВОЙ ДОСТАВКИ. Пустая строка допустима
        # как «отпечаток не передан», но НЕ как «повтор любого тела».
        fingerprint = token if isinstance(token, str) else ""
        redis = self._redis()

        # Небольшой bounded-цикл на случай «SET NX проиграл, а TTL ключа уже
        # истёк между SET и GET»: повторяем атомарную попытку, а не отдаём
        # «уже обработано» без данных.
        for _attempt in range(3):
            try:
                acquired = await redis.set(
                    key, fingerprint, nx=True, ex=int(ttl_seconds)
                )
            except Exception as exc:  # noqa: BLE001 — сбой Redis = отказ приёма
                raise GatewayAdapterError(
                    f"delivery claim backend unavailable: {type(exc).__name__}"
                ) from exc
            if acquired:
                return DeliveryVerdict(
                    status=DeliveryStatus.FIRST, first_seen_at=time.time()
                )
            try:
                stored = self._decode(await redis.get(key))
            except Exception as exc:  # noqa: BLE001 — сбой Redis = отказ приёма
                raise GatewayAdapterError(
                    f"delivery claim backend unavailable: {type(exc).__name__}"
                ) from exc
            if stored is None:
                continue  # ключ исчез по TTL → пробуем занять заново
            return DeliveryVerdict(
                status=DeliveryStatus.DUPLICATE,
                first_seen_at=None,
                previous_token=stored,
            )
        raise GatewayAdapterError(
            "delivery claim is unstable: key expired concurrently; refusing webhook"
        )

    async def claim_delivery_lease(
        self,
        *,
        key: str,
        ttl_seconds: int,
        lease_seconds: int,
        token: str,
    ) -> DeliveryVerdict:
        """SET NX заменён атомарной processing/done машиной состояний."""
        if not key or ttl_seconds < 1 or lease_seconds < 1 or not token:
            raise GatewayAdapterError("invalid delivery lease arguments")
        try:
            raw = await self._redis().eval(
                _CLAIM_DELIVERY_LEASE_LUA,
                1,
                key,
                token,
                int(lease_seconds) * 1000,
            )
        except Exception as exc:
            raise GatewayAdapterError(
                f"delivery lease backend unavailable: {type(exc).__name__}"
            ) from exc
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise GatewayAdapterError("delivery lease backend returned malformed verdict")
        status = self._decode(raw[0])
        previous = self._decode(raw[1]) or None
        retry = int(raw[2] or 0)
        if status == DeliveryStatus.FIRST.value:
            return DeliveryVerdict(
                status=DeliveryStatus.FIRST,
                first_seen_at=time.time(),
                previous_token=previous,
            )
        if status == DeliveryStatus.IN_PROGRESS.value:
            return DeliveryVerdict(
                status=DeliveryStatus.IN_PROGRESS,
                previous_token=previous,
                retry_after_seconds=max(1, retry),
            )
        if status == DeliveryStatus.DUPLICATE.value:
            return DeliveryVerdict(
                status=DeliveryStatus.DUPLICATE,
                previous_token=previous,
            )
        raise GatewayAdapterError("delivery lease backend returned unknown status")

    async def commit_delivery(self, *, key: str, ttl_seconds: int, token: str) -> bool:
        if not key or ttl_seconds < 1 or not token:
            raise GatewayAdapterError("invalid delivery commit arguments")
        try:
            result = await self._redis().eval(
                _COMMIT_DELIVERY_LUA, 1, key, token, int(ttl_seconds)
            )
        except Exception as exc:
            raise GatewayAdapterError(
                f"delivery commit backend unavailable: {type(exc).__name__}"
            ) from exc
        return bool(int(result or 0))

    async def release_delivery(self, *, key: str, token: str) -> None:
        if not key or not token:
            raise GatewayAdapterError("invalid delivery release arguments")
        try:
            await self._redis().eval(_RELEASE_DELIVERY_LUA, 1, key, token)
        except Exception as exc:
            raise GatewayAdapterError(
                f"delivery release backend unavailable: {type(exc).__name__}"
            ) from exc

    async def allow_anon(self, *, subject: str, plugin_id: str, company_id: str, limit: int, window_seconds: int) -> bool:
        """Явный ОТДЕЛЬНЫЙ bucket для анонимной публичной страницы (§7.2)."""
        bucket = f"{self._anon_prefix}:{plugin_id}:{company_id}:{subject or 'anon'}"
        return await self.allow(bucket=bucket, limit=limit, window_seconds=window_seconds)


# ---------------------------------------------------------------------------
# ActivationPort — тонкий мост к PluginActivator (задача 09)
# ---------------------------------------------------------------------------


@runtime_checkable
class InstanceIdResolver(Protocol):
    async def resolve(self, *, plugin_id: str, company_id: str) -> str | None: ...


@runtime_checkable
class ReadinessLike(Protocol):
    async def is_ready(self, plugin_instance_id: str) -> bool: ...


class MappingInstanceResolver:
    """``(plugin_id, company_id) → instance_id`` из явного реестра.

    В кластере реестр — БД ``m05_plugin_instances`` / ``CapabilityGrant``
    (задача A10). Неизвестная пара → ``None`` → отказ (fail-closed).
    """

    def __init__(self, mapping: Mapping[tuple[str, str], str]) -> None:
        self._mapping = dict(mapping)
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, *, plugin_id: str, company_id: str) -> str | None:
        self.calls.append((plugin_id, company_id))
        value = self._mapping.get((plugin_id, company_id))
        return value.strip() if isinstance(value, str) and value.strip() else None


class ActivationAdapter:
    """КЛЮЧЕВОЙ адаптер: тонкий мост ``ActivationPort`` → ``PluginActivator``.

    Прямого структурного совпадения НЕТ (``ports.py::ActivationPort`` говорит
    ``(plugin_id, company_id)``, а задача 09 — ``wake(instance_id, *, timeout)
    -> WakeResult``). Мост обязан:

    1. **маппить** ``(plugin, company) → instance_id`` через явный реестр;
    2. вызвать ``PluginActivator.wake(instance_id, timeout=...)`` — ожидание
       Ready уже выполнено ВНУТРИ activator'а (§7.4, критерий 13);
    3. **обернуть ``WakeResult.token`` в ``WakeLease``**, переведя
       ``WakeResult.expires_at`` (aware UTC datetime) в epoch-float;
    4. ``wait_ready`` → повторный опрос ``ReadinessPort`` (не «всегда True»);
       если порт не подключён — ``lease.ready`` (activator уже дождался Ready).

    Свежий токен из lease ``GatewayProxy`` кладёт в
    ``X-Plugin-Capability-Token``; :func:`with_trusted_bearer` дополнительно
    выставляет ``Authorization: Bearer <WakeResult.token>`` — см. шапку модуля.
    """

    def __init__(
        self,
        *,
        activator: Any,
        instance_resolver: Any,
        readiness: ReadinessLike | None = None,
        timeout: float | None = None,
        lease_ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if activator is None or not callable(getattr(activator, "wake", None)):
            raise GatewayAdapterError("ActivationAdapter requires PluginActivator.wake")
        if instance_resolver is None:
            raise GatewayAdapterError("ActivationAdapter requires an instance resolver")
        self._activator = activator
        self._instance_resolver = instance_resolver
        self._readiness = readiness
        self._timeout = timeout
        self._lease_ttl_seconds = float(lease_ttl_seconds)
        self._clock = clock
        self.wake_calls: list[tuple[str, str, str]] = []
        self.wait_calls: list[tuple[str, float]] = []
        self.last_token_jti: str | None = None

    async def _instance_id(
        self,
        *,
        plugin_id: str,
        company_id: str,
        expected_instance_id: str | None = None,
    ) -> str:
        resolver = getattr(self._instance_resolver, "resolve", None)
        if callable(resolver):
            value = resolver(plugin_id=plugin_id, company_id=company_id)
        elif callable(self._instance_resolver):
            value = self._instance_resolver(plugin_id, company_id)
        else:
            value = None
        if inspect.isawaitable(value):
            value = await value
        mapped = value.strip() if isinstance(value, str) and value.strip() else None
        if expected_instance_id is not None:
            if not isinstance(expected_instance_id, str) or not expected_instance_id.strip():
                raise GatewayAdapterError(
                    "durable grant plugin_instance_id is empty; wake-up refused"
                )
            expected = expected_instance_id.strip()
            if mapped is not None and mapped != expected:
                raise GatewayAdapterError(
                    "durable grant and activation resolver plugin_instance_id mismatch"
                )
            # The durable grant is authoritative; the resolver is only a
            # consistency check and must never redirect to another instance.
            return expected
        if mapped is None:
            raise GatewayAdapterError(
                f"no plugin instance_id for ({plugin_id!r}, {company_id!r}); "
                "wake-up refused (fail-closed)"
            )
        return mapped

    @staticmethod
    def _expires_epoch(result: Any, *, now: float, ttl_seconds: float) -> float:
        expires = getattr(result, "expires_at", None)
        if isinstance(expires, datetime):
            if expires.tzinfo is None or expires.utcoffset() is None:
                raise GatewayAdapterError("WakeResult.expires_at must be timezone-aware")
            return float(expires.timestamp())
        # Fail-closed: без достоверного exp не выпускаем «вечный» lease.
        return float(now + ttl_seconds)

    async def wake(
        self,
        *,
        plugin_id: str,
        company_id: str,
        plugin_instance_id: str | None = None,
    ) -> WakeLease:
        instance_id = await self._instance_id(
            plugin_id=plugin_id,
            company_id=company_id,
            expected_instance_id=plugin_instance_id,
        )
        self.wake_calls.append((plugin_id, company_id, instance_id))
        result = await self._activator.wake(instance_id, timeout=self._timeout)
        result_instance = getattr(result, "plugin_instance_id", None)
        if (
            not isinstance(result_instance, str)
            or not result_instance.strip()
            or result_instance != instance_id
        ):
            raise GatewayAdapterError(
                "PluginActivator returned a missing or different plugin_instance_id"
            )
        token = getattr(result, "token", None)
        if not isinstance(token, str) or not token.strip():
            raise GatewayAdapterError("WakeResult.token is missing: refusing to forward")
        token_claims = getattr(result, "claims", None)
        if not isinstance(token_claims, Mapping):
            raise GatewayAdapterError("WakeResult.claims is missing: refusing to forward")
        artifact_digest = token_claims.get("artifact_digest")
        if not is_valid_artifact_digest(artifact_digest):
            raise GatewayAdapterError(
                "WakeResult.claims.artifact_digest is missing or malformed"
            )
        grant_id = token_claims.get("grant_id")
        if not isinstance(grant_id, str) or not grant_id.strip():
            raise GatewayAdapterError("WakeResult.claims.grant_id is missing")
        for claim_name, expected in (
            ("plugin_instance_id", result_instance),
            ("plugin_id", plugin_id),
            ("company_id", company_id),
        ):
            bound = token_claims.get(claim_name)
            if bound != expected:
                raise GatewayAdapterError(
                    "WakeResult.claims tenant/plugin/instance binding is missing or mismatched"
                )
        expected_sub = f"plugin:{plugin_id}:{company_id}:{result_instance}"
        if token_claims.get("sub") != expected_sub:
            raise GatewayAdapterError(
                "WakeResult.claims.sub is missing or has a different identity binding"
            )
        self.last_token_jti = getattr(result, "jti", None)
        now = float(self._clock())
        return WakeLease(
            # Project only authoritative result/claims after exact validation;
            # never reconstruct trust by copying the requested binding.
            plugin_id=token_claims["plugin_id"],
            company_id=token_claims["company_id"],
            plugin_instance_id=result_instance,
            capability_token=token.strip(),
            artifact_digest=artifact_digest,
            grant_id=grant_id,
            expires_at=self._expires_epoch(
                result, now=now, ttl_seconds=self._lease_ttl_seconds
            ),
            ready=True,  # activator УЖЕ дождался Ready внутри wake
        )

    async def wait_ready(self, lease: WakeLease, *, timeout_seconds: float) -> bool:
        self.wait_calls.append((lease.plugin_id, timeout_seconds))
        # Просроченный lease = не годен к проксированию (§4.2 короткий TTL).
        if lease.expires_at <= float(self._clock()):
            return False
        if not is_valid_artifact_digest(lease.artifact_digest) or not lease.grant_id:
            return False
        if self._readiness is None:
            return bool(lease.ready)
        try:
            instance_id = lease.plugin_instance_id
            if not isinstance(instance_id, str) or not instance_id.strip():
                return False
            return bool(await self._readiness.is_ready(instance_id))
        except Exception:  # noqa: BLE001 — сбой пробы Ready ≠ «готов»
            return False


# ---------------------------------------------------------------------------
# BackendHttpPort — httpx, SSE без буферизации
# ---------------------------------------------------------------------------


class BackendHttpAdapter:
    """``BackendHttpPort``: ``httpx.AsyncClient`` + живой SSE-итератор.

    * **серверный таймаут**: берётся из ``BackendRequest.timeout_seconds``
      (``GatewayLimits``, а НЕ тело запроса, §4.4) и дополнительно
      ограничивается потолком адаптера ``max_timeout_seconds``;
    * **bounded non-SSE response**: ``client.send(stream=True)`` исключает
      скрытую буферизацию httpx; тело читается чанками до
      ``BackendRequest.max_response_bytes`` и закрывается при превышении;
    * **SSE без буферизации**: при ``request.stream=True`` НЕ вызывается
      ``aread()``/``.content``, ``b"".join(...)`` — возвращается живой
      ``AsyncIterator[bytes]`` из ``response.aiter_bytes()``; соединение
      закрывается в ``finally`` генератора, когда потребитель дочитал поток;
    * ``X-Accel-Buffering: no`` + ``Cache-Control: no-cache, no-transform``
      выставляются в заголовках потокового ответа (запрет буферизации на
      nginx/Traefik);
    * ``follow_redirects=False``, ``trust_env=False``: redirect и TLS
      проверяются на каждом хопе egress-контроллером (§6.3), а переменные
      окружения не должны подменять прокси;
    * **доверенный Bearer** (:func:`with_trusted_bearer`) добавляется ЯВНО
      из ``X-Plugin-Capability-Token`` — см. шапку модуля.

    ``client`` можно не передавать: тогда создаётся собственный
    ``httpx.AsyncClient`` (и закрывается через :meth:`aclose`). Для тестов
    подменяется duck-typed клиент с ``build_request``/``send``.
    """

    def __init__(
        self,
        *,
        base_url: str,
        client: Any = None,
        max_timeout_seconds: float = 300.0,
        inject_trusted_bearer: bool = True,
        client_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise GatewayAdapterError("BackendHttpAdapter requires a base_url")
        from urllib.parse import urlsplit

        parts = urlsplit(base_url.strip())
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
            raise GatewayAdapterError("base_url must be an http(s) URL without credentials")
        self._base_url = base_url.strip().rstrip("/")
        self._max_timeout_seconds = float(max_timeout_seconds)
        self._inject_trusted_bearer = bool(inject_trusted_bearer)
        self._owns_client = client is None
        self._client = client
        self._client_kwargs = dict(client_kwargs or {})
        self.sent_requests: list[BackendRequest] = []

    @property
    def inject_trusted_bearer(self) -> bool:
        return self._inject_trusted_bearer

    def _get_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._max_timeout_seconds),
                follow_redirects=False,
                trust_env=False,
                **self._client_kwargs,
            )
        return self._client

    def _url(self, path: str, raw_query: bytes = b"") -> str:
        if not isinstance(path, str) or not path.startswith("/"):
            raise GatewayAdapterError("backend path must start with '/'")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
            raise GatewayAdapterError("backend path contains control characters")
        try:
            query = canonical_raw_query(raw_query).decode("ascii")
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise GatewayAdapterError("raw query is not a safe ASCII request target") from exc
        suffix = f"?{query}" if query else ""
        return f"{self._base_url}{path}{suffix}"

    def _outgoing_headers(self, request: BackendRequest) -> dict[str, str]:
        token = _header_get(request.headers, HEADER_CAPABILITY_TOKEN)
        if self._inject_trusted_bearer:
            # ЯВНО: оба заголовка (X-Plugin-Capability-Token и
            # Authorization: Bearer <WakeResult.token>) в доверенном наборе.
            return with_trusted_bearer(request.headers, capability_token=token)
        # Интегратор мог выключить инъекцию ТОЛЬКО если сам обеспечивает
        # Authorization: Bearer; клиентский заголовок всё равно отбрасываем.
        out = {
            name: value
            for name, value in request.headers.items()
            if isinstance(name, str)
            and isinstance(value, str)
            and value
            and name.lower() not in _UNTRUSTED_REQUEST_HEADERS
        }
        return out

    @staticmethod
    def _response_headers(raw: Any, *, stream: bool) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, value in raw.headers.items():
            if not isinstance(name, str) or not isinstance(value, str) or not value:
                continue
            if name.lower() in _HOP_BY_HOP_RESPONSE_HEADERS:
                continue
            out[name] = value
        if stream:
            # Прямой запрет буферизации на reverse-proxy (§7.2, proxy.py:432).
            out["Cache-Control"] = "no-cache, no-transform"
            out["X-Accel-Buffering"] = "no"
        return out

    async def send(self, request: BackendRequest) -> BackendResponse:
        if not isinstance(request, BackendRequest):
            raise GatewayAdapterError("send expects BackendRequest")
        self.sent_requests.append(request)
        timeout = min(float(request.timeout_seconds), self._max_timeout_seconds)
        if timeout <= 0:
            raise GatewayAdapterError("backend timeout must be positive")

        client = self._get_client()
        built = client.build_request(
            request.method.upper(),
            self._url(request.path, request.query_string),
            headers=self._outgoing_headers(request),
            content=bytes(request.body),
            timeout=timeout,
        )
        # Таймаут задаётся на build_request (httpx.AsyncClient.send не принимает
        # ``timeout``). stream=True нужен ДЛЯ ВСЕХ ответов: stream=False заставил
        # бы httpx буферизовать non-SSE тело ещё до нашего configured cap.
        raw = await client.send(built, stream=True)
        headers = self._response_headers(raw, stream=bool(request.stream))

        if request.stream:
            async def _chunks() -> AsyncIterator[bytes]:
                try:
                    async for chunk in raw.aiter_bytes():
                        yield chunk
                finally:
                    await raw.aclose()

            # НИКОГДА не вызываем aread()/content для потокового ответа:
            # тело нигде не собирается в bytes (SSE без буферизации).
            return BackendResponse(
                status_code=raw.status_code,
                headers=headers,
                body=None,
                stream=_chunks(),
            )

        try:
            declared_header = _header_get(raw.headers, "content-length")
            if declared_header is not None:
                if not declared_header.isascii() or not declared_header.isdigit():
                    raise GatewayAdapterError("invalid upstream Content-Length")
                try:
                    declared = int(declared_header, 10)
                except (TypeError, ValueError) as exc:
                    raise GatewayAdapterError("invalid upstream Content-Length") from exc
                if declared < 0:
                    raise GatewayAdapterError("invalid upstream Content-Length")
                if declared > request.max_response_bytes:
                    raise BackendResponseTooLargeError(
                        limit_bytes=request.max_response_bytes
                    )
            body = bytearray()
            async for chunk in raw.aiter_bytes():
                if not chunk:
                    continue
                if len(chunk) > request.max_response_bytes - len(body):
                    raise BackendResponseTooLargeError(
                        limit_bytes=request.max_response_bytes
                    )
                body.extend(chunk)
        finally:
            await raw.aclose()
        return BackendResponse(
            status_code=raw.status_code,
            headers=headers,
            body=bytes(body),
            stream=None,
        )

    async def aclose(self) -> None:
        client = self._client
        if client is not None and self._owns_client:
            await client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# AuditPort — единая схема §4.5 + redaction
# ---------------------------------------------------------------------------


@runtime_checkable
class AppendOnlyAuditSink(Protocol):
    """Append-only sink. Никаких update/delete в контракте (§4.5)."""

    async def write(self, record: Mapping[str, Any]) -> None: ...


#: Ключи, которые НИКОГДА не попадают в аудит (§4.5, критерий №11).
_FORBIDDEN_AUDIT_KEY_PARTS: Final[tuple[str, ...]] = (
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "credential",
    "api_key",
    "apikey",
    "signature",
    "raw_body",
    "raw_payload",
    "body",
    "payload",
    "sql",
    "params",
    "dsn",
    "jwt",
    "private_key",
    "email",
    "phone",
    "pii",
)

_FORBIDDEN_AUDIT_VALUE_PARTS: Final[tuple[str, ...]] = (
    "bearer ",
    "sha256=",
    "authorization:",
    "cookie:",
    "password=",
    "secret=",
    "-----begin",
)

_REDACTED: Final[str] = "[redacted]"


def _looks_like_jwt(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 3 and all(len(part) >= 8 for part in parts)


def _safe_scalar(value: Any) -> Any:
    """Пропустить ТОЛЬКО короткие скаляры без секретов/тел/токенов."""
    if value is None or isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 256:
        return None
    lowered = text.lower()
    if any(part in lowered for part in _FORBIDDEN_AUDIT_VALUE_PARTS):
        return _REDACTED
    if _looks_like_jwt(text):
        return _REDACTED
    return text


def _redact_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            continue
        lowered = key.lower()
        if any(part in lowered for part in _FORBIDDEN_AUDIT_KEY_PARTS):
            continue  # ключ целиком отбрасываем: даже имя может выдать схему
        safe = _safe_scalar(value)
        if safe is not None:
            out[key] = safe
    return out


def _valid_digest(value: Any) -> str:
    """Только канонический digest, НИКОГДА само тело/payload (§4.5)."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if _DIGEST_64_RE.fullmatch(text) or _DIGEST_CANONICAL_RE.fullmatch(text):
        return text
    return ""  # мусор/raw payload отбрасываем, а не «как-нибудь запишем»


class AuditAdapter:
    """``AuditPort``: ``GatewayAuditEvent`` → единая схема §4.5 + redaction.

    Канонические поля: ``request_id``, ``run_id``/``correlation_id``,
    ``company_id``, ``cabinet_id``, ``project_id``, ``subject_id``,
    ``capability``, ``operation``, ``resource``, ``decision``, ``code``,
    ``grant_id``, ``reason``. Плюс ``event_type``, ``occurred_at``,
    ``schema_version``, ``redaction`` и ТОЛЬКО digest-ы
    (``payload_digest`` / ``path_digest``).

    В событии **никогда** нет raw SQL, параметров, тел payload, токенов,
    секретов, PII (критерий №11). Redaction применяется и к ``metadata``,
    и к ``reason``, и к идентификаторам.

    Append-only: sink обязан только дописывать. Ошибка записи пробрасывается
    (``GatewayAuth`` превращает её в ``AUDIT_APPEND_FAILED``/503 = deny для
    позитивной мутации, §4.5) — НЕ глушится и НЕ логируется «на всякий случай».
    """

    AUDIT_SCHEMA_VERSION: Final[str] = "plugin-gateway-audit/1"

    def __init__(self, sink: AppendOnlyAuditSink) -> None:
        if sink is None or not callable(getattr(sink, "write", None)):
            raise GatewayAdapterError("AuditAdapter requires an append-only sink with write()")
        self._sink = sink
        self.records: list[dict[str, Any]] = []

    def map_event(self, event: GatewayAuditEvent) -> dict[str, Any]:
        """Чистая функция маппинга (удобно тестировать без sink'а)."""
        if not isinstance(event, GatewayAuditEvent):
            raise GatewayAdapterError("AuditAdapter.record expects GatewayAuditEvent")
        metadata = event.metadata if isinstance(event.metadata, Mapping) else {}

        def _meta(name: str) -> Any:
            lowered = name.lower()
            return next(
                (value for key, value in metadata.items() if isinstance(key, str) and key.lower() == lowered),
                None,
            )

        def _safe_id(value: Any) -> str:
            safe = _safe_scalar(value)
            return safe if isinstance(safe, str) and _SAFE_ID_RE.fullmatch(safe) else ""

        reason = _safe_scalar(event.reason)
        record: dict[str, Any] = {
            "event_type": _safe_scalar(event.event) or "",
            "occurred_at": datetime.now(UTC).isoformat(),
            "schema_version": self.AUDIT_SCHEMA_VERSION,
            "redaction": "canonical",
            "request_id": _safe_id(event.request_id),
            "run_id": _safe_id(_meta("run_id")),
            "correlation_id": _safe_id(_meta("correlation_id")),
            "company_id": _safe_id(event.company_id),
            "cabinet_id": _safe_id(_meta("cabinet_id")),
            "project_id": _safe_id(_meta("project_id")),
            "subject_id": _safe_id(event.subject_id),
            "capability": _safe_id(_meta("capability")),
            "operation": _safe_id(_meta("operation")) or _safe_id(event.event),
            "resource": _safe_id(_meta("resource")) or _valid_digest(event.path_digest),
            "decision": _safe_id(event.decision),
            "code": _safe_id(event.code),
            "grant_id": _safe_id(event.grant_id),
            "reason": reason if isinstance(reason, str) else "",
            "payload_digest": _valid_digest(event.payload_digest),
            "path_digest": _valid_digest(event.path_digest),
            "metadata": _redact_metadata(metadata),
        }
        # Финальная перестраховка: ни одно значение не может содержать
        # запрещённый фрагмент (в т.ч. случайно собранный из metadata).
        for key, value in list(record.items()):
            if isinstance(value, str) and any(
                part in value.lower() for part in _FORBIDDEN_AUDIT_VALUE_PARTS
            ):
                record[key] = _REDACTED
        return record

    async def record(self, event: GatewayAuditEvent) -> None:
        mapped = self.map_event(event)
        await self._sink.write(mapped)
        # Копия для тестов/диагностики; append-only, без изменения записанного.
        self.records.append(dict(mapped))


__all__ = [
    "ANON_BUCKET_PREFIX",
    "ANON_SCOPES",
    "BEARER_PREFIX",
    "CLUSTER_BACKEND_NOTE",
    "HEADER_AUTHORIZATION",
    "ActivationAdapter",
    "AppendOnlyAuditSink",
    "AuditAdapter",
    "BackendHttpAdapter",
    "BackendSender",
    "BearerAuthorizationInjector",
    "CookieSessionPort",
    "EmployeeDirectoryPort",
    "GatewayAdapterError",
    "GrantAdapter",
    "GrantRecord",
    "GrantStorePort",
    "InMemoryCookieSessionStore",
    "InMemoryGrantStore",
    "InMemoryWebhookSecretRefs",
    "InstanceIdResolver",
    "MappingInstanceResolver",
    "ReadinessLike",
    "RedisRateLimitAdapter",
    "SecretAdapter",
    "SecretKeyRing",
    "SessionAdapter",
    "Sha256HmacAdapter",
    "SqlEmployeeDirectory",
    "with_trusted_bearer",
]
