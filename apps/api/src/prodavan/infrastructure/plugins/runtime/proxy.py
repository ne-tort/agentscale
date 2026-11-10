"""``HttpProxyPort`` — тонкий адаптер ``ProxyPort`` activator'а (задача 09).

Важное решение: НЕ дублируем Gateway
------------------------------------
Соседний агент (задача 11) УЖЕ реализовал HTTP-транспорт в
``infrastructure/plugins/adapters/gateway_adapters.py``:

* :class:`BackendHttpAdapter` — ``httpx.AsyncClient``, ``stream=True`` и живой
  ``AsyncIterator[bytes]`` без ``aread()``/``content``/``b"".join(...)``;
* :func:`with_trusted_bearer` / :class:`BearerAuthorizationInjector` — ЯВНО
  добавляют доверенный ``Authorization: Bearer <WakeResult.token>`` к
  ``X-Plugin-Capability-Token`` (исправление стыка 02/09/11);
* :func:`build_forward_headers` в ``gateway/headers.py`` — канонический
  allowlist доверенных ``X-Plugin-*`` с отбросом клиентских
  ``Authorization``/``Cookie``/``X-Plugin-*``/hop-by-hop.

Здесь НЕТ второго httpx-клиента, второго SSE-генератора и второй реализации
allowlist'а. :class:`HttpProxyPort` — ТОНКИЙ мост между узким контрактом
``activation/ports.py::ProxyPort``::

    async forward(*, plugin_instance_id, request, claims: MintedClaims) -> Any

и ``gateway/ports.py::BackendHttpPort``::

    async send(request: BackendRequest) -> BackendResponse

Что делает адаптер
------------------
1. Берёт **свежий** ``claims.token`` (выпущенный при wake, а не истёкший из
   Secret, §4.2/§7.4) и строит доверенные ``X-Plugin-*`` заголовки ИСКЛЮЧИТЕЛЬНО
   из ``claims.claims`` через :func:`build_forward_headers` (санитизация
   клиентских значений -> доверенная перезапись ПОСЛЕДНИМИ).
2. Добавляет ``Authorization: Bearer <token>`` ЯВНО и ПОСЛЕДНИМ — ровно то,
   чего не делает ``headers.py::TRUSTED_HEADERS`` (там доверенный Bearer
   сознательно не выставляется, см. ``headers.py:38-43``). Оба заголовка несут
   один и тот же свежий токен из ``WakeResult.token``.
3. Передаёт ``BackendRequest`` в ``BackendHttpAdapter.send``. SSE остаётся
   живым ``BackendResponse.stream``: тело нигде не читается целиком, соединение
   закрывается потребителем потока (Release), а не этим адаптером.

Динамические имена Service
--------------------------
Deployment/Service плагина называются ``plugin-<id>-<instance>`` (§5.1),
поэтому базовый URL НЕ может быть одним на все инстансы. ``backend_http_factory``
строит транспорт под конкретный ``plugin_instance_id``; адаптеры кэшируются и
живут до :meth:`aclose`. Явный ``backend_http=`` (для тестов) отключает фабрику.

Держать соединение до Release
-----------------------------
``httpx.AsyncClient`` создаётся ОДИН РАЗ на инстанс и НЕ закрывается в
``forward``. ``aclose()`` вызывается владельцем платформы при shutdown. Для SSE
это критично: закрытие клиента сразу после ``send`` оборвало бы поток.

Fail-closed
-----------
Отсутствие транспорта, пустой/истёкший токен, нераспознанный ``request``,
ошибка ``send`` -> ЯВНАЯ ошибка (``ProxyConfigurationError`` / ``NotReady``),
а не «200 без тела». Кэша токенов НЕТ: каждый ``forward`` берёт новый.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from prodavan.application.plugins.activation.errors import ActivationErrorCode, NotReady
from prodavan.application.plugins.activation.ports import MintedClaims
from prodavan.application.plugins.gateway.headers import (
    HEADER_CAPABILITY_TOKEN,
    HEADER_COMPANY_ID,
    HEADER_PLUGIN_ID,
    HEADER_PROJECT_ID,
    build_forward_headers,
)
from prodavan.application.plugins.gateway.ports import (
    BackendRequest,
    BackendResponse,
    GatewayClaims,
    GatewayGrant,
    is_valid_artifact_digest,
)

from .config import RuntimeConfig
from .errors import RuntimeConfigError
from .k8s_scale import plugin_service_name, validate_plugin_resource_name

logger = logging.getLogger(__name__)

HEADER_AUTHORIZATION = "Authorization"
BEARER_PREFIX = "Bearer "

#: Доверенные ``X-Plugin-*`` (перезаписываются из claims, а не из запроса).
TRUSTED_X_PLUGIN_HEADERS: frozenset[str] = frozenset(
    {
        HEADER_PLUGIN_ID,
        HEADER_COMPANY_ID,
        HEADER_PROJECT_ID,
        HEADER_CAPABILITY_TOKEN,
    }
)

#: Дополнительные доверенные заголовки, которых нет в allowlist'е Gateway.
HEADER_INSTANCE_ID = "X-Plugin-Instance-ID"
HEADER_GRANT_ID = "X-Plugin-Grant-ID"

#: Недоверенные заголовки внешнего клиента: defense-in-depth ДО сборки.
_STRIPPED_EXACT: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "proxy-authenticate",
        "cookie",
        "set-cookie",
        "host",
        "content-length",
        "x-prodavan-signature",
    }
)
_STRIPPED_PREFIXES: tuple[str, ...] = (
    "x-plugin-",
    "x-forwarded-",
    "x-original-",
    "x-real-ip",
    "forwarded",
)
_HOP_BY_HOP: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
    }
)


class ProxyConfigurationError(RuntimeConfigError):
    """Прокси не собран / некорректные входные данные (fail-closed)."""


@runtime_checkable
class BackendSenderPort(Protocol):
    """Структурный минимум ``BackendHttpPort`` (без импорта чужого Protocol)."""

    async def send(self, request: BackendRequest) -> BackendResponse: ...


def sanitize_incoming(headers: Mapping[str, str]) -> dict[str, str]:
    """Defense-in-depth: отбросить НЕдоверенные заголовки клиента.

    Каноническую санитизацию делает :func:`build_forward_headers` задачи 11;
    этот проход — дополнительная страховка на случай прямого вызова адаптера.
    Порядок критичен (ошибка v1 — доверенные распаковывались ДО клиентских).
    """
    out: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str) or not value:
            continue
        lowered = name.lower()
        if lowered in _STRIPPED_EXACT or lowered in _HOP_BY_HOP:
            continue
        if any(lowered.startswith(prefix) for prefix in _STRIPPED_PREFIXES):
            continue
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            continue  # защита от header injection / request splitting
        out[name] = value
    return out


def _gateway_identity(
    claims: MintedClaims, *, plugin_instance_id: str
) -> tuple[GatewayClaims, GatewayGrant]:
    """Project a complete, fresh authoritative identity without any fallback."""
    if not isinstance(claims, MintedClaims) or not claims.token.strip():
        raise ProxyConfigurationError("fresh capability token is required")
    if claims.expired_at(now=datetime.now(UTC)):
        raise ProxyConfigurationError("capability token is expired")
    payload = claims.claims
    if not isinstance(payload, Mapping):
        raise ProxyConfigurationError("claims payload is missing")
    for name in (
        "grant_id",
        "company_id",
        "plugin_id",
        "plugin_instance_id",
        "sub",
    ):
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ProxyConfigurationError(f"claims missing required {name}")
    bound_instance = payload["plugin_instance_id"]
    if bound_instance != plugin_instance_id:
        raise ProxyConfigurationError(
            "claims plugin_instance_id does not match the expected durable instance"
        )
    artifact_digest = payload.get("artifact_digest")
    if not is_valid_artifact_digest(artifact_digest):
        raise ProxyConfigurationError(
            "claims artifact_digest is missing or malformed"
        )
    plugin_id = payload["plugin_id"]
    company_id = payload["company_id"]
    expected_sub = f"plugin:{plugin_id}:{company_id}:{bound_instance}"
    if payload["sub"] != expected_sub:
        raise ProxyConfigurationError(
            "claims sub does not match company/plugin/instance binding"
        )
    project = payload.get("project_id")
    if project is not None and not isinstance(project, str):
        raise ProxyConfigurationError("claims project_id must be a string or null")
    gateway_claims = GatewayClaims(
        plugin_id=plugin_id,
        company_id=company_id,
        subject_id=payload["sub"],
        project_id=project or None,
        grant_id=payload["grant_id"],
        plugin_instance_id=bound_instance,
        artifact_digest=artifact_digest,
    )
    grant = GatewayGrant(
        grant_id=payload["grant_id"],
        plugin_instance_id=bound_instance,
        artifact_digest=artifact_digest,
        active=True,
        capability_token=claims.token.strip(),
    )
    return gateway_claims, grant


def _build_outgoing_headers(
    incoming: Mapping[str, str],
    *,
    token: str,
    claims: GatewayClaims,
    grant: GatewayGrant,
) -> dict[str, str]:
    out = dict(
        build_forward_headers(sanitize_incoming(incoming), claims, grant)
    )
    out[HEADER_GRANT_ID] = grant.grant_id
    # Bearer comes only from the fresh wake token, after all client values.
    out[HEADER_AUTHORIZATION] = f"{BEARER_PREFIX}{token}"
    return out


def trusted_x_plugin_headers(
    claims: MintedClaims,
    *,
    plugin_instance_id: str,
) -> dict[str, str]:
    """Build trusted identity headers exclusively from validated claims."""
    gateway_claims, grant = _gateway_identity(
        claims, plugin_instance_id=plugin_instance_id
    )
    out = dict(build_forward_headers({}, gateway_claims, grant))
    out[HEADER_GRANT_ID] = grant.grant_id
    return out


def build_outgoing_headers(
    incoming: Mapping[str, str],
    claims: MintedClaims,
    *,
    plugin_instance_id: str,
) -> dict[str, str]:
    """Sanitize client headers, then apply exact claims/grant and fresh Bearer."""
    gateway_claims, grant = _gateway_identity(
        claims, plugin_instance_id=plugin_instance_id
    )
    return _build_outgoing_headers(
        incoming,
        token=grant.capability_token,
        claims=gateway_claims,
        grant=grant,
    )


@dataclass(frozen=True, slots=True)
class HeldRequest:
    """Удержанный запрос activator'а (минимальный структурный контракт).

    Activator передаёт ``request: Any`` (Starlette/FastAPI ``Request`` или
    тестовый объект). Поддерживаются: объект с ``method``/``url.path``/
    ``headers``/``body``, Mapping и уже собранный ``BackendRequest``.

    ``body`` не логируется; ``stream`` включает SSE (без буферизации).
    """

    method: str = "GET"
    path: str = "/"
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    stream: bool = False
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.path.startswith("/") or ".." in self.path.split("/"):
            raise ProxyConfigurationError("некорректный путь backend-запроса")
        if not isinstance(self.method, str) or not self.method.strip():
            raise ProxyConfigurationError("HTTP-метод не задан")
        if not isinstance(self.body, (bytes, bytearray)):
            raise ProxyConfigurationError("тело запроса обязано быть bytes")
        if self.timeout_seconds is not None:
            value = float(self.timeout_seconds)
            if value <= 0 or value != value:
                raise ProxyConfigurationError("timeout_seconds должен быть положительным")
            object.__setattr__(self, "timeout_seconds", value)


def coerce_request(request: Any) -> HeldRequest:
    """Привести ``request: Any`` к ``HeldRequest`` (fail-closed).

    Никакого «если не распознали — отправим пустой GET»: неизвестная форма
    запроса = явная ошибка композиции.
    """
    if isinstance(request, BackendRequest):
        return HeldRequest(
            method=request.method,
            path=request.path,
            headers=dict(request.headers),
            body=bytes(request.body),
            stream=bool(request.stream),
            timeout_seconds=float(request.timeout_seconds),
        )
    if isinstance(request, HeldRequest):
        return request
    if isinstance(request, Mapping):
        headers = request.get("headers") or {}
        body = request.get("body") or b""
        return HeldRequest(
            method=str(request.get("method") or "GET"),
            path=str(request.get("path") or "/"),
            headers=dict(headers) if isinstance(headers, Mapping) else {},
            body=bytes(body) if isinstance(body, (bytes, bytearray)) else b"",
            stream=bool(request.get("stream", False)),
            timeout_seconds=request.get("timeout_seconds"),
        )

    method = getattr(request, "method", None)
    headers = getattr(request, "headers", None)
    url = getattr(request, "url", None)
    path = getattr(url, "path", None) if url is not None else getattr(request, "path", None)
    body = getattr(request, "body", None) or b""
    if not isinstance(method, str) or not isinstance(path, str):
        raise ProxyConfigurationError(
            "удержанный запрос не распознан: нужны method + url.path/headers"
        )
    raw_headers: Mapping[str, str] = headers if isinstance(headers, Mapping) else {}
    return HeldRequest(
        method=method,
        path=path,
        headers=dict(raw_headers),
        body=bytes(body) if isinstance(body, (bytes, bytearray)) else b"",
        stream=bool(getattr(request, "stream", False)),
        timeout_seconds=getattr(request, "timeout_seconds", None),
    )


def plugin_service_url(
    plugin_instance_id: str,
    config: RuntimeConfig,
    *,
    plugin_id: str,
    service_name_resolver: Callable[[str], str] | None = None,
) -> str:
    """Canonical per-instance Service URL; общего ``plugin-runtime`` НЕТ.

    Resolver из admin read model имеет приоритет и должен быть ТЕМ ЖЕ callable,
    который передан в ``K8sScalePort``. Без resolver оба id обязательны для
    единого DNS1035 builder; instance-only short name не создаётся.
    """
    if service_name_resolver is not None:
        name = validate_plugin_resource_name(str(service_name_resolver(plugin_instance_id)))
    else:
        name = plugin_service_name(
            plugin_id,
            plugin_instance_id,
            prefix=config.deployment_prefix,
        )
    return f"http://{name}.{config.namespace}.svc:{config.service_port}"


def _trusted_header(headers: Mapping[str, str], name: str) -> str:
    lowered = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == lowered and isinstance(value, str):
            return value.strip()
    return ""


class HttpProxyPort:
    """``ProxyPort`` activator'а: тонкий мост к ``BackendHttpPort`` Gateway.

    ``backend_http`` — ЯВНЫЙ транспорт (тесты/один инстанс). Если он не передан,
    используется ``backend_http_factory(instance_id)``; фабрика по умолчанию
    дополнительно получает trusted ``plugin_id`` из claims и создаёт
    :class:`BackendHttpAdapter` задачи 11 под canonical per-instance Service.
    Общий ``resource_name_resolver`` может заменить lookup в admin read model.
    Адаптеры кэшируются до :meth:`aclose`; без транспорта сборка падает.

    ``gateway_forward`` — НЕобязательная ГОТОВАЯ корутина/объект с тем же
    контрактом, что и ``ProxyPort.forward`` (keyword-only ``plugin_instance_id``,
    ``request``, ``claims``). Готовый ``GatewayProxy`` задачи 11 имеет ДРУГУЮ
    сигнатуру (``path``/``method``/``body``/``headers``/``GatewayClaims``/
    ``GatewayGrant``), поэтому напрямую НЕ подходит: интегратор обязан обернуть
    его тонким маппером. Это осознанная граница, а не «дублирование прокси».
    """

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        backend_http: BackendSenderPort | None = None,
        backend_http_factory: Callable[[str], BackendSenderPort] | None = None,
        service_name_resolver: Callable[[str], str] | None = None,
        gateway_forward: Any | None = None,
        client: Any = None,
    ) -> None:
        if not isinstance(config, RuntimeConfig):
            raise ProxyConfigurationError("HttpProxyPort requires RuntimeConfig")
        if backend_http is not None and not callable(getattr(backend_http, "send", None)):
            raise ProxyConfigurationError("backend_http обязан реализовывать send()")
        if backend_http_factory is not None and not callable(backend_http_factory):
            raise ProxyConfigurationError("backend_http_factory обязан быть вызываемым")
        if service_name_resolver is not None and not callable(service_name_resolver):
            raise ProxyConfigurationError("service_name_resolver обязан быть вызываемым")
        if gateway_forward is not None and not (
            callable(getattr(gateway_forward, "forward", None)) or callable(gateway_forward)
        ):
            raise ProxyConfigurationError(
                "gateway_forward обязан быть callable или реализовывать forward()"
            )
        self._config = config
        self._backend: BackendSenderPort | None = backend_http
        self._service_name_resolver = service_name_resolver
        self._gateway_forward = gateway_forward
        self._owned: list[Any] = []
        # Фабрика по умолчанию: НЕ импортируем соседний каталог на уровне
        # модуля, чтобы тесты могли подменить транспорт без IO. Фабрика
        # КЭШИРУЕТ адаптеры по instance_id: один httpx.AsyncClient на инстанс,
        # соединение живёт до Release (aclose), SSE не обрывается.
        self._factory: Callable[..., BackendSenderPort] | None = backend_http_factory
        self._factory_uses_canonical_identity = False
        self._cache: dict[tuple[str, str], BackendSenderPort] = {}
        if self._backend is None and self._factory is None and self._gateway_forward is None:
            self._factory = self._default_factory(client)
            self._factory_uses_canonical_identity = True
        if self._backend is None and self._factory is None and self._gateway_forward is None:
            raise ProxyConfigurationError("HttpProxyPort: отсутствует HTTP-транспорт")

    def _default_factory(self, client: Any) -> Callable[[str, str], BackendSenderPort]:
        from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
            BackendHttpAdapter,
        )

        config = self._config

        def build(instance_id: str, plugin_id: str) -> BackendSenderPort:
            adapter = BackendHttpAdapter(
                base_url=plugin_service_url(
                    instance_id,
                    config,
                    plugin_id=plugin_id,
                    service_name_resolver=self._service_name_resolver,
                ),
                client=client,
                max_timeout_seconds=config.stream_timeout_seconds,
                inject_trusted_bearer=True,
            )
            self._owned.append(adapter)
            return adapter  # type: ignore[return-value]

        return build

    @property
    def backend_http(self) -> BackendSenderPort | None:
        return self._backend

    @property
    def gateway_forward(self) -> Any | None:
        return self._gateway_forward

    def _resolve_backend(
        self,
        plugin_instance_id: str,
        *,
        plugin_id: str | None = None,
    ) -> BackendSenderPort:
        if self._backend is not None:
            return self._backend
        if self._factory is None:
            raise NotReady(
                plugin_instance_id=plugin_instance_id,
                detail=f"HTTP-транспорт плагина не подключён "
                f"({ActivationErrorCode.PROXY_MISSING.value})",
            )
        cache_key = (plugin_id or "", plugin_instance_id)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        if self._factory_uses_canonical_identity:
            if not plugin_id:
                raise ProxyConfigurationError(
                    "canonical Service URL требует plugin_id + plugin_instance_id"
                )
            created = self._factory(plugin_instance_id, plugin_id)  # type: ignore[call-arg]
        else:
            # Пользовательская фабрика сохраняет прежний DI-контракт: resolver
            # имён/маршрутизации целиком принадлежит инъекции.
            created = self._factory(plugin_instance_id)
        if not callable(getattr(created, "send", None)):
            raise ProxyConfigurationError(
                "backend_http_factory вернул объект без send() (fail-closed)"
            )
        self._cache[cache_key] = created
        return created

    async def send(self, request: BackendRequest) -> BackendResponse:
        """``BackendHttpPort`` для Gateway: маршрут по trusted identity headers.

        Текущий общий Gateway ещё не добавляет ``X-Plugin-Instance-ID`` в
        ``BackendRequest``; такой вызов намеренно fail-closed до integration
        с admin resolver. Activation ``forward()`` имеет оба id и работает с
        canonical URL без этого дополнительного заголовка.
        """
        if not isinstance(request, BackendRequest):
            raise ProxyConfigurationError("send ожидает BackendRequest")
        if self._backend is not None:
            return await self._backend.send(request)
        plugin_id = _trusted_header(request.headers, HEADER_PLUGIN_ID)
        instance_id = _trusted_header(request.headers, HEADER_INSTANCE_ID)
        if not plugin_id or not instance_id:
            raise ProxyConfigurationError(
                "per-instance backend send требует trusted X-Plugin-ID и "
                "X-Plugin-Instance-ID; общий plugin-runtime Service запрещён"
            )
        backend = self._resolve_backend(instance_id, plugin_id=plugin_id)
        return await backend.send(request)

    async def forward(
        self,
        *,
        plugin_instance_id: str,
        request: Any,
        claims: MintedClaims,
    ) -> Any:
        """Проксировать удержанный запрос в уже Ready backend плагина.

        Токен берётся из ``claims.token`` КАЖДЫЙ вызов (новый при wake, §7.4).
        Возвращается ``BackendResponse`` без буферизации потокового тела.
        """
        if not isinstance(claims, MintedClaims) or not claims.token.strip():
            raise NotReady(
                plugin_instance_id=plugin_instance_id,
                detail="проксирование без свежего capability-токена запрещено",
            )
        # Истёкший токен не должен уйти в backend (criterion 13, §4.2).
        if claims.expired_at(now=datetime.now(UTC)):
            raise NotReady(
                plugin_instance_id=plugin_instance_id,
                detail="capability-токен истёк до проксирования",
            )

        gateway_claims, grant = _gateway_identity(
            claims, plugin_instance_id=plugin_instance_id
        )

        if self._gateway_forward is not None:
            # Тонкая делегация задаче 11 (готовый маппер с тем же контрактом):
            # Gateway сам строит allowlist и wake, дублирования заголовков нет.
            target = self._gateway_forward
            forward = getattr(target, "forward", None)
            if callable(forward):
                result = forward(
                    plugin_instance_id=plugin_instance_id,
                    request=request,
                    claims=claims,
                )
            else:
                result = target(
                    plugin_instance_id=plugin_instance_id,
                    request=request,
                    claims=claims,
                )
            if hasattr(result, "__await__"):
                return await result
            return result

        held = coerce_request(request)
        headers = _build_outgoing_headers(
            held.headers,
            token=grant.capability_token,
            claims=gateway_claims,
            grant=grant,
        )
        timeout = (
            held.timeout_seconds
            if held.timeout_seconds is not None
            else (
                self._config.stream_timeout_seconds
                if held.stream
                else self._config.backend_timeout_seconds
            )
        )
        backend_request = BackendRequest(
            path=held.path,
            method=held.method.upper(),
            headers=headers,
            body=bytes(held.body),
            timeout_seconds=float(timeout),
            stream=held.stream,
            plugin_instance_id=gateway_claims.plugin_instance_id,
        )
        backend = self._resolve_backend(
            plugin_instance_id, plugin_id=gateway_claims.plugin_id
        )
        try:
            # НЕ читаем response.body и НЕ закрываем соединение здесь: SSE
            # остаётся живым итератором до Release потребителем потока.
            return await backend.send(backend_request)
        except Exception as exc:  # noqa: BLE001 — транспорт -> типизированный отказ
            logger.warning(
                "plugin backend forward failed instance=%s error=%s",
                plugin_instance_id,
                type(exc).__name__,
            )
            raise NotReady(
                plugin_instance_id=plugin_instance_id,
                detail=f"backend плагина недоступен: {type(exc).__name__}",
            ) from exc

    async def aclose(self) -> None:
        """Release: закрыть удерживаемые ``httpx.AsyncClient`` при shutdown.

        Вызывается владельцем платформы (lifespan), НЕ из ``forward``: иначе
        потоковый SSE-ответ оборвался бы на первом чанке.
        """
        targets: list[Any] = list(self._owned)
        if self._backend is not None:
            targets.append(self._backend)
        for target in targets:
            close = getattr(target, "aclose", None)
            if not callable(close):
                continue
            result = close()
            if hasattr(result, "__await__"):
                await result
        self._owned.clear()
        self._cache.clear()


def build_backend_request(
    request: Any,
    claims: MintedClaims,
    *,
    plugin_instance_id: str,
    backend_timeout_seconds: float,
    stream_timeout_seconds: float,
) -> BackendRequest:
    """Собрать ``BackendRequest`` (чистая точка для тестов, без IO).

    Ничего не отправляет: удобно проверять «Authorization из свежего токена +
    доверенные X-Plugin-*» без httpx и без Gateway.
    """
    gateway_claims, grant = _gateway_identity(
        claims, plugin_instance_id=plugin_instance_id
    )
    held = coerce_request(request)
    return BackendRequest(
        path=held.path,
        method=held.method.upper(),
        headers=_build_outgoing_headers(
            held.headers,
            token=grant.capability_token,
            claims=gateway_claims,
            grant=grant,
        ),
        body=bytes(held.body),
        timeout_seconds=(
            stream_timeout_seconds if held.stream else backend_timeout_seconds
        ),
        stream=held.stream,
        plugin_instance_id=gateway_claims.plugin_instance_id,
    )


__all__ = [
    "HEADER_AUTHORIZATION",
    "HEADER_GRANT_ID",
    "HEADER_INSTANCE_ID",
    "TRUSTED_X_PLUGIN_HEADERS",
    "BackendSenderPort",
    "HeldRequest",
    "HttpProxyPort",
    "ProxyConfigurationError",
    "build_backend_request",
    "build_outgoing_headers",
    "coerce_request",
    "plugin_service_url",
    "sanitize_incoming",
    "trusted_x_plugin_headers",
]
