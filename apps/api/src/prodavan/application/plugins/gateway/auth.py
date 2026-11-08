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
from dataclasses import dataclass, field
from typing import Any, Mapping

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
    GatewaySession,
    GrantPort,
    HmacPort,
    RateLimitPort,
    SecretPort,
    SessionPort,
)

# ---------------------------------------------------------------------------
# Заголовки webhook-контракта (docs/external-api.md:43-51, §7.5)
# ---------------------------------------------------------------------------

HEADER_SIGNATURE = "X-Prodavan-Signature"
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

    # ------------------------------------------------------------- /pluginpage

    async def authorize_page(
        self,
        *,
        plugin_id: str,
        company_id: str,
        path: str,
        headers: Mapping[str, str],
        is_api: bool = False,
        request_id: str = "",
    ) -> PageAuthResult:
        """Авторизация ``/pluginpage/{plugin}/{company}/{path}``.

        Порядок (fail-closed):
        1. активный grant;
        2. если путь в ``public_pages`` **и это не ``/api/*``** → урезанный
           ``ANON_SCOPES`` + ОТДЕЛЬНЫЙ rate limit, БЕЗ сессии и БЕЗ
           capability-токена;
        3. иначе — обязательная сессия + ``employee ∈ company``.

        ВАЖНО: исключение ``public_pages`` НЕ распространяется на ``/api/*``.
        Анонимный вызов backend API плагина всегда даёт ``401`` (критерий
        §11 №6), даже если страница объявлена публичной: иначе публичная
        страница получила бы доступ к мутирующим эндпоинтам backend'а.
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
            subject = self._header(headers, "X-Forwarded-For") or "anon"
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
                scopes=ANON_SCOPES,
                project_id=None,
                grant_id=grant.grant_id,
                anonymous=True,
            )
            # Anon НЕ получает capability-токен даже если grant его выдал.
            anon_grant = GatewayGrant(
                grant_id=grant.grant_id,
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
    ) -> WebhookAuthResult:
        """Авторизация ``/webhook/{plugin}/{company}/{path}`` (§7.5).

        Порядок (fail-closed):
        1. наличие всех трёх заголовков (signature / event / delivery);
        2. rate limit per ``(plugin, company)``;
        3. активный grant;
        4. секрет из ``SecretPort`` (не БД, не код);
        5. HMAC по ``raw_body``;
        6. атомарный claim delivery-id → повтор = 200 без повторной обработки.

        ``/webhook/`` НИКОГДА не считается статикой: даже пустой ``path``
        ведёт сюда, а ``index.html`` не отдаётся (критерий §11 №9).
        """
        if not plugin_id or not company_id:
            raise GatewayError(
                GatewayErrorCode.INVALID_PATH,
                detail="plugin/company path segments must not be empty",
            )

        signature = self._header(headers, HEADER_SIGNATURE)
        event = self._header(headers, HEADER_EVENT)
        delivery_id = self._header(headers, HEADER_DELIVERY)
        missing = [
            name
            for name, value in zip(
                _REQUIRED_WEBHOOK_HEADERS, (signature, event, delivery_id)
            )
            if not value
        ]
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
        # Явная перестраховка вместо ``assert`` (который исчезает при -O):
        # если порт заголовков вернул нестроку, это отказ, не падение с
        # AttributeError где-то ниже по конвейеру.
        if not (
            isinstance(signature, str)
            and isinstance(event, str)
            and isinstance(delivery_id, str)
        ):
            raise GatewayError(
                GatewayErrorCode.WEBHOOK_HEADERS_MISSING,
                detail="required webhook headers must be non-empty strings",
            )
        # Пустой delivery-id / event после trim — тоже отказ, не «пропустить».
        if not event.strip() or not delivery_id.strip():
            raise GatewayError(
                GatewayErrorCode.WEBHOOK_HEADERS_MISSING,
                detail="webhook event/delivery must not be blank",
            )

        await self._enforce_rate_limit(
            bucket=self._bucket("webhook", plugin_id, company_id),
            limit=self.limits.webhook_per_minute,
            window_seconds=self.limits.window_seconds,
            event_kind="plugin_webhook",
            plugin_id=plugin_id,
            company_id=company_id,
        )

        grant = await self._active_grant(
            plugin_id=plugin_id, company_id=company_id, surface="webhook"
        )

        # Секрет — только из secret store. Нет секрета = канал не настроен:
        # fail-closed 503, а не «принять без подписи».
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

        if not self.hmac_verifier.verify(
            secret=secret, signing_bytes=raw_body, signature_header=signature
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

        # Атомарный claim: гонка ретраёв не приведёт к двойной обработке.
        # Отпечаток первой доставки сохраняется в том же атомарном вызове —
        # иначе повтор с чужим телом прошёл бы как «уже обработанный».
        token = self._replay_token(
            plugin_id=plugin_id,
            company_id=company_id,
            delivery_id=delivery_id,
            event=event,
            body=raw_body,
        )
        verdict: DeliveryVerdict = await self.rate_limit.claim_delivery(
            key=self._delivery_key(
                plugin_id=plugin_id,
                company_id=company_id,
                delivery_id=delivery_id,
            ),
            ttl_seconds=self.limits.delivery_ttl_seconds,
            token=token,
        )

        if verdict.status is DeliveryStatus.DUPLICATE:
            # Повтор с тем же id, но другим телом/событием = подделка replay.
            if verdict.previous_token is not None and not hmac.compare_digest(
                verdict.previous_token, token
            ):
                await self._audit(
                    "plugin_webhook_received",
                    plugin_id=plugin_id,
                    company_id=company_id,
                    decision="deny",
                    code=GatewayErrorCode.REPLAY_DETECTED.value,
                    reason="delivery id reused with different payload",
                )
                raise GatewayError(
                    GatewayErrorCode.REPLAY_DETECTED,
                    detail="delivery id replay with a different payload",
                )
            claims = GatewayClaims(
                plugin_id=plugin_id,
                company_id=company_id,
                subject_id="webhook",
                scopes=WEBHOOK_SCOPES,
                grant_id=grant.grant_id,
            )
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
            )

        claims = GatewayClaims(
            plugin_id=plugin_id,
            company_id=company_id,
            subject_id="webhook",
            scopes=WEBHOOK_SCOPES,
            grant_id=grant.grant_id,
        )
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
        return WebhookAuthResult(
            claims=claims,
            grant=grant,
            event=event,
            delivery_id=delivery_id,
            duplicate=False,
        )


__all__ = [
    "DEFAULT_LIMITS",
    "HEADER_DELIVERY",
    "HEADER_EVENT",
    "HEADER_SIGNATURE",
    "GatewayAuth",
    "GatewayLimits",
    "PageAuthResult",
    "Sha256HmacVerifier",
    "WebhookAuthResult",
]
