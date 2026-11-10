"""k8s-адаптеры activator'а: ``ScalePort`` + ``ReadinessPort`` + CAS-состояние.

Реализует три порта из ``application/plugins/activation/ports.py`` (§7.4 v4,
ADR-008, критерии №13/№14):

=========================  ==========================================
Порт                       Адаптер
=========================  ==========================================
``ScalePort``              :class:`K8sScalePort`
``ReadinessPort``          :class:`K8sScalePort` (тот же объект)
``StateStorePort``         :class:`ConfigMapStateStore`
=========================  ==========================================

Почему НЕ ``SandboxClaim``: у плагинов обычный Deployment ``replicas: 0``
и ``readinessProbe /health``. Runtime-created Deployment/Service НЕ отслеживаются
Argo Application, поэтому широкий ``ignoreDifferences /spec/replicas`` не нужен;
``ensure_running`` агентов не ждёт Ready (§7.4). Gateway/activator использует
TokenRequest identity ``plugin-deployment-admin`` и патчит ТОЛЬКО подресурс
``deployments/scale`` — см. Role в
``infra/k3s/overlays/dev/plugins/plugin-lifecycle-rbac.yaml``:

* ``deployments/scale``: get / patch (изменение числа реплик);
* ``deployments``: get (ожидание Ready; остальные verbs нужны DeploymentAdmin);
* ``configmaps``: get / patch только для ``plugin-activation-state``;
  отдельный create без ``resourceNames`` (ограничение Kubernetes RBAC).

Адаптер НЕ умеет и НЕ пытается: менять образ/env/DSN плагина, читать pods или
secrets, создавать Deployment, писать любые другие ConfigMap. Любая попытка
упирается в RBAC, а код её просто не формирует.

Имена динамические
------------------
Deployment и Service создаются контроллером при grant и называются
``plugin-<id>-<instance>`` (шаблон
``infra/k3s/overlays/dev/plugins/plugin-deployment-template.yaml``). Порт
получает только ``plugin_instance_id``, поэтому имя вычисляется функцией,
которую можно подменить:

* ``deployment_names`` — явный mapping ``instance_id -> k8s-имя`` (приоритет);
* ``deployment_name_resolver`` — вызываемая ``(instance_id) -> str``;
* без resolver/mapping адаптер fail-closed: из одного instance_id нельзя
  безопасно восстановить canonical ``plugin_id + instance``.

:func:`plugin_resource_name` — ЕДИНЫЙ builder для Deployment и Service:
``plugin-<id>-<instance>``, DNS1035 <= 63. При обрезании сохраняется SHA-256
suffix, поэтому длинные идентификаторы с общим префиксом не схлопываются.
:func:`plugin_deployment_name` и :func:`plugin_service_name` только делегируют
этому builder и не вводят разных short-name алгоритмов.

Fail-closed
-----------
* k8s API недоступен / токен SA отсутствует / невалидный JSON / 403 / 409 / 5xx
  -> ``ScaleUnavailable`` (для scale) или ``StateStoreUnavailable`` (для CAS);
* Deployment не найден (404) -> ``ScaleUnavailable``, а не «реплик 0, считаем
  что спит»;
* ``Deployment.status.readyReplicas < 1`` -> ``False`` («ещё не готово»,
  activator продолжает polling), но НЕ «успех»;
* `/health` через Service DNS не отвечает, когда k8s уже сообщает Ready ->
  ``NotReady`` (противоречие сигналов = явный отказ);
* кэша «последнего удачного ответа» НЕТ: каждый вызов читает k8s API заново.

Стиль k8s-клиента повторяет
``prodavan.infrastructure.k8s.sandbox.client.K8sSandboxClient``: httpx REST без
SDK, ``InClusterAuth`` (SA token + CA из in-cluster kubeconfig), injectable
``api``/``client`` для тестов. Никакого ``kubectl`` и внешнего kubeconfig.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from prodavan.application.plugins.activation.errors import (
    ActivationError,
    NotReady,
    ScaleUnavailable,
)
from prodavan.application.plugins.activation.state import (
    ActivationState,
    DesiredState,
    JobLease,
)
from prodavan.infrastructure.k8s.auth import InClusterAuth

from .config import RuntimeConfig
from .errors import RuntimeConfigError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы k8s API
# ---------------------------------------------------------------------------

APPS_API = "/apis/apps/v1"
CORE_API = "/api/v1"
#: Kubernetes merge patch: меняет ТОЛЬКО перечисленные ключи ``data``, поэтому
#: состояния соседних инстансов не затираются при CAS одного из них.
MERGE_PATCH = "application/merge-patch+json"
SCALE_KIND = "Scale"
CONFIGMAP_KIND = "ConfigMap"
STATE_SCHEMA_VERSION = 1

_DNS1035 = re.compile(r"^[a-z](?:[-a-z0-9]*[a-z0-9])?$")
_MAX_RESOURCE_NAME = 63
_HASH_SUFFIX_LENGTH = 16
_CONFIGMAP_KEY_RE = re.compile(r"^[-._a-zA-Z0-9]{1,253}$")


# ---------------------------------------------------------------------------
# Ошибки транспорта (внутренние; наружу маппятся в доменные)
# ---------------------------------------------------------------------------


class K8sApiError(RuntimeError):
    """Любая ошибка k8s API. Наружу всегда маппится в доменную (fail-closed)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class K8sApiNotFound(K8sApiError):
    """404: Deployment / ConfigMap отсутствует."""


class K8sApiConflict(K8sApiError):
    """409: конфликт ``resourceVersion`` или объект уже создан."""


class K8sApiUnavailable(K8sApiError):
    """API недоступен: сеть, таймаут, токен SA, 5xx."""


class StateStoreUnavailable(ActivationError):
    """Состояние activation недоступно/повреждено: ЯВНЫЙ отказ, не «успех».

    Отдельный подкласс ``ActivationError`` (код стабилен для activator'а):
    битый JSON, отсутствие ``resourceVersion``, 403 на ConfigMap, конфликт CAS
    после исчерпания ретраев — всё это system failure, а не «возьмём дефолт».
    """

    CODE = "PLUGIN_STATE_STORE_UNAVAILABLE"

    def __init__(self, *, plugin_instance_id: str, detail: str | None = None) -> None:
        super().__init__(
            code=self.CODE,
            title="Plugin activation state unavailable",
            status=503,
            detail=detail
            or f"Состояние активации плагина {plugin_instance_id} недоступно",
            extra={"plugin_instance_id": plugin_instance_id},
        )


