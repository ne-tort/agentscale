"""Конфигурация runtime-адаптеров плагинов (F11, fail-closed без фолбэков).

Источник значений — ТОЛЬКО переменные окружения ниже (паттерн задачи MinIO:
``infrastructure/plugins/minio/config.py``), плюс явные аргументы фабрики.
НИКАКИХ чтений ``settings`` агентского контура, ``POD_*``, ``S3_*``, kubeconfig
по умолчанию и «dev-дефолтов»: у плагинов отдельный trust-контур (§1/§4.1 v4,
ADR-008). Токен ServiceAccount и CA читает ``InClusterAuth`` из смонтированного
``/var/run/secrets/kubernetes.io/serviceaccount`` — in-cluster kubeconfig здесь
не парсится и не подменяется.

Окружение (все значения из ConfigMap/Secret плагинового контура, БЕЗ секретов
в ConfigMap, см. ``infra/k3s/base/plugins/plugin-gateway.yaml``):

* ``PLUGIN_RUNTIME_NAMESPACE``  — namespace Deployment'ов и ConfigMap
  состояния. ОБЯЗАТЕЛЕН: пустое значение = ошибка, а не ``default``.
* ``PLUGIN_RUNTIME_DEPLOYMENT_PREFIX`` — префикс canonical resource name
  (``plugin`` и ``plugin-`` нормализуются к ``plugin-``; итог
  ``plugin-<id>-<instance>``).
* ``PLUGIN_RUNTIME_SERVICE_SUFFIX`` — ЗАРЕЗЕРВИРОВАНО: до manifest-driven
  descriptor поддерживается только пустое значение; Service = Deployment.
* ``PLUGIN_RUNTIME_SERVICE_PORT`` — ЗАРЕЗЕРВИРОВАНО: только 8080.
* ``PLUGIN_RUNTIME_HEALTH_PATH`` — ЗАРЕЗЕРВИРОВАНО: только ``/health``.
* ``PLUGIN_RUNTIME_STATE_CONFIGMAP`` — имя ConfigMap состояния; по умолчанию
  ``plugin-activation-state`` (ЗАФИКСИРОВАНО в RBAC ``resourceNames`` задачи 14).
* ``PLUGIN_RUNTIME_IMAGE_PULL_SECRET`` — НЕобязательное ИМЯ Secret для pull
  приватного backend image. Только RFC 1123 subdomain (например,
  ``plugin-ghcr-pull``), не dockerconfigjson, token или registry URL. Без имени
  Deployment не получает поле ``imagePullSecrets``.
* ``PLUGIN_RUNTIME_COSIGN_BIN`` — путь/имя бинарника (по умолчанию ``cosign``).
* ``PLUGIN_RUNTIME_COSIGN_TIMEOUT`` — таймаут одного вызова cosign (секунды).
* ``PLUGIN_RUNTIME_KMS_KEY_REF`` — НЕобязательный ЗАФИКСИРОВАННЫЙ trust root
  для KMS-fallback (``--key <pub|kms-uri>``). Без keyless, но проверка подписи
  НИКОГДА не отключается (критерий №18, §5.1): пустое/``off``/``none``/``false``
  трактуется как «режим выключен», а не «пропустить verify».
* ``PLUGIN_RUNTIME_BACKEND_TIMEOUT`` — таймаут HTTP-прокси к backend плагина.
* ``PLUGIN_RUNTIME_STREAM_TIMEOUT`` — более длинный таймаут для SSE.
 * PLUGIN_RUNTIME_CPU_REQUEST / PLUGIN_RUNTIME_MEMORY_REQUEST — requests
 *   backend-контейнера (по умолчанию 100m / 128Mi).
 * PLUGIN_RUNTIME_CPU_LIMIT / PLUGIN_RUNTIME_MEMORY_LIMIT — limits
 *   backend-контейнера (по умолчанию 500m / 512Mi).

ВАЖНО (self-hosted OIDC, риск из ``.github/workflows/plugin-artifacts.yml``):
keyless-верификация требует, чтобы у раннера был OIDC-токен и cert identity
совпадал с ``COSIGN_CERTIFICATE_IDENTITY``. Если OIDC недоступен — включают
KMS-fallback, но НЕ отключают проверку подписи. Адаптер это контролирует явно.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit

from .errors import RuntimeConfigError

# Имена переменных зафиксированы здесь и больше нигде не перечисляются:
# случайный «ещё один алиас» стал бы скрытым фолбэком на чужой контур.
ENV_NAMESPACE = "PLUGIN_RUNTIME_NAMESPACE"
ENV_GATEWAY_URL = "PLUGIN_RUNTIME_GATEWAY_URL"
ENV_DEPLOYMENT_PREFIX = "PLUGIN_RUNTIME_DEPLOYMENT_PREFIX"
ENV_SERVICE_SUFFIX = "PLUGIN_RUNTIME_SERVICE_SUFFIX"
ENV_SERVICE_PORT = "PLUGIN_RUNTIME_SERVICE_PORT"
ENV_HEALTH_PATH = "PLUGIN_RUNTIME_HEALTH_PATH"
ENV_STATE_CONFIGMAP = "PLUGIN_RUNTIME_STATE_CONFIGMAP"
ENV_IMAGE_PULL_SECRET = "PLUGIN_RUNTIME_IMAGE_PULL_SECRET"
ENV_COSIGN_BIN = "PLUGIN_RUNTIME_COSIGN_BIN"
ENV_COSIGN_TIMEOUT = "PLUGIN_RUNTIME_COSIGN_TIMEOUT"
ENV_KMS_KEY_REF = "PLUGIN_RUNTIME_KMS_KEY_REF"
ENV_BACKEND_TIMEOUT = "PLUGIN_RUNTIME_BACKEND_TIMEOUT"
ENV_STREAM_TIMEOUT = "PLUGIN_RUNTIME_STREAM_TIMEOUT"
ENV_CPU_REQUEST = "PLUGIN_RUNTIME_CPU_REQUEST"
ENV_MEMORY_REQUEST = "PLUGIN_RUNTIME_MEMORY_REQUEST"
ENV_CPU_LIMIT = "PLUGIN_RUNTIME_CPU_LIMIT"
ENV_MEMORY_LIMIT = "PLUGIN_RUNTIME_MEMORY_LIMIT"

DEFAULT_DEPLOYMENT_PREFIX = "plugin-"
DEFAULT_SERVICE_SUFFIX = ""
DEFAULT_SERVICE_PORT = 8080
DEFAULT_HEALTH_PATH = "/health"
#: ЗАФИКСИРОВАНО в Role и minimal ConfigMap manifest. Create без resourceNames
#: не должен становиться обходом для записи произвольного ConfigMap.
DEFAULT_STATE_CONFIGMAP = "plugin-activation-state"
DEFAULT_COSIGN_BIN = "cosign"
DEFAULT_COSIGN_TIMEOUT = 60.0
DEFAULT_BACKEND_TIMEOUT = 30.0
DEFAULT_STREAM_TIMEOUT = 300.0
DEFAULT_CPU_REQUEST = "100m"
DEFAULT_MEMORY_REQUEST = "128Mi"
DEFAULT_CPU_LIMIT = "500m"
DEFAULT_MEMORY_LIMIT = "512Mi"

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
_DNS1123_SUBDOMAIN = re.compile(
    r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*$"
)

# Backend descriptor ещё не поставляется manifest-driven контрактом. До его
# появления любые отличные значения — ошибка, а не молчаливая «свобода».
SUPPORTED_SERVICE_PORT = DEFAULT_SERVICE_PORT
SUPPORTED_HEALTH_PATH = DEFAULT_HEALTH_PATH

#: Значения, которые ЯВНО означают «KMS-fallback выключен». Всё остальное
#: непустое считается trust root. Запрещено interpreting как «отключить verify».
_KMS_DISABLED = frozenset({"", "off", "none", "false", "disabled", "0", "no"})


def _fail(detail: str) -> RuntimeConfigError:
    return RuntimeConfigError(detail)


def _text(source: Mapping[str, str], name: str, *, default: str, required: bool) -> str:
    raw = source.get(name)
    if raw is None or not str(raw).strip():
        if required:
            raise _fail(
                f"{name} не задана; фолбэк на namespace агентского контура ЗАПРЕЩЁН "
                "(плагины — отдельный trust-контур, ADR-008)"
            )
        return default
    return str(raw).strip()


def _positive_float(source: Mapping[str, str], name: str, *, default: float) -> float:
    raw = source.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise _fail(f"{name}={raw!r} не является числом") from exc
    if value <= 0 or value != value:  # NaN != NaN
        raise _fail(f"{name} должен быть положительным конечным числом")
    return value


def _port(source: Mapping[str, str], name: str, *, default: int) -> int:
    raw = source.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise _fail(f"{name}={raw!r} не является целым числом") from exc
    if not (1 <= value <= 65535):
        raise _fail(f"{name} должен быть 1..65535")
    return value


def _quantity(source: Mapping[str, str], name: str, *, default: str) -> str:
    value = _text(source, name, default=default, required=False)
    # Kubernetes quantity subset: positive decimal plus an optional binary/SI
    # suffix. Negative, zero, whitespace and expression-like values are denied.
    match = re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)?|\.[0-9]+)(m|k|M|G|T|P|E|Ki|Mi|Gi|Ti|Pi|Ei)?",
        value,
    )
    if match is None or float(match.group(1)) <= 0:
        raise _fail(f"{name}={value!r} некорректен: ожидалась положительная k8s quantity")
    return value


def _validate_namespace(namespace: str) -> str:
    if not _DNS_LABEL.fullmatch(namespace) or len(namespace) > 63:
        raise _fail(f"{ENV_NAMESPACE}={namespace!r} некорректен (RFC 1123 label)")
    return namespace


class InternalGatewayUrl(str):
    """Canonical HTTP(S) URL of an in-cluster Kubernetes Service.

    This value is projected into plugin Secrets, so it is never inferred from a
    request Host, static public origin, or an arbitrary external URL. Validation
    is intentionally performed at the projection composition seam: a bad optional
    value leaves that port unwired instead of silently selecting a fallback.
    """

    def __new__(cls, value: str) -> InternalGatewayUrl:
        if isinstance(value, cls):
            return value
        try:
            valid = (
                isinstance(value, str)
                and bool(value)
                and value == value.strip()
                and len(value) <= 2048
                and not any(char.isspace() or char == "\0" for char in value)
            )
            if not valid:
                raise ValueError("invalid URL text")
            parsed = urlsplit(value)
            hostname = parsed.hostname or ""
            parsed.port  # Validate malformed ports without echoing the URL.
            labels = hostname.split(".")
            try:
                ipaddress.ip_address(hostname)
                is_ip = True
            except ValueError:
                is_ip = False
            internal_dns = (
                len(hostname) <= 253
                and hostname == hostname.lower()
                and not hostname.endswith(".")
                and all(_DNS_LABEL.fullmatch(label) for label in labels)
                and not is_ip
                and hostname != "localhost"
                and (len(labels) == 1 or (len(labels) >= 3 and labels[2] == "svc"))
            )
            valid = (
                parsed.scheme in {"http", "https"}
                and internal_dns
                and parsed.username is None
                and parsed.password is None
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise _fail(
                f"{ENV_GATEWAY_URL} должен быть canonical URL внутреннего Service "
                "(http(s)://service[.namespace.svc][:port], без path/query/credentials)"
            )
        return str.__new__(cls, value)


def _validate_image_pull_secret(value: str | None) -> str | None:
    """Accept only a Kubernetes Secret name, never registry credentials."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _fail(f"{ENV_IMAGE_PULL_SECRET} должен содержать только имя Secret")
    name = value.strip()
    if not name:
        return None
    if len(name) > 253 or not _DNS1123_SUBDOMAIN.fullmatch(name):
        raise _fail(
            f"{ENV_IMAGE_PULL_SECRET} некорректен: ожидалось только RFC 1123 "
            "subdomain имя Secret, без URL, данных или credentials"
        )
    return name


