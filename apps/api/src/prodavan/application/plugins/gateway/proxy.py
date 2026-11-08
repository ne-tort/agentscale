"""``GatewayProxy`` — проксирование в спящий backend плагина (§7.2, §7.4).

Контракт:
* **wake до forward**: ``ActivationPort.wake`` вызывается ПЕРЕД любым
  ``BackendHttpPort.send`` (§7.4 «удерживает соединение до Ready, затем
  проксирует»). Проверено тестом «backend спит → wake вызван до forward»;
* **серверный таймаут** берётся из ``GatewayLimits``, НЕ из тела запроса (§4.4);
* **retry ТОЛЬКО на идемпотентные методы** (GET/HEAD/OPTIONS/TRACE/PUT/DELETE),
  и ТОЛЬКО на транспортные ошибки/5xx; POST/PATCH/стриминг — без ретраев;
* **SSE/стриминг без буферизации**: ``BackendResponse.stream`` — живой
  async-итератор, он передаётся в ``StreamingResponse`` как есть, тело нигде
  не читается целиком;
* **недоступность ≠ молчание**: нет Ready → ``202 + Retry-After``; таймаут →
  ``504``; ошибка upstream → ``502``; отсутствие lease → ``503``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from prodavan.application.plugins.gateway.auth import GatewayLimits
from prodavan.application.plugins.gateway.errors import (
    BackendNotReadyError,
    BackendTimeoutError,
    GatewayError,
    GatewayErrorCode,
)
from prodavan.application.plugins.gateway.headers import build_forward_headers
from prodavan.application.plugins.gateway.ports import (
    ActivationPort,
    AuditPort,
    BackendHttpPort,
    BackendRequest,
    BackendResponse,
    GatewayAuditEvent,
    GatewayClaims,
    GatewayGrant,
    WakeLease,
)

#: Идемпотентные методы, для которых допустим retry (RFC 7231 §4.2.2).
#: POST и PATCH намеренно ОТСУТСТВУЮТ: повтор может создать дубль/мутацию.
IDEMPOTENT_METHODS: frozenset[str] = frozenset(
    {"GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE"}
)

#: Заголовки ответа upstream, которые НЕЛЬЗЯ пробрасывать клиенту:
#: hop-by-hop и служебные (длина тела пересчитывается по фактическому ответу).
_STRIPPED_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "content-length",
        "host",
        "www-authenticate",
        "set-cookie",
    }
)


def sanitize_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Отфильтровать hop-by-hop/служебные заголовки ответа backend'а.

    ``Set-Cookie`` отбрасывается: backend плагина не имеет права сажать cookie
    на основной origin (§5.2 — отдельный static-origin без ``allow-same-origin``).
    """
    out: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str) or not value:
            continue
        if name.lower() in _STRIPPED_RESPONSE_HEADERS:
            continue
        out[name] = value
    return out


