"""``GatewayAuth`` — единая точка проверки входящих запросов Plugin Gateway.

Покрывает §7.2 (сессия + ``employee ∈ company`` + активный grant, ``public_pages``
с урезанным anon-scope и отдельным rate limit) и §7.5 (HMAC-подпись webhook'а,
``X-Prodavan-Event`` / ``X-Prodavan-Delivery``, идемпотентность и защита от
replay, rate limit per ``(plugin, company)``).

Fail-closed на каждом шаге:
* отсутствует порт / сессия / grant / секрет → явный ``GatewayError``;
* ``X-Prodavan-Signature`` без/с неверным значением → ``401``;
* повтор ``X-Prodavan-Delivery`` → ``DeliveryVerdict(DUPLICATE)`` (роутер
  отдаёт ``200`` **без** проксирования в backend), но повтор с чужим
  replay-токеном → ``401``;
* ``/webhook/`` никогда не трактуется как статический путь.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Mapping

from .canonical import (
    canonical_backend_path,
    canonical_raw_query,
    webhook_external_path,
    webhook_v2_signing_bytes,
)

from prodavan.application.plugins.gateway.errors import (
    GatewayError,
    GatewayErrorCode,
)
from prodavan.application.plugins.gateway.ports import (
    ANON_SCOPES,
    SESSION_SCOPES,
    WEBHOOK_SCOPES,
    AuditPort,
    DeliveryStatus,
    DeliveryVerdict,
    GatewayAuditEvent,
    GatewayClaims,
    GatewayGrant,
    GatewayOperation,
    GatewaySession,
    GrantPort,
    HmacPort,
    RateLimitPort,
    SecretPort,
    SessionPort,
    VerifiedWebhook,
    is_valid_artifact_digest,
)

# ---------------------------------------------------------------------------
# Заголовки webhook-контракта (docs/external-api.md:43-51, §7.5)
# ---------------------------------------------------------------------------

HEADER_SIGNATURE = "X-Prodavan-Signature"
HEADER_SIGNATURE_VERSION = "X-Prodavan-Signature-Version"
HEADER_TIMESTAMP = "X-Prodavan-Timestamp"
HEADER_EVENT = "X-Prodavan-Event"
HEADER_DELIVERY = "X-Prodavan-Delivery"

#: Обязательные заголовки входящего вебхука. Отсутствие любого = 401.
_REQUIRED_WEBHOOK_HEADERS: tuple[str, ...] = (
    HEADER_SIGNATURE,
    HEADER_EVENT,
    HEADER_DELIVERY,
)


# ---------------------------------------------------------------------------
# Конфигурация лимитов (явная, без «магических» чисел в коде)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GatewayLimits:
    """Лимиты шлюза. Раздельные для webhook / pluginpage / public_pages."""

    webhook_per_minute: int = 120
    pluginpage_per_minute: int = 300
    #: Публичные страницы — ОТДЕЛЬНЫЙ, более жёсткий лимит (§7.2).
    public_page_per_minute: int = 30
    window_seconds: int = 60
    #: TTL ключа идемпотентности; окно защиты от replay по времени (§7.5).
    delivery_ttl_seconds: int = 24 * 60 * 60
    #: Ответ при «backend ещё не Ready» (§7.4).
    retry_after_seconds: int = 3
    #: Серверный таймаут запроса к backend (НЕ из тела запроса, §4.4).
    backend_timeout_seconds: float = 30.0
    #: Таймаут удержания wake-up до Ready.
    ready_timeout_seconds: float = 120.0
    #: Retry только на идемпотентные методы, не более N попыток.
    max_idempotent_retries: int = 2
    #: Таймаут чтения потокового ответа (SSE) — отдельный от обычного.
    stream_timeout_seconds: float = 300.0
    #: Фактические caps входящих тел и non-streaming upstream response.
    webhook_body_limit_bytes: int = 1_048_576
    page_body_limit_bytes: int = 1_048_576
    typed_payload_limit_bytes: int = 1_048_576
    backend_response_limit_bytes: int = 4_194_304
    #: Дешёвые pre-HMAC limits: не дают анониму съесть per-installation bucket.
    webhook_preauth_ip_per_minute: int = 60
    webhook_preauth_global_per_minute: int = 10_000
    #: Freshness v2 и processing lease (done живёт delivery_ttl_seconds).
    webhook_timestamp_tolerance_seconds: int = 300
    #: 240с покрывает default wake 120с + 3 backend-попытки по 30с (210с).
    delivery_lease_seconds: int = 240
    #: XFF учитывается только если непосредственный peer входит в эти CIDR/IP.
    trusted_proxy_cidrs: tuple[str, ...] = ()


DEFAULT_LIMITS = GatewayLimits()


# ---------------------------------------------------------------------------
# Результаты авторизации
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WebhookAuthResult:
    """Разрешённый webhook. ``duplicate=True`` → роутер отдаёт 200 без forward."""

    claims: GatewayClaims
    grant: GatewayGrant
    event: str
    delivery_id: str
    duplicate: bool = False
    verified: VerifiedWebhook | None = None
    delivery_key: str = ""
    replay_token: str = ""


@dataclass(frozen=True, slots=True)
class PageAuthResult:
    """Разрешённая страница плагина (static или /api/*)."""

    claims: GatewayClaims
    grant: GatewayGrant
    public: bool = False
    session: GatewaySession | None = None


# ---------------------------------------------------------------------------
# HMAC-порт по умолчанию (as-built контракт, без внешних зависимостей)
# ---------------------------------------------------------------------------


class Sha256HmacVerifier:
    """``X-Prodavan-Signature: sha256=<hex(hmac_sha256(secret, raw_body))>``.

    Повторяет ``docs/external-api.md:51`` / ``domain/projects/webhook_hmac.py``.
    Сравнение константное по времени (``hmac.compare_digest``). Секрет приходит
    из ``SecretPort`` — здесь нет ни БД, ни дефолтов, ни чтения настроек.
    """

    def verify(
        self,
        *,
        secret: str,
        signing_bytes: bytes,
        signature_header: str | None,
    ) -> bool:
        if not secret or not signature_header or not isinstance(signature_header, str):
            return False
        header = signature_header.strip()
        if not header.startswith("sha256="):
            return False
        supplied = header[len("sha256=") :]
        # Ровно 64 hex-символа: иначе compare_digest может «провалиться» по
        # длине и это утечёт через тайминг, да и формат контракта нарушен.
        if len(supplied) != 64 or any(c not in "0123456789abcdef" for c in supplied):
            return False
        expected = hmac.new(
            secret.encode("utf-8"), signing_bytes, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, supplied)


# ---------------------------------------------------------------------------
# GatewayAuth
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GatewayAuth:
    """Проверка входящего запроса. Все порты обязательны (fail-closed).

    ``ActivationPort`` и ``BackendHttpPort`` здесь не нужны — они belong
    ``GatewayProxy``; шлюз разделяет «решить» и «исполнить» (§4.5: решение
    об авторизации и факт исполнения — разные события аудита).
    """

    sessions: SessionPort
    grants: GrantPort
    secrets: SecretPort
    rate_limit: RateLimitPort
    audit: AuditPort
    hmac_verifier: HmacPort = field(default_factory=Sha256HmacVerifier)
    limits: GatewayLimits = DEFAULT_LIMITS
    clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        # Fail-closed: ни один порт не может быть None. Отсутствие интеграции —
        # явная ошибка композиции, а не «тихое» разрешение запроса.
        for name in ("sessions", "grants", "secrets", "rate_limit", "audit", "hmac_verifier"):
            if getattr(self, name, None) is None:
                raise GatewayError(
                    GatewayErrorCode.PORT_UNAVAILABLE,
                    detail=f"required port '{name}' is not wired",
                )
        if self.limits is None:
            raise GatewayError(
                GatewayErrorCode.PORT_UNAVAILABLE,
                detail="GatewayLimits are not wired",
            )

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str | None:
        """Регистронезависимое чтение заголовка (Starlette уже нормализует)."""
        target = name.lower()
        for key, value in headers.items():
            if isinstance(key, str) and key.lower() == target:
                return value.strip() if isinstance(value, str) else None
        return None

    @staticmethod
    def _session_token(headers: Mapping[str, str]) -> str | None:
        """Извлечь маркер сессии, НЕ интерпретируя его формат.

        Приоритет: ``Authorization: Bearer ...`` (верифицированный OIDC /
        app key), затем cookie ``session``. Входящие ``Authorization`` и
        ``Cookie`` всё равно ОТБРАСЫВАЮТСЯ при проксировании (headers.py) —
        здесь они нужны только для принятия решения.
        """
        auth = GatewayAuth._header(headers, "Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token:
                return token
        cookie = GatewayAuth._header(headers, "Cookie")
        if cookie:
            for part in cookie.split(";"):
                key, _, value = part.strip().partition("=")
                if key.strip().lower() == "session" and value.strip():
                    return value.strip()
        return None

    @staticmethod
    def _replay_token(
        *, plugin_id: str, company_id: str, delivery_id: str, event: str, body: bytes
    ) -> str:
        """Отпечаток первой доставки для защиты от подделки повтора.

        Не является секретом: это детерминированный digest связки
        «(plugin, company, delivery, event, body)». Хранится вместе с ключом
        идемпотентности; повтор с тем же delivery-id, но другим телом/типом
        события не пройдёт проверку (§7.5, защита от replay).
        """
        payload = (
            plugin_id.encode("utf-8")
            + b"\x00"
            + company_id.encode("utf-8")
            + b"\x00"
            + delivery_id.encode("utf-8")
            + b"\x00"
            + event.encode("utf-8")
            + b"\x00"
            + body
        )
        return hashlib.sha256(payload).hexdigest()

    def _delivery_key(
        self, *, plugin_id: str, company_id: str, delivery_id: str
    ) -> str:
        """Ключ идемпотентности привязан к паре (plugin, company)."""
        digest = hashlib.sha256(
            f"{plugin_id}\x00{company_id}\x00{delivery_id}".encode("utf-8")
        ).hexdigest()
        return f"plugin-gateway:delivery:{digest}"

    @staticmethod
    def _bucket(kind: str, plugin_id: str, company_id: str, subject: str = "") -> str:
        tail = f":{subject}" if subject else ""
        return f"plugin-gateway:rl:{kind}:{plugin_id}:{company_id}{tail}"

    async def _enforce_rate_limit(
        self,
        *,
        bucket: str,
        limit: int,
        window_seconds: int,
        event_kind: str,
        plugin_id: str,
        company_id: str,
    ) -> None:
        # Отказ САМОГО хранилища лимитов — это system failure, а не «превышен
        # лимит». Адаптер (RedisRateLimitAdapter) бросает собственное
        # исключение, которое НЕ является GatewayError; без обёртки роутер
        # отдал бы 500 Internal Server Error вместо типизированного 503.
        # Fail-closed: недоступность Redis = deny, никогда не «пустить без
        # лимита» (дизайн v4 §7.2, критерий appendix-аудита).
        try:
            allowed = await self.rate_limit.allow(
                bucket=bucket, limit=limit, window_seconds=window_seconds
            )
        except GatewayError:
            raise
        except Exception as exc:  # noqa: BLE001 — любая ошибка порта = deny
            raise GatewayError(
                GatewayErrorCode.UPSTREAM_ERROR,
                detail="rate limit backend unavailable; refusing request",
            ) from exc
        if not allowed:
            await self._audit(
                event_kind,
                plugin_id=plugin_id,
                company_id=company_id,
                decision="deny",
                code=GatewayErrorCode.RATE_LIMITED.value,
                reason="rate limit exceeded",
            )
            raise GatewayError(
                GatewayErrorCode.RATE_LIMITED,
                detail="rate limit exceeded for (plugin, company)",
                retry_after_seconds=self.limits.window_seconds,
            )

    async def _audit(self, event: str, **kwargs: Any) -> None:
        """Append-only аудит. Ошибка записи = system failure (§4.5)."""
        payload = {
            "event": event,
            "plugin_id": "",
            "company_id": "",
            "decision": "",
            **kwargs,
        }
        try:
            await self.audit.record(GatewayAuditEvent(**payload))
        except TypeError as exc:
            raise GatewayError(
                GatewayErrorCode.AUDIT_APPEND_FAILED,
                detail=f"invalid audit event shape: {exc}",
            ) from exc
        except Exception as exc:  # noqa: BLE001 — любая ошибка sink = deny
            raise GatewayError(
                GatewayErrorCode.AUDIT_APPEND_FAILED,
                detail=f"audit append failed: {type(exc).__name__}",
            ) from exc

    async def _active_grant(
        self, *, plugin_id: str, company_id: str, surface: str
    ) -> GatewayGrant:
        grant = await self.grants.active_grant(
            plugin_id=plugin_id, company_id=company_id
        )
        if grant is None or not grant.active:
            await self._audit(
                f"plugin_{surface}_denied",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="deny",
                code=GatewayErrorCode.GRANT_INACTIVE.value,
                reason="no active grant",
            )
            raise GatewayError(
                GatewayErrorCode.GRANT_INACTIVE,
                detail=f"no active grant for plugin '{plugin_id}' in company '{company_id}'",
            )
        if not isinstance(grant.plugin_instance_id, str) or not grant.plugin_instance_id.strip():
            await self._audit(
                f"plugin_{surface}_denied",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="deny",
                code=GatewayErrorCode.PORT_UNAVAILABLE.value,
                reason="active durable grant has no plugin_instance_id",
            )
            raise GatewayError(
                GatewayErrorCode.PORT_UNAVAILABLE,
                detail="active durable grant has no plugin_instance_id (fail-closed)",
            )
        if not is_valid_artifact_digest(grant.artifact_digest):
            await self._audit(
                f"plugin_{surface}_denied",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="deny",
                code=GatewayErrorCode.PORT_UNAVAILABLE.value,
                reason="active durable grant has no valid artifact_digest",
            )
            raise GatewayError(
                GatewayErrorCode.PORT_UNAVAILABLE,
                detail="active durable grant has no valid artifact_digest (fail-closed)",
            )
        return grant

    @staticmethod
    def _is_public(path: str, declared: frozenset[str]) -> bool:
        """Публичность — ТОЛЬКО по явно объявленному списку из манифеста.

        Никаких «всё, что не /api/*» и никакого index.html по умолчанию.
        Поддерживается точное совпадение или префикс, оканчивающийся на ``/``.
        """
        normalized = "/" + path.strip("/")
        for entry in declared:
            if not entry:
                continue
            candidate = entry if entry.startswith("/") else "/" + entry
            if candidate.endswith("/"):
                if normalized.startswith(candidate) or normalized == candidate.rstrip("/"):
                    return True
            elif normalized == candidate:
                return True
        return False

    @staticmethod
    def _is_exact_public(path: str, declared: frozenset[str]) -> bool:
        """Exact canonical route match; префиксы/нормализация здесь запрещены."""
        try:
            if canonical_backend_path(path) != path:
                return False
            return any(
                entry == path and canonical_backend_path(entry) == entry
                for entry in declared
            )
        except (TypeError, ValueError):
            return False

    async def _try_authorize_public_operation(
        self,
        *,
        plugin_id: str,
        company_id: str,
        path: str,
        method: str | None,
        raw_body: bytes | None,
        raw_query: bytes | None,
        route_path: str | None,
        headers: Mapping[str, str],
        grant: GatewayGrant,
        declared_public: frozenset[str],
        request_id: str,
        client_ip: str,
    ) -> PageAuthResult | None:
        """Узкое anonymous-исключение для read-only public manifest operation.

        ``None`` означает, что исключение неприменимо и обычная session auth
        должна продолжиться. Наличие session token никогда не понижается до
        anonymous. Bootstrap DTO сюда не передаётся и доказательством не является.
        Разрешены только GET/HEAD с пустым body по explicit public declaration;
        side-effecting operations требуют отдельного позднего design, не bypass.
        Signature-заголовки здесь identity не доказывают и не используются;
        существующий webhook HMAC остаётся отдельным неизменным контуром.
        """
        if (
            route_path is None
            or self._session_token(headers) is not None
            or not self._is_exact_public(route_path, declared_public)
        ):
            return None
        resolver = getattr(self.grants, "rpc_operations", None)
        if not callable(resolver):
            return None
        declarations = await resolver(plugin_id=plugin_id, company_id=company_id)
        if declarations is None:
            return None
        if not isinstance(raw_query, bytes):
            raise GatewayError(
                GatewayErrorCode.OPERATION_NOT_ALLOWED,
                detail="anonymous public operation requires explicit raw query bytes",
            )
        try:
            backend_path = canonical_backend_path(path)
            canonical_raw_query(raw_query)
            operations = tuple(declarations)
        except (TypeError, ValueError) as exc:
            raise GatewayError(
                GatewayErrorCode.INVALID_PATH,
                detail="invalid public operation path or manifest registry",
            ) from exc

        path_digest = hashlib.sha256(backend_path.encode("utf-8")).hexdigest()
        normalized_method = method.upper() if isinstance(method, str) else ""
        read_only = normalized_method in {"GET", "HEAD"} and raw_body == b""
        if not read_only or any(not isinstance(item, GatewayOperation) for item in operations):
            matches: list[GatewayOperation] = []
        else:
            matches = [
                item
                for item in operations
                # HEAD переиспользует read-only GET declaration; manifest
                # продолжает допускать только canonical GET/POST declarations.
                if item.backend_path == backend_path and item.http_method == "GET"
            ]
        if len(matches) != 1 or not matches[0].public:
            await self._audit(
                "plugin_page_denied",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="deny",
                code=GatewayErrorCode.OPERATION_NOT_ALLOWED.value,
                reason=(
                    "operation is not a unique read-only public declaration "
                    "in the signed manifest"
                ),
                path_digest=path_digest,
                request_id=request_id,
            )
            raise GatewayError(
                GatewayErrorCode.OPERATION_NOT_ALLOWED,
                detail=(
                    "anonymous access requires an exact public GET declaration "
                    "and an empty body"
                ),
            )
        operation = matches[0]

        subject = hashlib.sha256((client_ip or "anon").encode("utf-8")).hexdigest()
        await self._enforce_rate_limit(
            bucket=self._bucket("public_page", plugin_id, company_id, subject),
            limit=self.limits.public_page_per_minute,
            window_seconds=self.limits.window_seconds,
            event_kind="plugin_page_public",
            plugin_id=plugin_id,
            company_id=company_id,
        )
        claims = GatewayClaims(
            plugin_id=plugin_id,
            company_id=company_id,
            subject_id="anon",
            plugin_instance_id=grant.plugin_instance_id,
            artifact_digest=grant.artifact_digest,
            scopes=ANON_SCOPES,
            project_id=None,
            grant_id=grant.grant_id,
            anonymous=True,
        )
        anon_grant = GatewayGrant(
            grant_id=grant.grant_id,
            plugin_instance_id=grant.plugin_instance_id,
            artifact_digest=grant.artifact_digest,
            published_artifact_digest=grant.published_artifact_digest,
            active=True,
            capability_token="",
            operations=frozenset(),
        )
        await self._audit(
            "plugin_page_opened",
            plugin_id=plugin_id,
            company_id=company_id,
            decision="allow",
            subject_id="anon",
            grant_id=grant.grant_id,
            request_id=request_id,
            metadata={
                "public": True,
                "scopes": sorted(ANON_SCOPES),
                "operation": operation.operation,
                "capability": f"{operation.capability_id}@{operation.capability_version}",
            },
        )
        return PageAuthResult(claims=claims, grant=anon_grant, public=True)

    # ------------------------------------------------------------- /pluginpage

    async def authorize_page(
        self,
        *,
        plugin_id: str,
        company_id: str,
        path: str,
        headers: Mapping[str, str],
        is_api: bool = False,
        method: str | None = None,
        raw_body: bytes | None = None,
        raw_query: bytes | None = None,
        route_path: str | None = None,
        request_id: str = "",
        client_ip: str = "",
    ) -> PageAuthResult:
        """Авторизация ``/pluginpage/{plugin}/{company}/{path}``.

        Порядок (fail-closed):
        1. активный grant;
        2. если путь в ``public_pages`` **и это не ``/api/*``** → урезанный
           ``ANON_SCOPES`` + ОТДЕЛЬНЫЙ rate limit, БЕЗ сессии и БЕЗ
           capability-токена;
        3. для ``/api/*`` без session token — ТОЛЬКО exact public route_path,
           уникальная read-only public GET declaration из signed manifest,
           метод GET/HEAD и пустой body; side effects anonymous запрещены;
        4. иначе — обязательная сессия + ``employee ∈ company``.

        Public bootstrap как самостоятельное доказательство не используется.
        Generic ``/api/*`` без всех этих доказательств по-прежнему даёт 401;
        private/unknown operation на public page даёт 403. Anonymous получает
        пустой capability_token и operations, независимо от durable grant.
        """
        if not plugin_id or not company_id:
            raise GatewayError(
                GatewayErrorCode.INVALID_PATH,
                detail="plugin/company path segments must not be empty",
            )

        grant = await self._active_grant(
            plugin_id=plugin_id, company_id=company_id, surface="page"
        )
        declared_public = await self.grants.public_pages(
            plugin_id=plugin_id, company_id=company_id
        )
        public = (not is_api) and self._is_public(path, declared_public)

        # --- публичная страница: явное исключение, урезанный scope ----------
        if public:
            if raw_body not in (None, b""):
                # Public static остаётся read-only: GET/HEAD принимают только
                # пустое body; POST и другие методы отклоняет proxy с 405.
                raise GatewayError(
                    GatewayErrorCode.OPERATION_NOT_ALLOWED,
                    detail="anonymous public static requires GET/HEAD with an empty body",
                )
            # XFF сюда не читается: router передаёт peer, уже разрешённый по
            # trusted_proxy_cidrs. Дополнительно не храним сам IP в Redis key.
            subject = hashlib.sha256((client_ip or "anon").encode("utf-8")).hexdigest()
            await self._enforce_rate_limit(
                bucket=self._bucket("public_page", plugin_id, company_id, subject),
                limit=self.limits.public_page_per_minute,
                window_seconds=self.limits.window_seconds,
                event_kind="plugin_page_public",
                plugin_id=plugin_id,
                company_id=company_id,
            )
            claims = GatewayClaims(
                plugin_id=plugin_id,
                company_id=company_id,
                subject_id="anon",
                plugin_instance_id=grant.plugin_instance_id,
                artifact_digest=grant.artifact_digest,
                scopes=ANON_SCOPES,
                project_id=None,
                grant_id=grant.grant_id,
                anonymous=True,
            )
            # Anon НЕ получает capability-токен даже если grant его выдал.
            anon_grant = GatewayGrant(
                grant_id=grant.grant_id,
                plugin_instance_id=grant.plugin_instance_id,
                artifact_digest=grant.artifact_digest,
                # Preserve only exact authenticated/published bundle evidence.
                # Anonymous never gains capability token or backend authority.
                published_artifact_digest=grant.published_artifact_digest,
                active=True,
                capability_token="",
                operations=frozenset(),
            )
            await self._audit(
                "plugin_page_opened",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="allow",
                subject_id="anon",
                grant_id=grant.grant_id,
                request_id=request_id,
                metadata={"public": True, "scopes": sorted(ANON_SCOPES)},
            )
            return PageAuthResult(claims=claims, grant=anon_grant, public=True)

        if is_api:
            public_operation = await self._try_authorize_public_operation(
                plugin_id=plugin_id,
                company_id=company_id,
                path=path,
                method=method,
                raw_body=raw_body,
                raw_query=raw_query,
                route_path=route_path,
                headers=headers,
                grant=grant,
                declared_public=declared_public,
                request_id=request_id,
                client_ip=client_ip,
            )
            if public_operation is not None:
                return public_operation

        # --- обычная страница: сессия + employee ∈ company ------------------
        await self._enforce_rate_limit(
            bucket=self._bucket("pluginpage", plugin_id, company_id),
            limit=self.limits.pluginpage_per_minute,
            window_seconds=self.limits.window_seconds,
            event_kind="plugin_page",
            plugin_id=plugin_id,
            company_id=company_id,
        )
        session = await self.sessions.resolve(
            session_token=self._session_token(headers)
        )
        if session is None:
            await self._audit(
                "plugin_page_denied",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="deny",
                code=GatewayErrorCode.SESSION_REQUIRED.value,
                reason="session required",
            )
            raise GatewayError(
                GatewayErrorCode.SESSION_REQUIRED,
                detail="a valid session is required for this plugin page",
            )

        member = await self.sessions.employee_in_company(
            employee_id=session.employee_id, company_id=company_id
        )
        if not member:
            await self._audit(
                "plugin_page_denied",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="deny",
                subject_id=session.subject_id,
                code=GatewayErrorCode.EMPLOYEE_NOT_IN_COMPANY.value,
                reason="employee not in company",
            )
            raise GatewayError(
                GatewayErrorCode.EMPLOYEE_NOT_IN_COMPANY,
                detail="employee is not a member of the requested company",
            )

        claims = GatewayClaims(
            plugin_id=plugin_id,
            company_id=company_id,
            subject_id=session.subject_id,
            plugin_instance_id=grant.plugin_instance_id,
            artifact_digest=grant.artifact_digest,
            scopes=SESSION_SCOPES,
            grant_id=grant.grant_id,
            employee_id=session.employee_id,
        )
        await self._audit(
            "plugin_page_opened",
            plugin_id=plugin_id,
            company_id=company_id,
            decision="allow",
            subject_id=session.subject_id,
            grant_id=grant.grant_id,
            request_id=request_id,
            metadata={"public": False},
        )
        return PageAuthResult(claims=claims, grant=grant, session=session)

    # ---------------------------------------------------------------- /webhook

    async def _enforce_webhook_preauth(
        self, *, plugin_id: str, company_id: str, client_ip: str
    ) -> None:
        """Bounded IP/global limits до секрета/HMAC и per-installation bucket."""
        subject = hashlib.sha256((client_ip or "unknown").encode("utf-8")).hexdigest()
        await self._enforce_rate_limit(
            bucket=f"plugin-gateway:rl:webhook_preauth_ip:{subject}",
            limit=self.limits.webhook_preauth_ip_per_minute,
            window_seconds=self.limits.window_seconds,
            event_kind="plugin_webhook_preauth",
            plugin_id=plugin_id,
            company_id=company_id,
        )
        await self._enforce_rate_limit(
            bucket="plugin-gateway:rl:webhook_preauth_global",
            limit=self.limits.webhook_preauth_global_per_minute,
            window_seconds=self.limits.window_seconds,
            event_kind="plugin_webhook_preauth",
            plugin_id=plugin_id,
            company_id=company_id,
        )

    async def _check_webhook_route(
        self, *, plugin_id: str, company_id: str, path: str
    ) -> None:
        """Применить manifest allowlist, если GrantPort смог его предоставить."""
        resolver = getattr(self.grants, "webhook_routes", None)
        if not callable(resolver):
            return
        declared = await resolver(plugin_id=plugin_id, company_id=company_id)
        if declared is None:
            return
        if self._is_public(path, frozenset(declared)):
            return
        await self._audit(
            "plugin_webhook_received",
            plugin_id=plugin_id,
            company_id=company_id,
            decision="deny",
            code=GatewayErrorCode.WEBHOOK_ROUTE_NOT_ALLOWED.value,
            reason="webhook route is not present in manifest allowlist",
            path_digest=hashlib.sha256(canonical_backend_path(path).encode("utf-8")).hexdigest(),
        )
        raise GatewayError(
            GatewayErrorCode.WEBHOOK_ROUTE_NOT_ALLOWED,
            detail="webhook route is not declared by the plugin manifest",
        )

    async def _claim_webhook_delivery(
        self,
        *,
        key: str,
        token: str,
        plugin_id: str,
        company_id: str,
    ) -> DeliveryVerdict:
        claim = getattr(self.rate_limit, "claim_delivery_lease", None)
        if not callable(claim):
            raise GatewayError(
                GatewayErrorCode.PORT_UNAVAILABLE,
                detail="webhook delivery lease/commit port is not wired",
                retry_after_seconds=self.limits.retry_after_seconds,
            )
        try:
            return await claim(
                key=key,
                ttl_seconds=self.limits.delivery_ttl_seconds,
                lease_seconds=self.limits.delivery_lease_seconds,
                token=token,
            )
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError(
                GatewayErrorCode.UPSTREAM_ERROR,
                detail="webhook delivery lease backend unavailable",
                retry_after_seconds=self.limits.retry_after_seconds,
            ) from exc

    async def commit_webhook(self, result: WebhookAuthResult) -> None:
        """Перевести delivery в done ТОЛЬКО после подтверждённого backend 2xx."""
        if result.duplicate or not result.delivery_key:
            return
        commit = getattr(self.rate_limit, "commit_delivery", None)
        if not callable(commit):
            raise GatewayError(
                GatewayErrorCode.DELIVERY_COMMIT_FAILED,
                detail="webhook delivery commit port is not wired",
                retry_after_seconds=self.limits.retry_after_seconds,
            )
        try:
            committed = await commit(
                key=result.delivery_key,
                ttl_seconds=self.limits.delivery_ttl_seconds,
                token=result.replay_token,
            )
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError(
                GatewayErrorCode.DELIVERY_COMMIT_FAILED,
                detail="webhook delivery commit backend unavailable",
                retry_after_seconds=self.limits.retry_after_seconds,
            ) from exc
        if not committed:
            raise GatewayError(
                GatewayErrorCode.DELIVERY_COMMIT_FAILED,
                detail="webhook delivery lease was lost before commit; retry is required",
                retry_after_seconds=self.limits.retry_after_seconds,
            )

    async def release_webhook(self, result: WebhookAuthResult) -> None:
        """Разрешить retry после non-2xx/202/timeout, не удаляя чужой done."""
        if result.duplicate or not result.delivery_key:
            return
        release = getattr(self.rate_limit, "release_delivery", None)
        if not callable(release):
            raise GatewayError(
                GatewayErrorCode.DELIVERY_COMMIT_FAILED,
                detail="webhook delivery release port is not wired",
                retry_after_seconds=self.limits.retry_after_seconds,
            )
        try:
            await release(key=result.delivery_key, token=result.replay_token)
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError(
                GatewayErrorCode.DELIVERY_COMMIT_FAILED,
                detail="webhook delivery lease release failed; retry after lease expiry",
                retry_after_seconds=self.limits.delivery_lease_seconds,
            ) from exc

    async def authorize_webhook(
        self,
        *,
        plugin_id: str,
        company_id: str,
        path: str,
        method: str,
        raw_body: bytes,
        headers: Mapping[str, str],
        request_id: str = "",
        raw_query: bytes = b"",
        client_ip: str = "",
    ) -> WebhookAuthResult:
        """Проверить webhook до создания processing lease.

        Порядок: bounded preauth, required headers, grant/route/secret,
        freshness + HMAC, per-installation limit, processing lease.
        Следовательно bad signature не расходует bucket installation.
        """
        if not plugin_id or not company_id:
            raise GatewayError(
                GatewayErrorCode.INVALID_PATH,
                detail="plugin/company path segments must not be empty",
            )
        try:
            canonical_raw_query(raw_query)
            external_path = webhook_external_path(
                plugin_id=plugin_id, company_id=company_id, path=path
            )
            canonical_backend_path(path)
        except (TypeError, ValueError) as exc:
            raise GatewayError(
                GatewayErrorCode.INVALID_PATH,
                detail="invalid webhook path or raw query",
            ) from exc

        await self._enforce_webhook_preauth(
            plugin_id=plugin_id, company_id=company_id, client_ip=client_ip
        )

        signature = self._header(headers, HEADER_SIGNATURE)
        event = self._header(headers, HEADER_EVENT)
        delivery_id = self._header(headers, HEADER_DELIVERY)
        version_header = self._header(headers, HEADER_SIGNATURE_VERSION)
        timestamp = self._header(headers, HEADER_TIMESTAMP) or ""
        signature_version = version_header or "1"
        missing = [
            name
            for name, value in zip(
                _REQUIRED_WEBHOOK_HEADERS, (signature, event, delivery_id)
            )
            if not value
        ]
        if signature_version == "2" and not timestamp:
            missing.append(HEADER_TIMESTAMP)
        if missing:
            await self._audit(
                "plugin_webhook_received",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="deny",
                code=GatewayErrorCode.WEBHOOK_HEADERS_MISSING.value,
                reason="missing required webhook headers",
                metadata={"missing": sorted(missing)},
            )
            raise GatewayError(
                GatewayErrorCode.WEBHOOK_HEADERS_MISSING,
                detail=f"missing webhook headers: {', '.join(missing)}",
            )
        if signature_version not in {"1", "2"}:
            raise GatewayError(
                GatewayErrorCode.INVALID_SIGNATURE_VERSION,
                detail="X-Prodavan-Signature-Version must be '1' or '2'",
            )
        if not (
            isinstance(signature, str)
            and isinstance(event, str)
            and isinstance(delivery_id, str)
        ):
            raise GatewayError(
                GatewayErrorCode.WEBHOOK_HEADERS_MISSING,
                detail="required webhook headers must be non-empty strings",
            )
        if (
            not event.strip()
            or not delivery_id.strip()
            or len(event) > 512
            or len(delivery_id) > 512
            or any(
                ord(ch) < 0x20 or ord(ch) == 0x7F
                for value in (event, delivery_id)
                for ch in value
            )
        ):
            raise GatewayError(
                GatewayErrorCode.WEBHOOK_HEADERS_MISSING,
                detail="webhook event/delivery must be bounded printable strings",
            )

        grant = await self._active_grant(
            plugin_id=plugin_id, company_id=company_id, surface="webhook"
        )
        await self._check_webhook_route(
            plugin_id=plugin_id, company_id=company_id, path=path
        )
        secret = await self.secrets.webhook_secret(
            plugin_id=plugin_id, company_id=company_id
        )
        if not secret:
            await self._audit(
                "plugin_webhook_received",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="deny",
                code=GatewayErrorCode.SECRET_UNAVAILABLE.value,
                reason="no webhook secret configured",
            )
            raise GatewayError(
                GatewayErrorCode.SECRET_UNAVAILABLE,
                detail="webhook HMAC secret is not configured",
            )

        signing_bytes = raw_body
        if signature_version == "2":
            if (
                not timestamp.isascii()
                or not timestamp.isdigit()
                or len(timestamp) > 20
                or abs(int(self.clock()) - int(timestamp))
                > self.limits.webhook_timestamp_tolerance_seconds
            ):
                await self._audit(
                    "plugin_webhook_received",
                    plugin_id=plugin_id,
                    company_id=company_id,
                    decision="deny",
                    code=GatewayErrorCode.INVALID_TIMESTAMP.value,
                    reason="webhook timestamp is malformed or outside freshness window",
                )
                raise GatewayError(
                    GatewayErrorCode.INVALID_TIMESTAMP,
                    detail="webhook timestamp is malformed or outside freshness window",
                )
            signing_bytes = webhook_v2_signing_bytes(
                timestamp=timestamp,
                delivery_id=delivery_id,
                event=event,
                method=method,
                canonical_path=external_path,
                raw_query=raw_query,
                raw_body=raw_body,
            )

        if not self.hmac_verifier.verify(
            secret=secret, signing_bytes=signing_bytes, signature_header=signature
        ):
            await self._audit(
                "plugin_webhook_received",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="deny",
                code=GatewayErrorCode.INVALID_SIGNATURE.value,
                reason="HMAC mismatch",
                payload_digest=hashlib.sha256(raw_body).hexdigest(),
            )
            raise GatewayError(
                GatewayErrorCode.INVALID_SIGNATURE,
                detail="X-Prodavan-Signature mismatch",
            )

        # Только ПОСЛЕ успешной подписи анонимный запрос может занять bucket
        # конкретной installation.
        await self._enforce_rate_limit(
            bucket=self._bucket("webhook", plugin_id, company_id),
            limit=self.limits.webhook_per_minute,
            window_seconds=self.limits.window_seconds,
            event_kind="plugin_webhook",
            plugin_id=plugin_id,
            company_id=company_id,
        )

        token = self._replay_token(
            plugin_id=plugin_id,
            company_id=company_id,
            delivery_id=delivery_id,
            event=event,
            body=raw_body,
        )
        delivery_key = self._delivery_key(
            plugin_id=plugin_id, company_id=company_id, delivery_id=delivery_id
        )
        verdict = await self._claim_webhook_delivery(
            key=delivery_key,
            token=token,
            plugin_id=plugin_id,
            company_id=company_id,
        )
        verified = VerifiedWebhook(
            signature=signature,
            event=event,
            delivery_id=delivery_id,
            signature_version=version_header or "",
            timestamp=timestamp if signature_version == "2" else "",
        )

        if (
            verdict.previous_token is not None
            and not hmac.compare_digest(verdict.previous_token, token)
        ) or (
            verdict.status in {DeliveryStatus.IN_PROGRESS, DeliveryStatus.DUPLICATE}
            and verdict.previous_token is None
        ):
            await self._audit(
                "plugin_webhook_received",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="deny",
                code=GatewayErrorCode.REPLAY_DETECTED.value,
                reason="delivery id reused with a different payload",
            )
            raise GatewayError(
                GatewayErrorCode.REPLAY_DETECTED,
                detail="delivery id replay with a different payload",
            )

        if verdict.status is DeliveryStatus.IN_PROGRESS:
            retry_after = verdict.retry_after_seconds or self.limits.retry_after_seconds
            await self._audit(
                "plugin_webhook_received",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="retry",
                code=GatewayErrorCode.DELIVERY_IN_PROGRESS.value,
                reason="delivery processing lease is still active",
                request_id=request_id,
            )
            raise GatewayError(
                GatewayErrorCode.DELIVERY_IN_PROGRESS,
                detail="delivery is already being processed; retry later",
                retry_after_seconds=retry_after,
            )

        claims = GatewayClaims(
            plugin_id=plugin_id,
            company_id=company_id,
            subject_id="webhook",
            plugin_instance_id=grant.plugin_instance_id,
            artifact_digest=grant.artifact_digest,
            scopes=WEBHOOK_SCOPES,
            grant_id=grant.grant_id,
        )
        if verdict.status is DeliveryStatus.DUPLICATE:
            await self._audit(
                "plugin_webhook_received",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="allow_duplicate",
                subject_id="webhook",
                grant_id=grant.grant_id,
                request_id=request_id,
                metadata={"duplicate": True, "has_event": bool(event)},
            )
            return WebhookAuthResult(
                claims=claims,
                grant=grant,
                event=event,
                delivery_id=delivery_id,
                duplicate=True,
                verified=verified,
                delivery_key=delivery_key,
                replay_token=token,
            )
        if verdict.status is not DeliveryStatus.FIRST:
            raise GatewayError(
                GatewayErrorCode.UPSTREAM_ERROR,
                detail="delivery lease backend returned an unknown verdict",
            )

        result = WebhookAuthResult(
            claims=claims,
            grant=grant,
            event=event,
            delivery_id=delivery_id,
            duplicate=False,
            verified=verified,
            delivery_key=delivery_key,
            replay_token=token,
        )
        try:
            await self._audit(
                "plugin_webhook_received",
                plugin_id=plugin_id,
                company_id=company_id,
                decision="allow",
                subject_id="webhook",
                grant_id=grant.grant_id,
                request_id=request_id,
                payload_digest=hashlib.sha256(raw_body).hexdigest(),
                metadata={"has_event": bool(event)},
            )
        except Exception:
            # Не оставлять processed при сбое audit после lease. Если release
            # тоже недоступен, короткий TTL остаётся последним fail-closed рубежом.
            try:
                await self.release_webhook(result)
            except GatewayError:
                pass
            raise
        return result

__all__ = [
    "DEFAULT_LIMITS",
    "HEADER_DELIVERY",
    "HEADER_EVENT",
    "HEADER_SIGNATURE",
    "HEADER_SIGNATURE_VERSION",
    "HEADER_TIMESTAMP",
    "GatewayAuth",
    "GatewayLimits",
    "PageAuthResult",
    "Sha256HmacVerifier",
    "WebhookAuthResult",
]
