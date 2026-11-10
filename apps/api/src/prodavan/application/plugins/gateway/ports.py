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

from prodavan.domain.plugins.origins import exact_https_origin

from .canonical import canonical_backend_path, canonical_raw_query

# ---------------------------------------------------------------------------
# Доверенные значения запроса (строятся GatewayAuth, а не клиентом)
# ---------------------------------------------------------------------------


def is_valid_artifact_digest(value: object) -> bool:
    """Canonical OCI/content digest: exactly ``sha256:<64 lowercase hex>``."""
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


@dataclass(frozen=True, slots=True)
class StaticResponsePolicy:
    """Server-owned security policy for successful static artifact responses.

    This is configuration supplied by the server composition, never a request or
    manifest field. Cross-origin iframes allow only the validated server shell
    origin, never implicit 'self' or a wildcard. CSP directives required by the
    publication evidence gate are constructed from typed fields so a client
    cannot supply a complete header value.
    """

    shell_origin: str
    default_src: tuple[str, ...] = ("'self'",)
    frame_ancestors: tuple[str, ...] = field(init=False)
    referrer_policy: str = "no-referrer"
    permissions_policy: str = "camera=(), microphone=(), geolocation=()"

    def __post_init__(self) -> None:
        canonical_shell = exact_https_origin(
            self.shell_origin, setting="PLUGIN_SHELL_ORIGIN"
        )
        object.__setattr__(self, "shell_origin", canonical_shell)
        object.__setattr__(self, "frame_ancestors", (canonical_shell,))
        sources = self.default_src
        if (
            not isinstance(sources, tuple)
            or not sources
            or any(
                not isinstance(source, str)
                or not source.strip()
                or source != source.strip()
                or any(
                    character in source
                    for character in (";", "\r", "\n", "\x00")
                )
                for source in sources
            )
        ):
            raise ValueError("default_src must be a non-empty tuple of safe CSP sources")
        for name in ("referrer_policy", "permissions_policy"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or any(character in value for character in ("\r", "\n", "\x00"))
            ):
                raise ValueError(f"{name} must be a non-empty safe header value")

    @property
    def content_security_policy(self) -> str:
        return (
            f"default-src {' '.join(self.default_src)}; "
            "base-uri 'none'; object-src 'none'; "
            f"frame-ancestors {' '.join(self.frame_ancestors)}"
        )

    def response_headers(self, *, artifact_digest: str) -> dict[str, str]:
        """Build the complete server-managed static response header set."""
        if not is_valid_artifact_digest(artifact_digest):
            raise ValueError("static response requires a canonical bundle digest")
        return {
            "X-AgentScale-Bundle-Digest": artifact_digest,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": self.content_security_policy,
            "Referrer-Policy": self.referrer_policy,
            "Permissions-Policy": self.permissions_policy,
        }