# ---------------------------------------------------------------------------
# Нормализация имён (должна совпадать у контроллера и у адаптера!)
# ---------------------------------------------------------------------------


def normalize_k8s_name(value: str) -> str:
    """Нормализовать ОДИН компонент имени БЕЗ потерь от plain truncation.

    Обрезание полного resource name выполняет только
    :func:`plugin_resource_name`, добавляя collision-resistant digest.
    """
    if not isinstance(value, str) or not value.strip():
        raise RuntimeConfigError("идентификатор плагина не может быть пустым")
    lowered = value.strip().lower()
    cleaned = re.sub(r"[^a-z0-9-]", "-", lowered)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    if not cleaned:
        raise RuntimeConfigError(f"идентификатор {value!r} не даёт валидного k8s-имени")
    return cleaned


def _canonical_prefix(prefix: str) -> str:
    if not isinstance(prefix, str):
        raise RuntimeConfigError("prefix имени плагина должен быть строкой")
    value = prefix.strip()
    stem = value[:-1] if value.endswith("-") else value
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", stem):
        raise RuntimeConfigError(f"некорректный DNS1035 prefix: {prefix!r}")
    return f"{stem}-"


def validate_plugin_resource_name(name: str) -> str:
    """Проверить общий контракт Deployment/Service: DNS1035, максимум 63."""
    if (
        not isinstance(name, str)
        or len(name) > _MAX_RESOURCE_NAME
        or not _DNS1035.fullmatch(name)
    ):
        raise RuntimeConfigError(f"некорректное canonical resource name: {name!r}")
    return name


def plugin_resource_name(
    plugin_id: str,
    plugin_instance_id: str,
    *,
    prefix: str = "plugin-",
) -> str:
    """Canonical имя, ОДИНАКОВОЕ для Deployment и Service одного instance.

    Полное нормализованное имя сохраняется, если оно помещается в 63 символа.
    Иначе добавляются 16 hex-символов SHA-256 от ИСХОДНЫХ id и префикса. Два
    длинных id с одинаковым видимым началом поэтому получают разные имена.
    """
    canonical_prefix = _canonical_prefix(prefix)
    raw_plugin_id = plugin_id.strip() if isinstance(plugin_id, str) else plugin_id
    raw_instance_id = (
        plugin_instance_id.strip() if isinstance(plugin_instance_id, str) else plugin_instance_id
    )
    candidate = (
        f"{canonical_prefix}{normalize_k8s_name(raw_plugin_id)}-"
        f"{normalize_k8s_name(raw_instance_id)}"
    )
    if len(candidate) > _MAX_RESOURCE_NAME:
        digest_input = (
            f"{canonical_prefix}\0{raw_plugin_id}\0{raw_instance_id}".encode("utf-8")
        )
        digest = hashlib.sha256(digest_input).hexdigest()[:_HASH_SUFFIX_LENGTH]
        stem_length = _MAX_RESOURCE_NAME - _HASH_SUFFIX_LENGTH - 1
        stem = candidate[:stem_length].rstrip("-")
        candidate = f"{stem}-{digest}"
    return validate_plugin_resource_name(candidate)


def default_deployment_name(
    plugin_instance_id: str,
    *,
    prefix: str = "plugin-",
    plugin_id: str | None = None,
) -> str:
    """Compatibility wrapper; без plugin_id canonical имя намеренно невозможно."""
    if plugin_id is None:
        raise RuntimeConfigError(
            "canonical Deployment name требует plugin_id + plugin_instance_id; "
            "подключите admin resource_name_resolver"
        )
    return plugin_resource_name(plugin_id, plugin_instance_id, prefix=prefix)


def plugin_deployment_name(
    plugin_id: str,
    plugin_instance_id: str,
    *,
    prefix: str = "plugin-",
) -> str:
    return plugin_resource_name(plugin_id, plugin_instance_id, prefix=prefix)


def plugin_service_name(
    plugin_id: str,
    plugin_instance_id: str,
    *,
    prefix: str = "plugin-",
) -> str:
    return plugin_resource_name(plugin_id, plugin_instance_id, prefix=prefix)


def state_key(plugin_instance_id: str) -> str:
    """Ключ ``ConfigMap.data`` для одного инстанса (детерминированный).

    Разрешённый алфавит ключа ConfigMap — ``[-._a-zA-Z0-9]``. Идентификаторы с
    ``/``, ``:`` и т.п. хэшируются: коллизии исключены, функция детерминирована
    для всех реплик Gateway.
    """
    if not isinstance(plugin_instance_id, str) or not plugin_instance_id.strip():
        raise RuntimeConfigError("plugin_instance_id не может быть пустым")
    if _CONFIGMAP_KEY_RE.fullmatch(plugin_instance_id):
        return plugin_instance_id
    digest = hashlib.sha256(plugin_instance_id.encode("utf-8")).hexdigest()[:40]
    return f"state-{digest}"


# ---------------------------------------------------------------------------
# Транспорт k8s API (инъекция для тестов)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class K8sRawResponse:
    """Нормализованный ответ k8s API. Тело — уже dict (невалидный JSON = отказ)."""

    status_code: int
    body: dict[str, Any]

    @property
    def resource_version(self) -> str:
        meta = self.body.get("metadata")
        value = meta.get("resourceVersion") if isinstance(meta, dict) else None
        return str(value) if value is not None else ""


@runtime_checkable
class K8sApiPort(Protocol):
    """Минимум k8s REST: один запрос по ПОЛНОМУ API-пути."""

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> K8sRawResponse: ...