def _validate_prefix(prefix: str) -> str:
    """Канонизировать ``plugin``/``plugin-`` к одному безопасному префиксу.

    DNS1035 требует букву первым символом: цифровой префикс нельзя исправить
    дальнейшей склейкой с plugin_id, поэтому он отклоняется здесь.
    """
    if not isinstance(prefix, str):
        raise _fail(f"{ENV_DEPLOYMENT_PREFIX}={prefix!r} некорректен")
    value = prefix.strip()
    stem = value[:-1] if value.endswith("-") else value
    if (
        not stem
        or value != value.lower()
        or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", stem)
        or len(stem) > 62
    ):
        raise _fail(
            f"{ENV_DEPLOYMENT_PREFIX}={prefix!r} некорректен "
            "(ожидался DNS1035 prefix, например 'plugin')"
        )
    return f"{stem}-"


def _validate_health_path(path: str) -> str:
    if not path.startswith("/") or ".." in path or any(ch.isspace() for ch in path):
        raise _fail(f"{ENV_HEALTH_PATH}={path!r} некорректен (ожидался абсолютный путь)")
    return path


def _validate_binary(binary: str) -> str:
    # Не разрешаем пробелы/переводы строк: argv собирается БЕЗ shell=True,
    # но пустая строка или мусор привели бы к FileNotFoundError -> VERIFY_FAILED.
    if not binary or any(ch.isspace() or ch == "\x00" for ch in binary):
        raise _fail(f"{ENV_COSIGN_BIN} некорректен (пробелы/NUL запрещены)")
    return binary


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Неизменяемый снимок конфигурации runtime-адаптеров для одной сборки.

    Поля НЕ содержат секретов: KMS-ref — это публичный ключ/URI доверия, а не
    приватный ключ. В ``repr`` всё безопасно (секретов здесь нет по дизайну).
    """

    namespace: str
    deployment_prefix: str = DEFAULT_DEPLOYMENT_PREFIX
    service_suffix: str = DEFAULT_SERVICE_SUFFIX
    service_port: int = DEFAULT_SERVICE_PORT
    health_path: str = DEFAULT_HEALTH_PATH
    state_configmap: str = DEFAULT_STATE_CONFIGMAP
    image_pull_secret: str | None = None
    cosign_binary: str = DEFAULT_COSIGN_BIN
    cosign_timeout_seconds: float = DEFAULT_COSIGN_TIMEOUT
    kms_key_ref: str = ""
    backend_timeout_seconds: float = DEFAULT_BACKEND_TIMEOUT
    stream_timeout_seconds: float = DEFAULT_STREAM_TIMEOUT
    cpu_request: str = DEFAULT_CPU_REQUEST
    memory_request: str = DEFAULT_MEMORY_REQUEST
    cpu_limit: str = DEFAULT_CPU_LIMIT
    memory_limit: str = DEFAULT_MEMORY_LIMIT
    # Raw optional input; internal_gateway_url() returns the validated value type.
    # Invalid/missing values disable only SecretProjection, never select a fallback.
    gateway_url: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _validate_namespace(self.namespace)
        object.__setattr__(
            self, "deployment_prefix", _validate_prefix(self.deployment_prefix)
        )
        _validate_health_path(self.health_path)
        object.__setattr__(
            self, "image_pull_secret", _validate_image_pull_secret(self.image_pull_secret)
        )
        _validate_binary(self.cosign_binary)
        for field_name, env_name in (
            ("cpu_request", ENV_CPU_REQUEST),
            ("memory_request", ENV_MEMORY_REQUEST),
            ("cpu_limit", ENV_CPU_LIMIT),
            ("memory_limit", ENV_MEMORY_LIMIT),
        ):
            value = _quantity({env_name: getattr(self, field_name)}, env_name, default="1")
            object.__setattr__(self, field_name, value)
        if self.service_port != SUPPORTED_SERVICE_PORT:
            raise _fail(
                f"{ENV_SERVICE_PORT}={self.service_port!r} не поддерживается: "
                f"до manifest-driven backend descriptor зафиксирован порт "
                f"{SUPPORTED_SERVICE_PORT}"
            )
        if self.health_path != SUPPORTED_HEALTH_PATH:
            raise _fail(
                f"{ENV_HEALTH_PATH}={self.health_path!r} не поддерживается: "
                f"до manifest-driven backend descriptor зафиксирован путь "
                f"{SUPPORTED_HEALTH_PATH!r}"
            )
        if self.state_configmap != DEFAULT_STATE_CONFIGMAP:
            raise _fail(
                f"{ENV_STATE_CONFIGMAP}={self.state_configmap!r} не поддерживается: "
                f"activation store ограничен {DEFAULT_STATE_CONFIGMAP!r}; "
                "RBAC create не должен использоваться для других ConfigMap"
            )
        if self.service_suffix != DEFAULT_SERVICE_SUFFIX:
            raise _fail(
                f"{ENV_SERVICE_SUFFIX}={self.service_suffix!r} не поддерживается: "
                "Service и Deployment обязаны иметь одно canonical DNS1035-имя"
            )
        for name in (
            "cosign_timeout_seconds",
            "backend_timeout_seconds",
            "stream_timeout_seconds",
        ):
            value = float(getattr(self, name))
            if value <= 0 or value != value:
                raise _fail(f"{name} должен быть положительным конечным числом")
        if self.kms_key_ref and any(ch.isspace() or ch == "\x00" for ch in self.kms_key_ref):
            raise _fail(f"{ENV_KMS_KEY_REF} не должен содержать пробелы/NUL")
        # KMS-ref не может маскироваться под «отключить проверку»: такие значения
        # отсекает from_env, но и при прямом конструировании запрещаем.
        if self.kms_key_ref.strip().lower() in _KMS_DISABLED - {""}:
            raise _fail(
                f"{ENV_KMS_KEY_REF}={self.kms_key_ref!r}: отключение проверки подписи "
                "ЗАПРЕЩЕНО (§5.1, критерий №18); укажите реальный trust root или '' "
                "для keyless-режима"
            )

    def internal_gateway_url(self) -> InternalGatewayUrl | None:
        """Return the typed trusted URL, or None when projection must stay unwired."""
        if self.gateway_url is None or not str(self.gateway_url).strip():
            return None
        return InternalGatewayUrl(self.gateway_url)

    @property
    def kms_mode(self) -> bool:
        """Включён ли KMS-fallback с зафиксированным trust root (без keyless)."""
        return bool(self.kms_key_ref.strip()) and self.kms_key_ref.strip().lower() not in _KMS_DISABLED

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RuntimeConfig:
        """Построить конфигурацию из окружения (fail-closed).

        ``env=None`` -> ``os.environ``. Явный mapping нужен тестам и делает
        сборку детерминированной. НИ ОДНА переменная чужого контура не читается.
        """
        source: Mapping[str, str] = os.environ if env is None else env
        kms_raw = str(source.get(ENV_KMS_KEY_REF, "") or "").strip()
        # ЯВНЫЙ запрет «отключить проверку подписи»: такие значения — не trust
        # root, а попытка обойти критерий 18. Пустое -> keyless, остальное -> KMS.
        if kms_raw and kms_raw.lower() in _KMS_DISABLED:
            raise _fail(
                f"{ENV_KMS_KEY_REF}={kms_raw!r}: отключение проверки подписи ЗАПРЕЩЕНО "
                "(§5.1, критерий №18); используйте '' для keyless или реальный "
                "KMS/публичный ключ для fallback"
            )
        return cls(
            namespace=_validate_namespace(_text(source, ENV_NAMESPACE, default="", required=True)),
            gateway_url=_text(source, ENV_GATEWAY_URL, default="", required=False) or None,
            deployment_prefix=_text(
                source, ENV_DEPLOYMENT_PREFIX, default=DEFAULT_DEPLOYMENT_PREFIX, required=False
            ),
            service_suffix=_text(
                source, ENV_SERVICE_SUFFIX, default=DEFAULT_SERVICE_SUFFIX, required=False
            ),
            service_port=_port(source, ENV_SERVICE_PORT, default=DEFAULT_SERVICE_PORT),
            health_path=_text(source, ENV_HEALTH_PATH, default=DEFAULT_HEALTH_PATH, required=False),
            state_configmap=_text(
                source, ENV_STATE_CONFIGMAP, default=DEFAULT_STATE_CONFIGMAP, required=False
            ),
            image_pull_secret=_validate_image_pull_secret(
                _text(source, ENV_IMAGE_PULL_SECRET, default="", required=False)
            ),
            cosign_binary=_text(source, ENV_COSIGN_BIN, default=DEFAULT_COSIGN_BIN, required=False),
            cosign_timeout_seconds=_positive_float(
                source, ENV_COSIGN_TIMEOUT, default=DEFAULT_COSIGN_TIMEOUT
            ),
            kms_key_ref=kms_raw,
            backend_timeout_seconds=_positive_float(
                source, ENV_BACKEND_TIMEOUT, default=DEFAULT_BACKEND_TIMEOUT
            ),
            stream_timeout_seconds=_positive_float(
                source, ENV_STREAM_TIMEOUT, default=DEFAULT_STREAM_TIMEOUT
            ),
            cpu_request=_quantity(source, ENV_CPU_REQUEST, default=DEFAULT_CPU_REQUEST),
            memory_request=_quantity(
                source, ENV_MEMORY_REQUEST, default=DEFAULT_MEMORY_REQUEST
            ),
            cpu_limit=_quantity(source, ENV_CPU_LIMIT, default=DEFAULT_CPU_LIMIT),
            memory_limit=_quantity(source, ENV_MEMORY_LIMIT, default=DEFAULT_MEMORY_LIMIT),
        )


__all__ = [
    "DEFAULT_BACKEND_TIMEOUT",
    "DEFAULT_CPU_LIMIT",
    "DEFAULT_CPU_REQUEST",
    "DEFAULT_MEMORY_LIMIT",
    "DEFAULT_MEMORY_REQUEST",
    "DEFAULT_COSIGN_BIN",
    "DEFAULT_COSIGN_TIMEOUT",
    "DEFAULT_DEPLOYMENT_PREFIX",
    "DEFAULT_HEALTH_PATH",
    "DEFAULT_SERVICE_PORT",
    "DEFAULT_SERVICE_SUFFIX",
    "DEFAULT_STATE_CONFIGMAP",
    "DEFAULT_STREAM_TIMEOUT",
    "SUPPORTED_HEALTH_PATH",
    "SUPPORTED_SERVICE_PORT",
    "InternalGatewayUrl",
    "ENV_BACKEND_TIMEOUT",
    "ENV_COSIGN_BIN",
    "ENV_COSIGN_TIMEOUT",
    "ENV_CPU_LIMIT",
    "ENV_CPU_REQUEST",
    "ENV_MEMORY_LIMIT",
    "ENV_MEMORY_REQUEST",
    "ENV_DEPLOYMENT_PREFIX",
    "ENV_HEALTH_PATH",
    "ENV_IMAGE_PULL_SECRET",
    "ENV_KMS_KEY_REF",
    "ENV_NAMESPACE",
    "ENV_GATEWAY_URL",
    "ENV_SERVICE_PORT",
    "ENV_SERVICE_SUFFIX",
    "ENV_STATE_CONFIGMAP",
    "ENV_STREAM_TIMEOUT",
    "RuntimeConfig",
]
