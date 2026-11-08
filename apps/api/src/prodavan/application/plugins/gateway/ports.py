"""Контракты (Ports) Plugin Gateway (F6, §7.2/§7.4/§7.5 дизайна v4).

Шлюз — всегда-running сервис, но он **не владеет** реализациями: сессии, grant'ы,
секреты, активация backend'а, HTTP-транспорт, аудит и rate limit приходят извне
через эти Protocol. Композиция — задача родителя/интегратора (F11). Отсутствие
любого порта = fail-closed ошибка, а не «деградация до разрешения».

Протоколы намеренно структурные (``typing.Protocol``): задача 09 (activator),
задача 02 (identity), задача 04 (transport) и др. реализуют их в своих каталогах,
не импортируя этот модуль и не создавая циклических зависимостей.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Доверенные значения запроса (строятся GatewayAuth, а не клиентом)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GatewayClaims:
    """Доверенный identity одного проксируемого запроса.

    Это НЕ ``PluginClaims`` (JWT плагина для /capabilities/v1/*). Здесь —
    уже проверенная шлюзом связка «кто / куда», из которой ``headers.py``
    строит исходящие заголовки. Клиент не может повлиять ни на одно поле.

    ``anonymous=True`` только для явно объявленных ``public_pages``: тогда
    ``scopes`` урезан до ``ANON_SCOPES``, ``capability_token`` не выдаётся.
    """

    plugin_id: str
    company_id: str
    subject_id: str
    scopes: frozenset[str] = frozenset()
    project_id: str | None = None
    grant_id: str | None = None
    anonymous: bool = False
    employee_id: str | None = None

    def __post_init__(self) -> None:
        # Fail-closed: пустые идентификаторы не должны попасть в доверенные
        # заголовки — иначе downstream получит «доверенную» пустоту.
        for name in ("plugin_id", "company_id", "subject_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
            # Значения уходят в HTTP-заголовки: CRLF/управляющие символы
            # недопустимы (иначе — header injection / request splitting).
            if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
                raise ValueError(f"{name} must not contain control characters")
        if self.project_id is not None and (
            not isinstance(self.project_id, str)
            or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in self.project_id)
        ):
            raise ValueError("project_id must not contain control characters")
        if self.anonymous and self.employee_id is not None:
            raise ValueError("anonymous claims must not carry an employee_id")


@dataclass(frozen=True, slots=True)
class GatewayGrant:
    """Активный grant плагина + короткий capability-token из lease wake-up.

    ``capability_token`` перезаписывает одноимённый входящий заголовок. Для
    anon/public_pages он пуст: публичная страница не получает capability-токен.
    """

    grant_id: str
    active: bool = True
    capability_token: str = ""
    operations: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.grant_id.strip():
            raise ValueError("grant_id must not be empty")


#: Урезанный scope анонимной публичной страницы (§12, вопрос 1 — формат anon-scope).
#: Никаких capability, никакого project, никакого employee-контекста.
ANON_SCOPES: frozenset[str] = frozenset({"pluginpage:public:read"})

#: Scope обычной (авторизованной) страницы плагина.
SESSION_SCOPES: frozenset[str] = frozenset(
    {"pluginpage:read", "pluginpage:write", "pluginpage:api"}
)

#: Scope входящего вебхука — только приём события, без доступа к страницам.
WEBHOOK_SCOPES: frozenset[str] = frozenset({"webhook:receive"})


# ---------------------------------------------------------------------------
# Сессия / сотрудник
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GatewaySession:
    """Разрешённая сессия сотрудника (после проверки подписи/куки авторизации)."""

    session_id: str
    employee_id: str
    subject_id: str
    company_ids: frozenset[str] = frozenset()


class SessionPort(Protocol):
    """Разрешение сессии и проверка ``employee ∈ company``.

    Реализация (задача 02 / общий auth-контур) сама решает, откуда брать
    сессию: OIDC bearer, cookie, app key. Шлюз передаёт только «сырой»
    маркер сессии и не интерпретирует его формат.
    """

    async def resolve(self, *, session_token: str | None) -> GatewaySession | None:
        """Вернуть сессию или ``None``. ``None`` = сессии нет (fail-closed)."""
        ...

    async def employee_in_company(self, *, employee_id: str, company_id: str) -> bool:
        """Явная проверка членства сотрудника в компании (§7.2)."""
        ...


class GrantPort(Protocol):
    """Проверка активного grant/bind и реестр ``public_pages`` из манифеста."""

    async def active_grant(
        self, *, plugin_id: str, company_id: str
    ) -> GatewayGrant | None:
        """Активный grant для пары (plugin, company) или ``None``."""
        ...

    async def public_pages(
        self, *, plugin_id: str, company_id: str
    ) -> frozenset[str]:
        """Явно объявленные публичные пути (точное совпадение или префикс ``/``).

        Пустой набор = публичных страниц нет, анонимный доступ запрещён.
        """
        ...


class SecretPort(Protocol):
    """Секрет HMAC из secret store (НЕ из БД и НЕ из кода/настроек, §7.5)."""

    async def webhook_secret(self, *, plugin_id: str, company_id: str) -> str | None:
        """Вернуть активный секрет, ``None`` = канал не настроен (fail-closed)."""
        ...


class HmacPort(Protocol):
    """Проверка HMAC константным по времени сравнением.

    ``signing_bytes`` формируется вызывающей стороной: по умолчанию это ровно
    ``raw_body`` (as-built контракт ``docs/external-api.md:51``).
    """

    def verify(
        self,
        *,
        secret: str,
        signing_bytes: bytes,
        signature_header: str | None,
    ) -> bool: ...


# ---------------------------------------------------------------------------
# Rate limit / идемпотентность / replay
# ---------------------------------------------------------------------------


class DeliveryStatus(StrEnum):
    FIRST = "first"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class DeliveryVerdict:
    """Результат атомарной проверки ``X-Prodavan-Delivery``.

    ``previous_token`` — HMAC-токен ПЕРВОЙ доставки. Повтор с тем же
    delivery-id возвращает ``200`` без повторной обработки **только если**
    предъявленный токен совпадает с сохранённым: иначе это подделка/
    повтор с чужим replay-токеном → ``401`` (защита от replay, §7.5).
    """

    status: DeliveryStatus
    first_seen_at: float | None = None
    previous_token: str | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.status is DeliveryStatus.DUPLICATE


class RateLimitPort(Protocol):
    """Rate limit и атомарный claim идемпотентности (защита от replay).

    Оба метода обязаны быть атомарными в реализации (Redis SET NX + TTL /
    INCR + EXPIRE): гонка между двумя ретраями не должна привести к двойной
    обработке вебхука.
    """

    async def allow(self, *, bucket: str, limit: int, window_seconds: int) -> bool:
        """``True`` = запрос разрешён, ``False`` = лимит исчерпан (429)."""
        ...

    async def claim_delivery(
        self, *, key: str, ttl_seconds: int, token: str | None = None
    ) -> DeliveryVerdict:
        """Атомарно зарегистрировать delivery-id + отпечаток первой доставки.

        ``token`` — детерминированный digest связки (delivery, event, body),
        который сохраняется при ПЕРВОМ вызове. Повтор возвращает
        ``DUPLICATE`` с ``previous_token``; шлюз отказывает, если новый
        ``token`` не совпал (защита от replay, §7.5).
        """
        ...


# ---------------------------------------------------------------------------
# Активация backend'а (реализация — задача 09)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WakeLease:
    """Результат wake-up: backend Ready + новый короткий capability-token.

    §7.4: Gateway патчит ``deployments/scale`` и **удерживает соединение до
    Ready**, затем проксирует. ``ensure_running`` из SandboxClaim не ждёт Ready
    и для плагинов не применяется.
    """

    plugin_id: str
    company_id: str
    capability_token: str
    expires_at: float
    ready: bool = True

    def __post_init__(self) -> None:
        if not self.plugin_id.strip() or not self.company_id.strip():
            raise ValueError("plugin_id/company_id must not be empty")
        if not self.capability_token.strip():
            raise ValueError("wake lease must carry a capability token")


class ActivationPort(Protocol):
    """Пробуждение спящего backend'а плагина (задача 09, ADR-008).

    ИНТЕГРАЦИЯ (для родителя, F11): задача 09 экспортирует
    ``PluginActivator.wake(instance_id, *, timeout) -> WakeResult``, где
    ``WakeResult.token`` — свежий ``PluginClaims``, а само ожидание Ready уже
    выполнено ВНУТРИ ``wake``. Этот Protocol намеренно минимален и описывает
    «контракт шлюза», а не сигнатуру activator'а: интегратор подкладывает
    ТОНКИЙ адаптер, который

    * маппит ``(plugin_id, company_id)`` → ``instance_id``;
    * вызывает ``PluginActivator.wake(...)`` и оборачивает ``WakeResult.token``
      в ``WakeLease``;
    * ``wait_ready`` возвращает ``True`` (activator уже дождался Ready) либо
      повторно опрашивает ``ReadinessPort``.

    Прямого структурного совпадения НЕТ (ключи ``plugin_instance_id`` vs
    ``plugin_id/company_id``) — это осознанная граница, чтобы шлюз не зависел
    от доменных типов задачи 09. См. отчёт F6 родителю.
    """

    async def wake(self, *, plugin_id: str, company_id: str) -> WakeLease:
        """Инициировать 0→1 и вернуть lease. ДОЛЖЕН вызываться до forward."""
        ...

    async def wait_ready(
        self, lease: WakeLease, *, timeout_seconds: float
    ) -> bool:
        """Удержать запрос до Ready. ``False`` = не дождались (202 + Retry-After)."""
        ...


# ---------------------------------------------------------------------------
# HTTP к backend'у / статике
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackendRequest:
    """Один исходящий запрос к backend'у плагина или static-origin."""

    path: str
    method: str
    headers: Mapping[str, str]
    body: bytes = b""
    timeout_seconds: float = 30.0
    #: True = ответ потоковый (SSE/стриминг): НЕ буферизовать, НЕ ретраить.
    stream: bool = False

    def __post_init__(self) -> None:
        if not self.path.startswith("/"):
            raise ValueError("backend path must start with '/'")


@dataclass(slots=True)
class BackendResponse:
    """Ответ upstream. Ровно одно из ``body`` / ``stream`` должно быть задано."""

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    stream: AsyncIterator[bytes] | None = None

    @property
    def is_streaming(self) -> bool:
        return self.stream is not None


class BackendHttpPort(Protocol):
    """Транспорт к backend'у плагина / static-origin.

    Реализация не должна буферизовать потоковый ответ: ``BackendResponse.stream``
    — это живой async-итератор, который роутер отдаёт в ``StreamingResponse``.
    """

    async def send(self, request: BackendRequest) -> BackendResponse: ...


# ---------------------------------------------------------------------------
# Аудит
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GatewayAuditEvent:
    """Каноническая запись аудита шлюза (§4.5): только идентификаторы, без PII.

    В событии **никогда** нет raw body, параметров, токенов, секретов, SQL.
    ``payload_digest`` — опциональный sha256-hex тела (не само тело).
    """

    event: str
    plugin_id: str
    company_id: str
    decision: str
    code: str = ""
    subject_id: str = ""
    grant_id: str = ""
    path_digest: str = ""
    payload_digest: str = ""
    reason: str = ""
    request_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


class AuditPort(Protocol):
    """Append-only аудит. Ошибка записи = deny/system failure (§4.5)."""

    async def record(self, event: GatewayAuditEvent) -> None: ...


__all__ = [
    "ANON_SCOPES",
    "SESSION_SCOPES",
    "WEBHOOK_SCOPES",
    "ActivationPort",
    "AuditPort",
    "BackendHttpPort",
    "BackendRequest",
    "BackendResponse",
    "DeliveryStatus",
    "DeliveryVerdict",
    "GatewayAuditEvent",
    "GatewayClaims",
    "GatewayGrant",
    "GatewaySession",
    "GrantPort",
    "HmacPort",
    "RateLimitPort",
    "SecretPort",
    "SessionPort",
    "WakeLease",
]