class K8sApiClient:
    """httpx REST к k8s API через ``InClusterAuth`` (без kubernetes SDK).

    ``client`` можно подменить в тестах (объект с async ``request``); ``auth``
    тоже инъектится. ``httpx`` импортируется ЛОКАЛЬНО при первом запросе, чтобы
    импорт модуля не создавал HTTP-клиент и не читал kubeconfig.

    Without token_provider, in-cluster kubeconfig: ``InClusterAuth`` берёт
    ``KUBERNETES_SERVICE_HOST/PORT`` и токен/CA из
    ``/var/run/secrets/kubernetes.io/serviceaccount``. Если пода не в кластере
    (``available() == False``) — сразу ``K8sApiUnavailable``, без «тихого
    успеха» и без попытки сходить в дев-фолбэк. With token_provider, auth
    supplies only host/CA and every request awaits a fresh cached short-lived token.
    """

    def __init__(
        self,
        *,
        auth: InClusterAuth | None = None,
        client: Any = None,
        timeout_seconds: float = 10.0,
        token_provider: Any = None,
    ) -> None:
        if token_provider is not None and not callable(
            getattr(token_provider, "get_token", None)
        ):
            raise RuntimeConfigError("token_provider must implement async get_token()")
        self._auth = auth or InClusterAuth()
        self._token_provider = token_provider
        self._client = client
        self._timeout = float(timeout_seconds)
        if self._timeout <= 0 or self._timeout != self._timeout:
            raise RuntimeConfigError("timeout k8s API должен быть положительным")
        self._closed = False
        self._close_lock = asyncio.Lock()

    @property
    def scoped_identity(self) -> bool:
        """Whether requests use TokenRequest identity instead of the mounted token."""
        return self._token_provider is not None

    def _get_client(self) -> Any:
        if self._closed:
            raise K8sApiUnavailable("k8s API client is closed")
        if self._client is None:
            import httpx  # локальный импорт: import модуля не делает IO

            self._client = httpx.AsyncClient(**self._auth.client_kwargs())
        return self._client

    async def aclose(self) -> None:
        """Close this REST transport once; the token provider is closed by its owner."""
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            client = self._client
            if client is None:
                return
            close = getattr(client, "aclose", None)
            if not callable(close):
                return
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                raise K8sApiUnavailable("k8s API client shutdown failed") from None

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> K8sRawResponse:
        if not isinstance(path, str) or not path.startswith("/"):
            raise K8sApiError("k8s API path должен начинаться с '/'")
        if self._closed:
            raise K8sApiUnavailable("k8s API client is closed")
        try:
            # In provider mode auth supplies only cluster host and CA. Its
            # mounted token is deliberately neither checked nor read.
            api_base = self._auth.api_base()
        except Exception as exc:
            raise K8sApiUnavailable(
                f"не удалось прочитать in-cluster cluster config: {type(exc).__name__}"
            ) from exc
        if self._token_provider is None:
            if not self._auth.available():
                raise K8sApiUnavailable(
                    "in-cluster kubeconfig недоступен (KUBERNETES_SERVICE_HOST/SA token)"
                )
            try:
                headers = dict(self._auth.headers())
            except Exception as exc:  # noqa: BLE001 — чтение токена/CA = отказ
                raise K8sApiUnavailable(
                    f"не удалось прочитать in-cluster auth: {type(exc).__name__}"
                ) from exc
        else:
            try:
                token = await self._token_provider.get_token()
            except Exception:
                raise K8sApiUnavailable(
                    "short-lived lifecycle Kubernetes identity is unavailable"
                ) from None
            if (
                not isinstance(token, str)
                or not 0 < len(token) <= 16384
                or not all(33 <= ord(character) <= 126 for character in token)
            ):
                raise K8sApiUnavailable(
                    "short-lived lifecycle Kubernetes identity is invalid"
                )
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        if content_type:
            headers["Content-Type"] = content_type

        client = self._get_client()
        try:
            response = await client.request(
                method.upper(),
                f"{api_base}{path}",
                headers=headers,
                json=body,
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 — сеть/таймаут/DNS = недоступность
            raise K8sApiUnavailable(f"k8s API недоступен: {type(exc).__name__}") from None

        status = int(getattr(response, "status_code", 0) or 0)
        if status == 404:
            raise K8sApiNotFound("ресурс k8s не найден (404)")
        if status == 409:
            raise K8sApiConflict("конфликт resourceVersion (409)")
        if status == 403:
            raise K8sApiError("доступ к k8s API запрещён RBAC (403)")
        if status >= 400 or status < 200:
            raise K8sApiUnavailable(f"k8s API вернул HTTP {status}")
        if status == 204:
            return K8sRawResponse(status_code=status, body={})
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 — невалидный JSON = отказ
            raise K8sApiError("k8s API вернул невалидный JSON") from exc
        if not isinstance(payload, dict):
            raise K8sApiError("k8s API вернул не объект")
        return K8sRawResponse(status_code=status, body=payload)


def _resolve_scoped_k8s_api(
    *,
    api: K8sApiPort | None,
    token_provider: Any = None,
    auth: InClusterAuth | None = None,
    client: Any = None,
    api_timeout_seconds: float = 10.0,
) -> K8sApiPort:
    """Resolve only a TokenRequest-backed or explicitly scoped custom API.

    A bare ``client``/``auth`` is transport configuration, not identity evidence.
    In particular, never construct the historical mounted-token default when the
    lifecycle client is absent. Custom API ports must opt in with the same
    ``scoped_identity = True`` marker used by the other canonical runtimes.
    """
    if token_provider is not None and not callable(
        getattr(token_provider, "get_token", None)
    ):
        raise RuntimeConfigError("token_provider must implement async get_token()")
    if api is None:
        if token_provider is None:
            raise RuntimeConfigError(
                "lifecycle K8s API is required; default_mounted_service_account_forbidden"
            )
        return K8sApiClient(
            auth=auth,
            client=client,
            timeout_seconds=api_timeout_seconds,
            token_provider=token_provider,
        )
    if not callable(getattr(api, "request", None)):
        raise RuntimeConfigError("lifecycle K8s API must implement async request()")

    provider_marker = getattr(api, "_token_provider", ...)
    if provider_marker is not ...:
        if not callable(getattr(provider_marker, "get_token", None)):
            raise RuntimeConfigError("default_mounted_service_account_forbidden")
        if token_provider is not None and provider_marker is not token_provider:
            raise RuntimeConfigError(
                "lifecycle K8s API and token_provider identity mismatch"
            )
    elif getattr(api, "scoped_identity", False) is not True:
        raise RuntimeConfigError("default_mounted_service_account_forbidden")
    return api


# ---------------------------------------------------------------------------
# HTTP-проба /health через Service DNS
# ---------------------------------------------------------------------------


class ReadinessMode(StrEnum):
    """Как комбинировать k8s-status и HTTP-пробу ``/health``.

    * ``STATUS`` — только ``Deployment.status.readyReplicas >= 1``;
    * ``PROBE`` — только HTTP-проба через Service DNS;
    * ``BOTH`` (по умолчанию, самый fail-closed) — k8s-status AND проба;
    * ``EITHER`` — k8s-status OR проба.

    При ``BOTH`` проба НЕ выполняется, пока k8s сообщает «не Ready»: во время
    холодного старта connection-refused — ожидаемое «ещё не готово» (``False``),
    а не сбой инфраструктуры. Но если k8s УЖЕ Ready, а ``/health`` не отвечает,
    это противоречие -> явный ``NotReady`` (не «успех по статусу»).
    """

    STATUS = "status"
    PROBE = "probe"
    BOTH = "both"
    EITHER = "either"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Результат HTTP-пробы. ``error`` != None = проба НЕ отвечает."""

    status_code: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status_code is not None and 200 <= self.status_code < 300


@runtime_checkable
class HttpProbePort(Protocol):
    async def probe(self, url: str) -> ProbeResult: ...


class ServiceHealthProbe:
    """GET ``/health`` по in-cluster DNS имени Service плагина.

    ``http://<service>.<namespace>.svc:<port>/health`` (короткий DNS-суффикс
    ``.svc`` резолвится через search domains пода, как в as-built
    ``prodavan-api.prodavan.svc``). Никаких ``localhost``/внешних адресов:
    плагин доступен только через свой Service (§6, NP plugin-egress).

    ``follow_redirects=False`` и ``trust_env=False``: redirect/прокси не должны
    подменять адрес пробы (проверка на каждом хопе — зона egress-контроллера).
    """

    def __init__(
        self,
        *,
        client: Any = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._client = client
        self._timeout = float(timeout_seconds)
        if self._timeout <= 0 or self._timeout != self._timeout:
            raise RuntimeConfigError("таймаут health-пробы должен быть положительным")

    def _get_client(self) -> Any:
        if self._client is None:
            import httpx  # локальный импорт: import модуля не делает IO

            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    async def probe(self, url: str) -> ProbeResult:
        if not isinstance(url, str) or not url.startswith("http://"):
            return ProbeResult(error="некорректный URL health-пробы")
        try:
            response = await self._get_client().get(url, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 — любая транспортная ошибка = «не отвечает»
            return ProbeResult(error=type(exc).__name__)
        return ProbeResult(status_code=int(getattr(response, "status_code", 0) or 0))


# ---------------------------------------------------------------------------
# K8sScalePort: ScalePort + ReadinessPort
# ---------------------------------------------------------------------------


class K8sScalePort:
    """PATCH ``deployments/scale`` + Ready по ``status`` и/или ``/health``.

    Ни одного «тихого успеха»: любая ошибка k8s -> ``ScaleUnavailable``,
    отсутствие Deployment -> ``ScaleUnavailable``, противоречие Ready ->
    ``NotReady``. ``is_ready() == False`` означает «ещё не готово» (activator
    продолжит polling), а НЕ «можно проксировать».

    ``max_replicas`` по умолчанию 1: модельный контракт §7.4 — Deployment
    плагина живёт в 0/1 и создаётся вне Argo Application. Расширяется только
    явной настройкой.
    """

    scoped_identity = True

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        api: K8sApiPort | None = None,
        probe: HttpProbePort | None = None,
        readiness_mode: ReadinessMode = ReadinessMode.BOTH,
        deployment_name_resolver: Callable[[str], Any] | None = None,
        deployment_names: Mapping[str, str] | None = None,
        max_replicas: int = 1,
        api_timeout_seconds: float = 10.0,
        probe_timeout_seconds: float = 5.0,
        cluster_domain: str = "svc",
        client: Any = None,
        probe_client: Any = None,
        auth: InClusterAuth | None = None,
        token_provider: Any = None,
    ) -> None:
        if not isinstance(config, RuntimeConfig):
            raise RuntimeConfigError("K8sScalePort requires RuntimeConfig")
        if not isinstance(readiness_mode, ReadinessMode):
            raise RuntimeConfigError("readiness_mode должен быть ReadinessMode")
        if not isinstance(max_replicas, int) or isinstance(max_replicas, bool) or max_replicas < 1:
            raise RuntimeConfigError("max_replicas должен быть целым >= 1")
        if not cluster_domain or any(ch.isspace() for ch in cluster_domain):
            raise RuntimeConfigError("cluster_domain некорректен")
        self._config = config
        self._api: K8sApiPort = _resolve_scoped_k8s_api(
            api=api,
            token_provider=token_provider,
            auth=auth,
            client=client,
            api_timeout_seconds=api_timeout_seconds,
        )
        self._probe: HttpProbePort = probe or ServiceHealthProbe(
            client=probe_client, timeout_seconds=probe_timeout_seconds
        )
        self._readiness_mode = readiness_mode
        self._deployment_names = dict(deployment_names or {})
        self._resolver = deployment_name_resolver
        self._max_replicas = max_replicas
        self._cluster_domain = cluster_domain.strip(".")

    @property
    def api(self) -> K8sApiPort:
        """Shared REST client for the admin lifecycle adapter."""
        return self._api

    # -- имена и пути -------------------------------------------------------

    def deployment_name(self, plugin_instance_id: str) -> str:
        """Динамическое имя Deployment (RBAC без ``resourceNames`` на scale)."""
        if not isinstance(plugin_instance_id, str) or not plugin_instance_id.strip():
            raise RuntimeConfigError("plugin_instance_id не может быть пустым")
        explicit = self._deployment_names.get(plugin_instance_id)
        if explicit is not None:
            name = str(explicit)
        elif self._resolver is not None:
            resolved = self._resolver(plugin_instance_id)
            if inspect.isawaitable(resolved):
                close = getattr(resolved, "close", None)
                if callable(close):
                    close()
                raise RuntimeConfigError(
                    "async durable name resolver доступен только в async scale/readiness методах"
                )
            name = str(resolved)
        else:
            raise RuntimeConfigError(
                "canonical Deployment name требует plugin_id + instance; "
                "передайте deployment_name_resolver/deployment_names из admin read model"
            )
        return validate_plugin_resource_name(name)

    async def _deployment_name_async(self, plugin_instance_id: str) -> str:
        """Resolve through the durable instance projection after API restarts."""
        if not isinstance(plugin_instance_id, str) or not plugin_instance_id.strip():
            raise RuntimeConfigError("plugin_instance_id не может быть пустым")
        explicit = self._deployment_names.get(plugin_instance_id)
        if explicit is not None:
            resolved: Any = explicit
        elif self._resolver is not None:
            resolved = self._resolver(plugin_instance_id)
            if inspect.isawaitable(resolved):
                resolved = await resolved
        else:
            raise RuntimeConfigError(
                "canonical Deployment name требует plugin_id + instance; "
                "передайте durable deployment_name_resolver"
            )
        return validate_plugin_resource_name(str(resolved))

    def service_name(self, plugin_instance_id: str) -> str:
        """Service ВСЕГДА имеет то же canonical имя, что и Deployment."""
        return self.deployment_name(plugin_instance_id)

    def health_url(self, plugin_instance_id: str) -> str:
        """In-cluster URL пробы: ``http://<svc>.<ns>.svc:<port>/health``."""
        service = self.service_name(plugin_instance_id)
        return (
            f"http://{service}.{self._config.namespace}.{self._cluster_domain}"
            f":{self._config.service_port}{self._config.health_path}"
        )

    async def _health_url_async(self, plugin_instance_id: str) -> str:
        service = await self._deployment_name_async(plugin_instance_id)
        return (
            f"http://{service}.{self._config.namespace}.{self._cluster_domain}"
            f":{self._config.service_port}{self._config.health_path}"
        )

    @property
    def _ns(self) -> str:
        return self._config.namespace

    def _scale_path(self, name: str) -> str:
        return f"{APPS_API}/namespaces/{self._ns}/deployments/{name}/scale"

    def _deployment_path(self, name: str) -> str:
        return f"{APPS_API}/namespaces/{self._ns}/deployments/{name}"

    # -- ScalePort ----------------------------------------------------------

    async def current_replicas(self, plugin_instance_id: str) -> int:
        """ФАКТИЧЕСКОЕ число реплик из подресурса ``deployments/scale``.

        Возвращает ``Scale.spec.replicas`` (желаемое, применённое API), а не
        «число из GitOps-манифеста»: иначе повторный wake застанет рассинхрон
        (contract ``ScalePort``, §7.4). 404/API -> ``ScaleUnavailable``.
        """
        name = "<unresolved>"
        try:
            name = await self._deployment_name_async(plugin_instance_id)
            response = await self._api.request("GET", self._scale_path(name))
        except K8sApiNotFound as exc:
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"Deployment {name!r} не найден: wake невозможен",
            ) from exc
        except K8sApiError as exc:
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"deployments/scale недоступен: {exc.reason}",
            ) from exc
        except Exception as exc:  # noqa: BLE001 — fail-closed
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"deployments/scale завершился ошибкой: {type(exc).__name__}",
            ) from exc
        if response.body.get("kind") != SCALE_KIND:
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail="k8s вернул не объект Scale (нарушение контракта API)",
            )
        replicas = (response.body.get("spec") or {}).get("replicas")
        # autoscaling/v1 omits Scale.spec.replicas for the zero value: an
        # omitted field means zero replicas, not a malformed object.
        if replicas is None:
            replicas = 0
        if not isinstance(replicas, int) or isinstance(replicas, bool) or replicas < 0:
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail="Scale.spec.replicas отсутствует или некорректен",
            )
        return replicas

    async def scale_to(self, plugin_instance_id: str, replicas: int) -> None:
        """PATCH ``deployments/scale`` (RBAC: только этот подресурс).

        Меняется ИСКЛЮЧИТЕЛЬНО ``/spec/replicas``: тело patch не содержит
        ``spec.template``, env, image, DSN. Отрицательные/не-int значения и
        значения сверх ``max_replicas`` отклоняются ДО запроса (fail-closed).
        """
        if not isinstance(replicas, int) or isinstance(replicas, bool) or replicas < 0:
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail="replicas обязан быть неотрицательным целым",
            )
        if replicas > self._max_replicas:
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"replicas={replicas} превышает max_replicas={self._max_replicas} (§7.4: 0/1)",
            )
        try:
            name = await self._deployment_name_async(plugin_instance_id)
        except Exception as exc:  # noqa: BLE001 - durable projection failure
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"canonical Deployment name недоступен: {type(exc).__name__}",
            ) from exc
        body = {
            "apiVersion": "autoscaling/v1",
            "kind": SCALE_KIND,
            "metadata": {"name": name, "namespace": self._ns},
            "spec": {"replicas": replicas},
        }
        try:
            response = await self._api.request(
                "PATCH",
                self._scale_path(name),
                body=body,
                content_type=MERGE_PATCH,
            )
        except K8sApiNotFound as exc:
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"Deployment {name!r} не найден при scale_to({replicas})",
            ) from exc
        except K8sApiConflict as exc:
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"конфликт при scale_to({replicas}): {exc.reason}",
            ) from exc
        except K8sApiError as exc:
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"scale_to({replicas}) недоступен: {exc.reason}",
            ) from exc
        except Exception as exc:  # noqa: BLE001 — fail-closed
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"scale_to({replicas}) завершился ошибкой: {type(exc).__name__}",
            ) from exc

        # Подтверждение: API вернул применённое значение. «Отправили и забыли»
        # здесь недопустимо — activator считает wake успешным только по факту.
        if response.body.get("kind") != SCALE_KIND:
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail="k8s вернул не объект Scale (нарушение контракта API)",
            )
        applied = (response.body.get("spec") or {}).get("replicas")
        # autoscaling/v1 omits Scale.spec.replicas for the zero value: omitted
        # means zero ONLY when zero was requested; a missing field for any
        # non-zero target stays a hard confirmation failure (no silent wake).
        effective = 0 if applied is None and replicas == 0 else applied
        if not isinstance(effective, int) or isinstance(effective, bool) or effective != replicas:
            raise ScaleUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"deployments/scale не подтвердил replicas={replicas} (получено {applied!r})",
            )

    # -- ReadinessPort ------------------------------------------------------

    async def is_ready(self, plugin_instance_id: str) -> bool:
        """Ready по ``status.readyReplicas >= 1`` И/ИЛИ HTTP-пробе ``/health``.

        ``False`` — «ещё не готово» (activator опрашивает до таймаута).
        Исключение ``NotReady`` — k8s API недоступен, Deployment исчез, либо
        k8s сообщает Ready, а ``/health`` не отвечает. Никакого «успеха по
        флагу» (критерий №13).
        """
        mode = self._readiness_mode
        status_ready = False
        if mode is not ReadinessMode.PROBE:
            status_ready = await self._status_ready(plugin_instance_id)

        if mode is ReadinessMode.STATUS:
            return status_ready
        if mode is ReadinessMode.BOTH and not status_ready:
            # Холодный старт: под ещё тянет образ / применяет Liquibase.
            # Connection-refused здесь ожидаем, а не сбой инфраструктуры.
            return False
        if mode is ReadinessMode.EITHER and status_ready:
            return True

        try:
            health_url = await self._health_url_async(plugin_instance_id)
        except Exception as exc:  # noqa: BLE001 - durable projection failure
            raise NotReady(
                plugin_instance_id=plugin_instance_id,
                detail=f"canonical Service name недоступен: {type(exc).__name__}",
            ) from exc
        probe = await self._probe.probe(health_url)
        if probe.error is not None:
            # ЯВНАЯ ошибка: readiness probe не отвечает, а «тихий успех»
            # привёл бы к connection-refused на первом же запросе (§7.4).
            raise NotReady(
                plugin_instance_id=plugin_instance_id,
                detail=f"health-проба не отвечает: {probe.error}",
            )
        if probe.ok:
            return True
        if mode is ReadinessMode.BOTH:
            # Противоречие: Deployment Ready, а /health вернул не 2xx.
            raise NotReady(
                plugin_instance_id=plugin_instance_id,
                detail=f"Deployment Ready, но /health вернул HTTP {probe.status_code}",
            )
        return False

    async def _status_ready(self, plugin_instance_id: str) -> bool:
        """Прочитать ``Deployment.status.readyReplicas`` (read-only RBAC)."""
        name = "<unresolved>"
        try:
            name = await self._deployment_name_async(plugin_instance_id)
            response = await self._api.request("GET", self._deployment_path(name))
        except K8sApiNotFound as exc:
            raise NotReady(
                plugin_instance_id=plugin_instance_id,
                detail=f"Deployment {name!r} не найден при проверке Ready",
            ) from exc
        except K8sApiError as exc:
            raise NotReady(
                plugin_instance_id=plugin_instance_id,
                detail=f"k8s API недоступен при проверке Ready: {exc.reason}",
            ) from exc
        except Exception as exc:  # noqa: BLE001 — fail-closed
            raise NotReady(
                plugin_instance_id=plugin_instance_id,
                detail=f"проверка Ready завершилась ошибкой: {type(exc).__name__}",
            ) from exc
        ready = (response.body.get("status") or {}).get("readyReplicas")
        if ready is None:
            # Отсутствие поля = «статус ещё не опубликован», а не «готов».
            return False
        if not isinstance(ready, int) or isinstance(ready, bool) or ready < 0:
            raise NotReady(
                plugin_instance_id=plugin_instance_id,
                detail="Deployment.status.readyReplicas некорректен",
            )
        return ready >= 1