@dataclass(frozen=True, slots=True)
class GatewayClaims:
    """Доверенный identity одного проксируемого запроса.

    Это НЕ ``PluginClaims`` (JWT плагина для /capabilities/v1/*). Здесь —
    уже проверенная шлюзом связка «кто / куда», из которой ``headers.py``
    строит исходящие заголовки. Клиент не может повлиять ни на одно поле.

    ``anonymous=True`` только для явно объявленных ``public_pages`` либо для
    read-only public operation из signed manifest на exact public page:
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
    #: Trusted durable instance identity. ``None`` marks absence, never a fallback.
    plugin_instance_id: str | None = None
    #: Digest of the signed artifact bound to the same durable grant/instance.
    artifact_digest: str | None = None

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
        if self.plugin_instance_id is not None and (
            not isinstance(self.plugin_instance_id, str)
            or not self.plugin_instance_id.strip()
            or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in self.plugin_instance_id)
        ):
            raise ValueError(
                "plugin_instance_id must be non-empty and contain no control characters"
            )
        if self.artifact_digest is not None and not is_valid_artifact_digest(
            self.artifact_digest
        ):
            raise ValueError("artifact_digest must be sha256:<64 lowercase hex>")
        if self.project_id is not None and (
            not isinstance(self.project_id, str)
            or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in self.project_id)
        ):
            raise ValueError("project_id must not contain control characters")
        if self.anonymous and self.employee_id is not None:
            raise ValueError("anonymous claims must not carry an employee_id")


@dataclass(frozen=True, slots=True)
class GatewayOperation:
    """RPC declaration из подписанного manifest, переданный через GrantPort.

    Это не client input и не public bootstrap DTO. Адаптер обязан связать
    declaration с активным grant и digest опубликованного signed artifact.
    ``public=True`` — лишь eligibility: anonymous MVP допускает только read-only
    GET/HEAD; public POST/side effects требуют отдельного позднего design.
    """

    capability_id: str
    capability_version: str
    operation: str
    http_method: str
    backend_path: str
    public: bool = False

    def __post_init__(self) -> None:
        for name in ("capability_id", "capability_version", "operation"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or any(
                ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value
            ):
                raise ValueError(f"{name} must be non-empty and printable")
        if self.http_method not in {"GET", "POST"}:
            raise ValueError("operation http_method must be GET or POST")
        if not isinstance(self.public, bool):
            raise ValueError("operation public must be a boolean")
        if (
            not isinstance(self.backend_path, str)
            or canonical_backend_path(self.backend_path) != self.backend_path
            or not self.backend_path.startswith("/api/")
        ):
            raise ValueError("operation backend_path must be a canonical /api/ path")


@dataclass(frozen=True, slots=True)
class GatewayGrant:
    """Активный grant плагина + короткий capability-token из lease wake-up.

    ``capability_token`` перезаписывает одноимённый входящий заголовок. Для
    anon/public_pages/public operations он пуст: anonymous не получает token.
    ``artifact_digest`` — immutable digest активного durable grant/revision;
    ``None`` допускается только вне backend forwarding, где proxy требует его
    явно и fail-closed. ``published_artifact_digest`` дополнительно подтверждает
    тот же bundle digest через опубликованный ``PluginArtifactRecord`` и
    обязателен только для static response. Он никогда не заполняется из
    ``backend_digest`` или клиентских данных.
    """

    grant_id: str
    active: bool = True
    capability_token: str = ""
    operations: frozenset[str] = frozenset()
    plugin_instance_id: str | None = None
    artifact_digest: str | None = None
    published_artifact_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.grant_id.strip():
            raise ValueError("grant_id must not be empty")
        if self.plugin_instance_id is not None and (
            not isinstance(self.plugin_instance_id, str)
            or not self.plugin_instance_id.strip()
            or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in self.plugin_instance_id)
        ):
            raise ValueError(
                "plugin_instance_id must be non-empty and contain no control characters"
            )
        if self.artifact_digest is not None and not is_valid_artifact_digest(
            self.artifact_digest
        ):
            raise ValueError("artifact_digest must be sha256:<64 lowercase hex>")
        if self.published_artifact_digest is not None and not is_valid_artifact_digest(
            self.published_artifact_digest
        ):
            raise ValueError(
                "published_artifact_digest must be sha256:<64 lowercase hex>"
            )


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
    """Активный grant/bind и signed-manifest pages/operations реестры."""

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

    async def rpc_operations(
        self, *, plugin_id: str, company_id: str
    ) -> tuple[GatewayOperation, ...] | None:
        """Operations из signed manifest, связанного с активным grant/digest.

        ``None`` означает, что manifest-backed реестр недоступен: anonymous
        operation auth обязана остаться закрытой. Возвращаются и public, и
        private declarations, чтобы GatewayAuth сам применил ``public is True``.
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
    IN_PROGRESS = "in_progress"
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
    retry_after_seconds: int | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.status is DeliveryStatus.DUPLICATE


class RateLimitPort(Protocol):
    """Rate limit и атомарный claim идемпотентности (защита от replay).

    Rate buckets, processing claim и done commit обязаны быть атомарными.
    Processing живёт короткий lease TTL; успешная идемпотентность — дольше.
    """

    async def allow(self, *, bucket: str, limit: int, window_seconds: int) -> bool:
        """``True`` = запрос разрешён, ``False`` = лимит исчерпан (429)."""
        ...

    async def claim_delivery(
        self, *, key: str, ttl_seconds: int, token: str | None = None
    ) -> DeliveryVerdict:
        """Legacy API; новые webhook используют :meth:`claim_delivery_lease`."""
        ...

    async def claim_delivery_lease(
        self,
        *,
        key: str,
        ttl_seconds: int,
        lease_seconds: int,
        token: str,
    ) -> DeliveryVerdict:
        """Атомарно занять processing lease; done-хранится ``ttl_seconds``.

        ``FIRST`` разрешает forward, ``IN_PROGRESS`` даёт 503 + Retry-After,
        ``DUPLICATE`` означает ТОЛЬКО ранее подтверждённый backend 2xx.
        """
        ...

    async def commit_delivery(self, *, key: str, ttl_seconds: int, token: str) -> bool:
        """Атомарно перевести processing в done после подтверждённого 2xx."""
        ...

    async def release_delivery(self, *, key: str, token: str) -> None:
        """Снять только свой processing lease для повторной доставки."""
        ...