@dataclass(slots=True)
class GatewayProxy:
    """Исполнение уже авторизованного запроса (решение принято ``GatewayAuth``)."""

    activation: ActivationPort
    backend_http: BackendHttpPort
    audit: AuditPort
    #: Отдельный static-origin (§5.2/§13.3): всегда-running, БЕЗ cookie и БЕЗ
    #: capability-токена. Необязателен на конструкторе, но обязателен при
    #: обращении к статике — иначе fail-closed 503, а не «молчание».
    static_http: BackendHttpPort | None = None
    limits: GatewayLimits = field(default_factory=GatewayLimits)

    def __post_init__(self) -> None:
        for name in ("activation", "backend_http", "audit"):
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

    # ------------------------------------------------------------------ аудит

    async def _audit(self, event: str, **kwargs: Any) -> None:
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
        except Exception as exc:  # noqa: BLE001 — ошибка sink = system failure
            raise GatewayError(
                GatewayErrorCode.AUDIT_APPEND_FAILED,
                detail=f"audit append failed: {type(exc).__name__}",
            ) from exc

    # ------------------------------------------------------------------ wake

    async def _wake_and_wait(self, claims: GatewayClaims) -> WakeLease:
        """Пробудить backend и ДОЖДАТЬСЯ Ready. Возвращает lease с токеном.

        Fail-closed: ``wake`` без lease → 503; ``wait_ready=False`` →
        ``BackendNotReadyError`` (роутер превращает в ``202 + Retry-After``).
        """
        try:
            lease = await self.activation.wake(
                plugin_id=claims.plugin_id, company_id=claims.company_id
            )
        except Exception as exc:  # noqa: BLE001 — любая ошибка активатора явная
            await self._audit(
                "plugin_wake_up",
                plugin_id=claims.plugin_id,
                company_id=claims.company_id,
                decision="deny",
                code=GatewayErrorCode.UPSTREAM_ERROR.value,
                reason=f"activation failed: {type(exc).__name__}",
            )
            raise GatewayError(
                GatewayErrorCode.UPSTREAM_ERROR,
                detail=f"backend activation failed: {type(exc).__name__}",
            ) from exc

        if lease is None or not lease.capability_token:
            await self._audit(
                "plugin_wake_up",
                plugin_id=claims.plugin_id,
                company_id=claims.company_id,
                decision="deny",
                code=GatewayErrorCode.WAKE_LEASE_MISSING.value,
                reason="activator returned no lease/token",
            )
            raise GatewayError(
                GatewayErrorCode.WAKE_LEASE_MISSING,
                detail="activator did not return a wake lease with a capability token",
            )

        ready = await self.activation.wait_ready(
            lease, timeout_seconds=self.limits.ready_timeout_seconds
        )
        if not ready or not lease.ready:
            await self._audit(
                "plugin_wake_up",
                plugin_id=claims.plugin_id,
                company_id=claims.company_id,
                decision="retry",
                code="NOT_READY",
                reason="backend not ready within timeout",
            )
            # Явный «принято, повторите», а не тихое молчание/пустой 200.
            raise BackendNotReadyError("backend not ready after wake-up")

        await self._audit(
            "plugin_wake_up",
            plugin_id=claims.plugin_id,
            company_id=claims.company_id,
            decision="allow",
            reason="backend ready",
        )
        return lease

    # --------------------------------------------------------------- forward

    async def forward(
        self,
        *,
        path: str,
        method: str,
        body: bytes,
        headers: Mapping[str, str],
        claims: GatewayClaims,
        grant: GatewayGrant,
        stream: bool = False,
        request_id: str = "",
    ) -> BackendResponse:
        """Пробудить, построить доверенные заголовки и проксировать.

        ``grant`` приходит из ``GatewayAuth``; при wake-up в него подставляется
        **новый короткий capability-token** из lease (§7.2, §7.4). Клиентский
        ``X-Plugin-Capability-Token`` уже отброшен в ``headers.py``.
        """
        normalized_method = method.upper()
        if normalized_method not in IDEMPOTENT_METHODS | {"POST", "PATCH"}:
            raise GatewayError(
                GatewayErrorCode.METHOD_NOT_ALLOWED,
                detail=f"method '{method}' is not supported by the plugin gateway",
            )
        if not path or ".." in path.split("/"):
            raise GatewayError(
                GatewayErrorCode.INVALID_PATH,
                detail="invalid backend path",
            )

        # 1) WAKE ДО FORWARD. Ни один send() не выполняется раньше.
        lease = await self._wake_and_wait(claims)

        # 2) Новый короткий lease → обновляем доверенный capability-token.
        effective_grant = GatewayGrant(
            grant_id=grant.grant_id,
            active=grant.active,
            capability_token=lease.capability_token,
            operations=grant.operations,
        )

        # 3) Allowlist: клиентские X-Plugin-*/Cookie/Authorization/Forwarded
        #    отброшены, доверенные значения выставлены ПОСЛЕДНИМИ.
        forward_headers = build_forward_headers(headers, claims, effective_grant)

        # 4) Серверный таймаут; для SSE — отдельный, более длинный.
        timeout = (
            self.limits.stream_timeout_seconds
            if stream
            else self.limits.backend_timeout_seconds
        )
        request = BackendRequest(
            path="/" + path.strip("/"),
            method=normalized_method,
            headers=forward_headers,
            body=body,
            timeout_seconds=timeout,
            stream=stream,
        )

        # 5) Retry ТОЛЬКО на идемпотентные методы и ТОЛЬКО не-стриминг.
        attempts = (
            self.limits.max_idempotent_retries + 1
            if normalized_method in IDEMPOTENT_METHODS and not stream
            else 1
        )
        response: BackendResponse | None = None
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                candidate = await self.backend_http.send(request)
            except GatewayError:
                raise
            except Exception as exc:  # noqa: BLE001 — транспорт -> типизированная ошибка
                last_error = exc
                response = None
                if attempt < attempts:
                    continue
                break

            # 5xx от backend — повторяем только идемпотентный запрос.
            if candidate.status_code >= 500 and attempt < attempts:
                response = candidate
                continue
            response = candidate
            break

        if response is None:
            await self._audit(
                "plugin_backend_forward",
                plugin_id=claims.plugin_id,
                company_id=claims.company_id,
                decision="deny",
                code=GatewayErrorCode.UPSTREAM_ERROR.value,
                reason=f"backend transport error: {type(last_error).__name__ if last_error else 'unknown'}",
                request_id=request_id,
            )
            # TimeoutError/asyncio.TimeoutError — отдельный 504, остальное 502.
            is_timeout = last_error is not None and (
                isinstance(last_error, BackendTimeoutError)
                or type(last_error).__name__ in {"TimeoutError", "TimeoutException"}
            )
            raise GatewayError(
                GatewayErrorCode.BACKEND_TIMEOUT if is_timeout else GatewayErrorCode.UPSTREAM_ERROR,
                detail="plugin backend is unavailable",
                retry_after_seconds=(
                    self.limits.retry_after_seconds if is_timeout else None
                ),
            )

        # 6) Стриминг НЕ буферизуем: возвращаем живой итератор как есть.
        #    Ни ``.body``, ни ``b"".join(...)`` здесь не вызываются.
        await self._audit(
            "plugin_backend_forward",
            plugin_id=claims.plugin_id,
            company_id=claims.company_id,
            decision="allow",
            code=str(response.status_code),
            request_id=request_id,
            metadata={"stream": response.is_streaming, "method": normalized_method},
        )
        return response

    # --------------------------------------------------------------- статика

    async def forward_static(
        self,
        *,
        path: str,
        method: str,
        headers: Mapping[str, str],
        claims: GatewayClaims,
        grant: GatewayGrant,
        is_api: bool,
        request_id: str = "",
    ) -> BackendResponse:
        """Отдать статику pluginpage с отдельного static-origin (§5.2).

        Жёсткие правила:
        * ``is_api=True`` сюда не попадает NEVER: ``/api/*`` идёт только в
          backend через ``forward``. Анонимный ``/api/*`` отсечён ещё в
          ``GatewayAuth`` (401, критерий §11 №6);
        * НЕТ пробуждения backend'а: статика живёт на отдельном always-running
          origin (§13.3 «v1 static-origin»);
        * НЕТ подстановки index.html: пустой ``path`` → ``400``, а не SPA-shell
          (критерий §11 №9 — «/webhook/ и /pluginpage/ не уезжают в SPA»);
        * capability-токен и cookie НЕ передаются на static-origin.
        """
        if is_api:
            raise GatewayError(
                GatewayErrorCode.INVALID_PATH,
                detail="/api/* must be proxied to the backend, not to static origin",
            )
        normalized = path.strip("/")
        if not normalized:
            raise GatewayError(
                GatewayErrorCode.INVALID_PATH,
                detail="empty pluginpage path: index.html fallback is forbidden",
            )
        if ".." in normalized.split("/"):
            raise GatewayError(
                GatewayErrorCode.INVALID_PATH,
                detail="invalid static path",
            )
        if self.static_http is None:
            raise GatewayError(
                GatewayErrorCode.STATIC_ORIGIN_NOT_CONFIGURED,
                detail="plugin static origin is not configured (fail-closed)",
            )
        if method.upper() not in {"GET", "HEAD"}:
            raise GatewayError(
                GatewayErrorCode.METHOD_NOT_ALLOWED,
                detail="static origin accepts GET/HEAD only",
            )

        # Доверенные заголовки identity нужны и статике, но capability-токен
        # туда НЕ уходит: у static-origin нет /.capabilities и нет cookie.
        safe_grant = GatewayGrant(
            grant_id=grant.grant_id,
            active=grant.active,
            capability_token="",
            operations=frozenset(),
        )
        request = BackendRequest(
            path="/" + normalized,
            method=method.upper(),
            headers=build_forward_headers(headers, claims, safe_grant),
            timeout_seconds=self.limits.backend_timeout_seconds,
            stream=False,
        )
        try:
            response = await self.static_http.send(request)
        except Exception as exc:  # noqa: BLE001 — транспорт -> типизированная ошибка
            raise GatewayError(
                GatewayErrorCode.UPSTREAM_ERROR,
                detail=f"static origin unavailable: {type(exc).__name__}",
            ) from exc

        await self._audit(
            "plugin_page_opened",
            plugin_id=claims.plugin_id,
            company_id=claims.company_id,
            decision="allow",
            code=str(response.status_code),
            subject_id=claims.subject_id,
            request_id=request_id,
            metadata={"static": True, "public": claims.anonymous},
        )
        return response

    # --------------------------------------------------------------- SSE/stream

    @staticmethod
    def streaming_chunks(response: BackendResponse) -> AsyncIterator[bytes]:
        """Вернуть НЕБУФЕРИЗОВАННЫЙ async-итератор чанков для ``StreamingResponse``.

        Fail-closed: если upstream вернул обычный (не потоковый) ответ, это
        ошибка контракта, а не «молчаливый» пустой поток.
        """
        if response.stream is None:
            raise GatewayError(
                GatewayErrorCode.UPSTREAM_ERROR,
                detail="upstream did not return a streaming response",
            )
        return response.stream

    @staticmethod
    def response_headers(response: BackendResponse, *, stream: bool) -> dict[str, str]:
        """Безопасные заголовки ответа + запрет буферизации для SSE."""
        out = sanitize_response_headers(response.headers)
        if stream:
            # Прямой запрет буферизации на reverse-proxy (nginx/Traefik).
            out["Cache-Control"] = "no-cache, no-transform"
            out["X-Accel-Buffering"] = "no"
        return out


__all__ = [
    "IDEMPOTENT_METHODS",
    "GatewayProxy",
    "sanitize_response_headers",
]