# ---------------------------------------------------------------------------
# Сериализация ActivationState (канонический JSON для межпроцессного CAS)
# ---------------------------------------------------------------------------


def _encode_dt(value: datetime) -> str:
    """aware UTC datetime -> ISO 8601. Naive = отказ (молчаливый баг §11)."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StateStoreUnavailable(
            plugin_instance_id="<encode>",
            detail="datetime в ActivationState обязан быть aware UTC",
        )
    return value.astimezone(UTC).isoformat()


def _decode_dt(value: object, *, field: str, plugin_instance_id: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise StateStoreUnavailable(
            plugin_instance_id=plugin_instance_id,
            detail=f"{field}: отсутствует ISO-строка даты",
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise StateStoreUnavailable(
            plugin_instance_id=plugin_instance_id,
            detail=f"{field}: невалидная ISO-строка даты",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StateStoreUnavailable(
            plugin_instance_id=plugin_instance_id,
            detail=f"{field}: naive datetime запрещён",
        )
    return parsed


def encode_state(state: ActivationState) -> str:
    """Канонический JSON состояния: ОДИНАКОВЫЙ у всех реплик Gateway.

    ``sort_keys`` + фиксированные разделители нужны для CAS: два процесса,
    прочитавшие одно и то же состояние, обязаны получить идентичную строку.
    Сравнение по объектной идентичности (как в ``InMemoryStateStore``) здесь
    НЕ работает — это главное отличие межпроцессной реализации.
    """
    if not isinstance(state, ActivationState):
        raise StateStoreUnavailable(
            plugin_instance_id="<encode>",
            detail="ожидался ActivationState",
        )
    payload: dict[str, Any] = {
        "schema": STATE_SCHEMA_VERSION,
        "plugin_instance_id": state.plugin_instance_id,
        "desired": state.desired.value,
        "last_active_at": _encode_dt(state.last_active_at) if state.last_active_at else None,
        "active_job_ids": sorted(state.active_job_ids),
        "wake_in_flight": bool(state.wake_in_flight),
        "job_leases": {
            job_id: {
                "job_id": lease.job_id,
                "registered_at": _encode_dt(lease.registered_at),
                "expires_at": _encode_dt(lease.expires_at),
            }
            for job_id, lease in sorted(state.job_leases.items())
        },
    }
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise StateStoreUnavailable(
            plugin_instance_id=state.plugin_instance_id,
            detail="ActivationState не сериализуется в канонический JSON",
        ) from exc


def decode_state(raw: str, *, plugin_instance_id: str) -> ActivationState:
    """Восстановить frozen ``ActivationState``. Любое расхождение = отказ."""
    if not isinstance(raw, str) or not raw.strip():
        raise StateStoreUnavailable(
            plugin_instance_id=plugin_instance_id,
            detail="пустое состояние в ConfigMap",
        )
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise StateStoreUnavailable(
            plugin_instance_id=plugin_instance_id,
            detail="состояние в ConfigMap не является JSON",
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema") != STATE_SCHEMA_VERSION:
        raise StateStoreUnavailable(
            plugin_instance_id=plugin_instance_id,
            detail="неизвестная схема состояния в ConfigMap",
        )
    stored_id = payload.get("plugin_instance_id")
    if stored_id != plugin_instance_id:
        # Защита от перепутанного ключа: чужое состояние НЕ подставляем.
        raise StateStoreUnavailable(
            plugin_instance_id=plugin_instance_id,
            detail="состояние в ConfigMap привязано к другому инстансу",
        )
    try:
        desired = DesiredState(str(payload["desired"]))
        last_active_raw = payload.get("last_active_at")
        last_active = (
            _decode_dt(last_active_raw, field="last_active_at", plugin_instance_id=plugin_instance_id)
            if last_active_raw is not None
            else None
        )
        leases_raw = payload.get("job_leases")
        if not isinstance(leases_raw, dict):
            raise ValueError("job_leases должен быть объектом")
        leases: dict[str, JobLease] = {}
        for job_id, item in leases_raw.items():
            if not isinstance(item, dict) or item.get("job_id") != job_id:
                raise ValueError("job_leases: ключ не совпадает с lease.job_id")
            leases[job_id] = JobLease(
                job_id=job_id,
                registered_at=_decode_dt(
                    item.get("registered_at"),
                    field=f"job_leases[{job_id}].registered_at",
                    plugin_instance_id=plugin_instance_id,
                ),
                expires_at=_decode_dt(
                    item.get("expires_at"),
                    field=f"job_leases[{job_id}].expires_at",
                    plugin_instance_id=plugin_instance_id,
                ),
            )
        ids = payload.get("active_job_ids")
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            raise ValueError("active_job_ids должен быть списком строк")
        return ActivationState(
            plugin_instance_id=plugin_instance_id,
            desired=desired,
            last_active_at=last_active,
            active_job_ids=frozenset(ids),
            wake_in_flight=bool(payload.get("wake_in_flight", False)),
            job_leases=leases,
        )
    except StateStoreUnavailable:
        # Уже типизированный отказ (_encode_dt/_decode_dt) — не переупаковываем.
        raise
    except ActivationError as exc:
        # ``ActivationState.__post_init__`` (рассинхрон active_job_ids/leases,
        # пустой id) бросает базовый ActivationError. Приводим к единому типу
        # хранилища, чтобы activator видел system failure, а не «успех».
        raise StateStoreUnavailable(
            plugin_instance_id=plugin_instance_id,
            detail=f"состояние в ConfigMap повреждено: {type(exc).__name__}",
        ) from exc
    except Exception as exc:  # noqa: BLE001 — битые данные = system failure
        raise StateStoreUnavailable(
            plugin_instance_id=plugin_instance_id,
            detail=f"состояние в ConfigMap повреждено: {type(exc).__name__}",
        ) from exc


# ---------------------------------------------------------------------------
# ConfigMapStateStore — StateStorePort с CAS через resourceVersion
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """Прочитанное состояние + оптимистичная версия ConfigMap."""

    state: ActivationState
    resource_version: str
    configmap_exists: bool
    key_exists: bool


class ConfigMapStateStore:
    """``StateStorePort`` на ConfigMap с МЕЖПРОЦЕССНЫМ CAS.

    Отличие от ``InMemoryStateStore`` (задача 09): там CAS сравнивает объект по
    идентичности (``current is not expected``) и держит ``asyncio.Lock`` — это
    защищает только ОДИН процесс. При ``replicas: 2`` Plugin Gateway два wake
    из разных подов оба прошли бы проверку ``wake_in_flight`` и сделали бы два
    PATCH ``scale_to(1)``.

    Здесь CAS двуслойный:

    1. **Семантический**: сравнивается КАНОНИЧЕСКИЙ JSON состояния
       (:func:`encode_state`), а не object identity. Два процесса, прочитавшие
       одно состояние, видят одну строку; изменивший его конкурент — другую.
    2. **Оптимистичный k8s**: в merge-patch кладётся
       ``metadata.resourceVersion`` прочитанного ConfigMap. k8s атомарно
       отклоняет PATCH c чужой версией (409) -> ``compare_and_swap`` возвращает
       ``False`` -> activator повторяет цикл (у него 8 попыток).

    Один ConfigMap на ВСЕ инстансы (имя ``plugin-activation-state`` зафиксировано
    в RBAC ``resourceNames``). Patch меняет ТОЛЬКО один ключ ``data`` — соседние
    состояния не затираются. Известная гонка: ``resourceVersion`` у объекта
    ОДИН на всех, поэтому CAS разных инстансов конфликтуют даже при разных
    ключах; activator обрабатывает это ретраем (риск зафиксирован в отчёте).

    Отсутствие ConfigMap/ключа != ошибка: как и ``InMemoryStateStore.load``,
    возвращается дефолтное состояние ``SUSPENDED`` («ещё не будили»). Запись
    при этом идёт через POST create, и 409 от параллельного создания = ``False``.
    """

    scoped_identity = True

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        api: K8sApiPort | None = None,
        save_attempts: int = 3,
        client: Any = None,
        auth: InClusterAuth | None = None,
        token_provider: Any = None,
    ) -> None:
        if not isinstance(config, RuntimeConfig):
            raise RuntimeConfigError("ConfigMapStateStore requires RuntimeConfig")
        if not isinstance(save_attempts, int) or save_attempts < 1:
            raise RuntimeConfigError("save_attempts должен быть >= 1")
        self._config = config
        self._api: K8sApiPort = _resolve_scoped_k8s_api(
            api=api,
            token_provider=token_provider,
            auth=auth,
            client=client,
        )
        self._save_attempts = save_attempts

    @property
    def configmap_name(self) -> str:
        return self._config.state_configmap

    @property
    def _collection_path(self) -> str:
        return f"{CORE_API}/namespaces/{self._config.namespace}/configmaps"

    @property
    def _object_path(self) -> str:
        return f"{self._collection_path}/{self._config.state_configmap}"

    # -- чтение -------------------------------------------------------------

    async def _read(self, plugin_instance_id: str) -> _Snapshot:
        key = state_key(plugin_instance_id)
        default = ActivationState(plugin_instance_id=plugin_instance_id)
        try:
            response = await self._api.request("GET", self._object_path)
        except K8sApiNotFound:
            # ConfigMap ещё не создан — это «плагин не будили», а не сбой.
            return _Snapshot(default, resource_version="", configmap_exists=False, key_exists=False)
        except K8sApiError as exc:
            raise StateStoreUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"ConfigMap состояния недоступен: {exc.reason}",
            ) from exc
        except Exception as exc:  # noqa: BLE001 — fail-closed
            raise StateStoreUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"чтение состояния завершилось ошибкой: {type(exc).__name__}",
            ) from exc

        if response.body.get("kind") != CONFIGMAP_KIND:
            raise StateStoreUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail="k8s вернул не ConfigMap (нарушение контракта API)",
            )
        resource_version = response.resource_version
        if not resource_version:
            # Без resourceVersion оптимистичный CAS невозможен: «повезёт» здесь
            # быть не должно (иначе два wake снова пройдут одновременно).
            raise StateStoreUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail="ConfigMap не содержит metadata.resourceVersion: CAS невозможен",
            )
        data = response.body.get("data")
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise StateStoreUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail="ConfigMap.data не является объектом",
            )
        if key not in data:
            return _Snapshot(default, resource_version, configmap_exists=True, key_exists=False)
        return _Snapshot(
            state=decode_state(str(data[key]), plugin_instance_id=plugin_instance_id),
            resource_version=resource_version,
            configmap_exists=True,
            key_exists=True,
        )

    # -- запись -------------------------------------------------------------

    async def _write(
        self,
        *,
        plugin_instance_id: str,
        state: ActivationState,
        resource_version: str,
        configmap_exists: bool,
    ) -> bool:
        """Вернуть ``True`` при успехе, ``False`` при конфликте (повторить)."""
        key = state_key(plugin_instance_id)
        payload = encode_state(state)
        metadata: dict[str, Any] = {
            "name": self._config.state_configmap,
            "namespace": self._config.namespace,
        }
        body: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": CONFIGMAP_KIND,
            "metadata": metadata,
            "data": {key: payload},
        }
        if not configmap_exists:
            # Создание: у нового объекта ещё нет resourceVersion. Параллельный
            # create даст 409 -> False -> activator повторит чтение/CAS.
            method, path = "POST", self._collection_path
        else:
            # КЛЮЧЕВОЕ: resourceVersion в merge-patch — оптимистичная блокировка
            # объекта. k8s отклонит PATCH, если кто-то изменил ConfigMap между
            # нашим GET и PATCH (в т.ч. состояние ДРУГОГО инстанса).
            metadata["resourceVersion"] = resource_version
            method, path = "PATCH", self._object_path
        try:
            await self._api.request(
                method,
                path,
                body=body,
                content_type=MERGE_PATCH,
            )
        except (K8sApiConflict, K8sApiNotFound):
            # 409 (чужой resourceVersion / параллельный create) или 404 (объект
            # удалили между чтением и записью) — «состояние изменилось, повтори».
            return False
        except K8sApiError as exc:
            raise StateStoreUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"запись состояния недоступна: {exc.reason}",
            ) from exc
        except Exception as exc:  # noqa: BLE001 — fail-closed
            raise StateStoreUnavailable(
                plugin_instance_id=plugin_instance_id,
                detail=f"запись состояния завершилась ошибкой: {type(exc).__name__}",
            ) from exc
        return True

    # -- StateStorePort ------------------------------------------------------

    async def load(self, plugin_instance_id: str) -> ActivationState:
        """Прочитать состояние. Нет записи -> дефолт (не ошибка, НЕ «Ready»)."""
        snapshot = await self._read(plugin_instance_id)
        return snapshot.state

    async def save(self, state: ActivationState) -> None:
        """Записать состояние без семантического CAS (внутри блокировки воркера).

        Оптимистичная версия всё равно используется: «просто перезаписать»
        потеряло бы изменения другого процесса. При 409 делается несколько
        повторов, затем ``StateStoreUnavailable`` (а не «тихий успех»).
        """
        instance = state.plugin_instance_id
        for _ in range(self._save_attempts):
            snapshot = await self._read(instance)
            if await self._write(
                plugin_instance_id=instance,
                state=state,
                resource_version=snapshot.resource_version,
                configmap_exists=snapshot.configmap_exists,
            ):
                return
        raise StateStoreUnavailable(
            plugin_instance_id=instance,
            detail="не удалось атомарно сохранить состояние: конфликт resourceVersion",
        )

    async def compare_and_swap(
        self,
        *,
        expected: ActivationState,
        new: ActivationState,
    ) -> bool:
        """Атомарно заменить ``expected`` на ``new`` (межпроцессный CAS).

        ``False`` означает «состояние изменилось другой стороной, повторите
        чтение», а НЕ успех. Сравнение идёт по каноническому JSON, поэтому
        работает между разными репликами Gateway (в отличие от
        ``InMemoryStateStore``, сравнивавшего объекты по ``is``).
        """
        if not isinstance(expected, ActivationState) or not isinstance(new, ActivationState):
            raise StateStoreUnavailable(
                plugin_instance_id=getattr(expected, "plugin_instance_id", "<unknown>"),
                detail="CAS ожидает ActivationState",
            )
        if expected.plugin_instance_id != new.plugin_instance_id:
            return False
        instance = expected.plugin_instance_id
        snapshot = await self._read(instance)
        # Семантическое сравнение: object identity здесь бесполезна (другой
        # процесс создал новый объект при deserialize).
        if encode_state(snapshot.state) != encode_state(expected):
            return False
        return await self._write(
            plugin_instance_id=instance,
            state=new,
            resource_version=snapshot.resource_version,
            configmap_exists=snapshot.configmap_exists,
        )


__all__ = [
    "APPS_API",
    "CONFIGMAP_KIND",
    "CORE_API",
    "MERGE_PATCH",
    "SCALE_KIND",
    "STATE_SCHEMA_VERSION",
    "ConfigMapStateStore",
    "HttpProbePort",
    "K8sApiClient",
    "K8sApiConflict",
    "K8sApiError",
    "K8sApiNotFound",
    "K8sApiPort",
    "K8sApiUnavailable",
    "K8sRawResponse",
    "K8sScalePort",
    "ProbeResult",
    "ReadinessMode",
    "ServiceHealthProbe",
    "StateStoreUnavailable",
    "decode_state",
    "default_deployment_name",
    "encode_state",
    "normalize_k8s_name",
    "plugin_deployment_name",
    "plugin_resource_name",
    "plugin_service_name",
    "state_key",
    "validate_plugin_resource_name",
]
