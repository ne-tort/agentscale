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
  ``504``; ошибка upstream → ``502``; отсутствие lease → ``503``;
* **trusted instance assertion**: ``X-Plugin-Instance-ID`` берётся только из
  согласованных ``GatewayClaims``/активного ``GatewayGrant``; lease обязан
  подтвердить тот же grant/tenant/plugin/instance/artifact. Клиентские identity-заголовки
  не являются источником assertion и не попадают в audit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from prodavan.application.plugins.gateway.auth import GatewayLimits
from prodavan.application.plugins.gateway.canonical import (
    canonical_backend_path,
    canonical_raw_query,
)
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
    StaticResponsePolicy,
    VerifiedWebhook,
    WakeLease,
    is_valid_artifact_digest,
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
    static_response_policy: StaticResponsePolicy | None = None

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
        if self.static_http is not None and not isinstance(
            self.static_response_policy, StaticResponsePolicy
        ):
            raise GatewayError(
                GatewayErrorCode.PORT_UNAVAILABLE,
                detail="trusted shell-origin static response policy is not wired",
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

    @staticmethod
    def _required_instance_id(claims: GatewayClaims, grant: GatewayGrant) -> str:
        claims_instance = claims.plugin_instance_id
        grant_instance = grant.plugin_instance_id
        if (
            not isinstance(claims_instance, str)
            or not claims_instance.strip()
            or not isinstance(grant_instance, str)
            or not grant_instance.strip()
        ):
            raise GatewayError(
                GatewayErrorCode.PORT_UNAVAILABLE,
                detail="durable grant plugin_instance_id is required for backend forward",
            )
        if claims_instance != grant_instance:
            raise GatewayError(
                GatewayErrorCode.PORT_UNAVAILABLE,
                detail="claims and durable grant plugin_instance_id mismatch",
            )
        return claims_instance

    @staticmethod
    def _required_artifact_digest(
        claims: GatewayClaims, grant: GatewayGrant
    ) -> str:
        claims_digest = claims.artifact_digest
        grant_digest = grant.artifact_digest
        if (
            not isinstance(claims_digest, str)
            or not isinstance(grant_digest, str)
            or not is_valid_artifact_digest(claims_digest)
            or not is_valid_artifact_digest(grant_digest)
        ):
            raise GatewayError(
                GatewayErrorCode.PORT_UNAVAILABLE,
                detail="durable grant artifact_digest is required for backend forward",
            )
        if claims_digest != grant_digest:
            raise GatewayError(
                GatewayErrorCode.PORT_UNAVAILABLE,
                detail="claims and durable grant artifact_digest mismatch",
            )
        return grant_digest

    @staticmethod
    def _required_static_artifact_digest(
        claims: GatewayClaims, grant: GatewayGrant
    ) -> str:
        """Require the same bundle digest in claims, grant and published artifact."""
        grant_digest = GatewayProxy._required_artifact_digest(claims, grant)
        published_digest = grant.published_artifact_digest
        if not is_valid_artifact_digest(published_digest):
            raise GatewayError(
                GatewayErrorCode.PORT_UNAVAILABLE,
                detail=(
                    "published static artifact identity is unavailable "
                    "(fail-closed; no backend digest fallback)"
                ),
            )
        if published_digest != grant_digest:
            raise GatewayError(
                GatewayErrorCode.PORT_UNAVAILABLE,
                detail=(
                    "durable grant and published static artifact_digest mismatch "
                    "(fail-closed)"
                ),
            )
        return published_digest

    async def _wake_and_wait(
        self,
        claims: GatewayClaims,
        *,
        plugin_instance_id: str,
        expected_artifact_digest: str,
        expected_grant_id: str,
    ) -> WakeLease:
        """Пробудить backend и ДОЖДАТЬСЯ Ready. Возвращает lease с токеном.

        Fail-closed: ``wake`` без lease → 503; ``wait_ready=False`` →
        ``BackendNotReadyError`` (роутер превращает в ``202 + Retry-After``).
        """
        try:
            lease = await self.activation.wake(
                plugin_id=claims.plugin_id,
                company_id=claims.company_id,
                plugin_instance_id=plugin_instance_id,
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

        if (
            lease.plugin_instance_id != plugin_instance_id
            or lease.plugin_id != claims.plugin_id
            or lease.company_id != claims.company_id
            or lease.artifact_digest != expected_artifact_digest
            or lease.grant_id != expected_grant_id
        ):
            await self._audit(
                "plugin_wake_up",
                plugin_id=claims.plugin_id,
                company_id=claims.company_id,
                decision="deny",
                code=GatewayErrorCode.WAKE_LEASE_MISSING.value,
                reason="activator returned a lease with a different grant/tenant/plugin/instance/artifact binding",
            )
            raise GatewayError(
                GatewayErrorCode.WAKE_LEASE_MISSING,
                detail="activator returned a lease with a different grant/tenant/plugin/instance/artifact binding",
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
        query_string: bytes = b"",
        verified_webhook: VerifiedWebhook | None = None,
    ) -> BackendResponse:
        """Пробудить, построить доверенные заголовки и проксировать.

        ``grant`` приходит из ``GatewayAuth``; при wake-up в него подставляется
        **новый короткий capability-token** из lease (§7.2, §7.4). Клиентские
        ``X-Plugin-Capability-Token``/``X-Plugin-Instance-ID`` уже отброшены
        в ``headers.py`` и пересоздаются только из проверенных server-side
        claims/grant/lease. Никакие trusted header values не пишутся в audit.
        """
        normalized_method = method.upper()
        if normalized_method not in IDEMPOTENT_METHODS | {"POST", "PATCH"}:
            raise GatewayError(
                GatewayErrorCode.METHOD_NOT_ALLOWED,
                detail=f"method '{method}' is not supported by the plugin gateway",
            )
        plugin_instance_id = self._required_instance_id(claims, grant)
        artifact_digest = self._required_artifact_digest(claims, grant)
        try:
            backend_path = canonical_backend_path(path)
            canonical_raw_query(query_string)
        except (TypeError, ValueError) as exc:
            raise GatewayError(
                GatewayErrorCode.INVALID_PATH,
                detail="invalid backend path or raw query",
            ) from exc

        # 1) WAKE ДО FORWARD. Ни один send() не выполняется раньше.
        lease = await self._wake_and_wait(
            claims,
            plugin_instance_id=plugin_instance_id,
            expected_artifact_digest=artifact_digest,
            expected_grant_id=grant.grant_id,
        )

        # 2) Новый короткий lease → обновляем доверенный capability-token.
        effective_grant = GatewayGrant(
            grant_id=grant.grant_id,
            plugin_instance_id=plugin_instance_id,
            active=grant.active,
            capability_token=lease.capability_token,
            operations=grant.operations,
            artifact_digest=artifact_digest,
        )

        # 3) Allowlist: клиентские X-Plugin-*/Cookie/Authorization/Forwarded
        #    отброшены, доверенные значения выставлены ПОСЛЕДНИМИ.
        forward_headers = build_forward_headers(
            headers,
            claims,
            effective_grant,
            verified_webhook=verified_webhook,
        )

        # 4) Серверный таймаут; для SSE — отдельный, более длинный.
        timeout = (
            self.limits.stream_timeout_seconds
            if stream
            else self.limits.backend_timeout_seconds
        )
        request = BackendRequest(
            path=backend_path,
            method=normalized_method,
            headers=forward_headers,
            body=body,
            timeout_seconds=timeout,
            stream=stream,
            query_string=query_string,
            max_response_bytes=self.limits.backend_response_limit_bytes,
            plugin_instance_id=plugin_instance_id,
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
        query_string: bytes = b"",
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
        plugin_instance_id = self._required_instance_id(claims, grant)
        artifact_digest = self._required_static_artifact_digest(claims, grant)
        try:
            normalized = canonical_backend_path(path)
            canonical_raw_query(query_string)
        except (TypeError, ValueError) as exc:
            raise GatewayError(
                GatewayErrorCode.INVALID_PATH,
                detail="invalid static path or raw query",
            ) from exc
        if normalized == "/":
            raise GatewayError(
                GatewayErrorCode.INVALID_PATH,
                detail="empty pluginpage path: index.html fallback is forbidden",
            )
        if self.static_http is None:
            raise GatewayError(
                GatewayErrorCode.STATIC_ORIGIN_NOT_CONFIGURED,
                detail="plugin static origin is not configured (fail-closed)",
            )
        policy = self.static_response_policy
        if policy is None:
            raise GatewayError(
                GatewayErrorCode.PORT_UNAVAILABLE,
                detail="trusted shell-origin static response policy is not wired",
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
            plugin_instance_id=plugin_instance_id,
            active=grant.active,
            capability_token="",
            operations=frozenset(),
            artifact_digest=artifact_digest,
            published_artifact_digest=artifact_digest,
        )
        policy_headers = policy.response_headers(
            artifact_digest=artifact_digest
        )
        managed_names = {name.lower() for name in policy_headers}
        static_request_headers = {
            name: value
            for name, value in headers.items()
            if isinstance(name, str) and name.lower() not in managed_names
        }
        request = BackendRequest(
            path=normalized,
            method=method.upper(),
            headers=build_forward_headers(static_request_headers, claims, safe_grant),
            timeout_seconds=self.limits.backend_timeout_seconds,
            stream=False,
            query_string=query_string,
            max_response_bytes=self.limits.backend_response_limit_bytes,
            plugin_instance_id=plugin_instance_id,
        )
        try:
            response = await self.static_http.send(request)
        except GatewayError:
            raise
        except Exception as exc:  # noqa: BLE001 — транспорт -> типизированная ошибка
            raise GatewayError(
                GatewayErrorCode.UPSTREAM_ERROR,
                detail=f"static origin unavailable: {type(exc).__name__}",
            ) from exc

        # Static-origin/client values cannot override evidence or security policy.
        # Remove case-insensitive duplicates before writing canonical server values.
        response_headers = {
            name: value
            for name, value in response.headers.items()
            if isinstance(name, str) and name.lower() not in managed_names
        }
        response_headers.update(policy_headers)
        response.headers = response_headers

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