# ---------------------------------------------------------------------------
# Активация backend'а (реализация — задача 09)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WakeLease:
    """Результат wake-up: backend Ready + новый короткий capability-token.

    §7.4: Gateway патчит ``deployments/scale`` и **удерживает соединение до
    Ready**, затем проксирует. ``ensure_running`` из SandboxClaim не ждёт Ready
    и для плагинов не применяется. ``artifact_digest``/``grant_id`` копируются
    только из проверенных ``WakeResult.claims``; proxy отклоняет их отсутствие
    или mismatch с активным durable grant.
    """

    plugin_id: str
    company_id: str
    capability_token: str
    expires_at: float
    ready: bool = True
    plugin_instance_id: str | None = None
    artifact_digest: str | None = None
    grant_id: str | None = None

    def __post_init__(self) -> None:
        if not self.plugin_id.strip() or not self.company_id.strip():
            raise ValueError("plugin_id/company_id must not be empty")
        if not self.capability_token.strip():
            raise ValueError("wake lease must carry a capability token")
        if self.plugin_instance_id is not None and (
            not isinstance(self.plugin_instance_id, str)
            or not self.plugin_instance_id.strip()
        ):
            raise ValueError("wake lease plugin_instance_id must not be empty")
        if self.artifact_digest is not None and not is_valid_artifact_digest(
            self.artifact_digest
        ):
            raise ValueError("wake lease artifact_digest must be sha256:<64 lowercase hex>")
        if self.grant_id is not None and (
            not isinstance(self.grant_id, str)
            or not self.grant_id.strip()
            or any(
                ord(character) < 0x20 or ord(character) == 0x7F
                for character in self.grant_id
            )
        ):
            raise ValueError("wake lease grant_id must be non-empty and printable")


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

    async def wake(
        self,
        *,
        plugin_id: str,
        company_id: str,
        plugin_instance_id: str | None = None,
    ) -> WakeLease:
        """Инициировать 0→1 exact grant instance и вернуть lease."""
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
class VerifiedWebhook:
    """Проверенные GatewayAuth webhook-заголовки для trusted re-add.

    Пустая ``signature_version`` означает legacy v1 без явного version header.
    Никогда не создаётся из клиентских данных вне успешной HMAC-проверки.
    """

    signature: str
    event: str
    delivery_id: str
    signature_version: str = ""
    timestamp: str = ""

    def as_headers(self) -> dict[str, str]:
        out = {
            "X-Prodavan-Signature": self.signature,
            "X-Prodavan-Event": self.event,
            "X-Prodavan-Delivery": self.delivery_id,
        }
        if self.signature_version:
            out["X-Prodavan-Signature-Version"] = self.signature_version
        if self.timestamp:
            out["X-Prodavan-Timestamp"] = self.timestamp
        return out


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
    #: Исходный query без разбора/пересборки; path query не содержит.
    query_string: bytes = b""
    #: Жёсткий cap для non-streaming response; 0/отрицательное значение запрещено.
    max_response_bytes: int = 1_048_576
    #: Durable instance из grant; общий per-instance transport не принимает None.
    plugin_instance_id: str | None = None

    def __post_init__(self) -> None:
        normalized = canonical_backend_path(self.path)
        if normalized != self.path:
            raise ValueError("backend path must be canonical and start with '/'")
        canonical_raw_query(self.query_string)
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if self.plugin_instance_id is not None and (
            not isinstance(self.plugin_instance_id, str)
            or not self.plugin_instance_id.strip()
            or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in self.plugin_instance_id)
        ):
            raise ValueError(
                "plugin_instance_id must be non-empty and contain no control characters"
            )


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
    "GatewayOperation",
    "GatewaySession",
    "StaticResponsePolicy",
    "GrantPort",
    "HmacPort",
    "RateLimitPort",
    "SecretPort",
    "SessionPort",
    "VerifiedWebhook",
    "WakeLease",
    "is_valid_artifact_digest",
]
