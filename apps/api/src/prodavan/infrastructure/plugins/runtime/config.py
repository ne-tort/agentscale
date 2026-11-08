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
* ``PLUGIN_RUNTIME_DEPLOYMENT_PREFIX`` — префикс имени Deployment
  (по умолчанию ``plugin-``; итог ``plugin-<id>-<instance>``).
* ``PLUGIN_RUNTIME_SERVICE_SUFFIX`` — суффикс DNS-имени Service для HTTP-пробы
  ``/health`` (по умолчанию пустой: Deployment и Service носят одно имя).
* ``PLUGIN_RUNTIME_SERVICE_PORT`` — порт Service (по умолчанию 8080).
* ``PLUGIN_RUNTIME_HEALTH_PATH`` — путь пробы (по умолчанию ``/health``).
* ``PLUGIN_RUNTIME_STATE_CONFIGMAP`` — имя ConfigMap состояния; по умолчанию
  ``plugin-activation-state`` (ЗАФИКСИРОВАНО в RBAC ``resourceNames`` задачи 14).
* ``PLUGIN_RUNTIME_COSIGN_BIN`` — путь/имя бинарника (по умолчанию ``cosign``).
* ``PLUGIN_RUNTIME_COSIGN_TIMEOUT`` — таймаут одного вызова cosign (секунды).
* ``PLUGIN_RUNTIME_KMS_KEY_REF`` — НЕобязательный ЗАФИКСИРОВАННЫЙ trust root
  для KMS-fallback (``--key <pub|kms-uri>``). Без keyless, но проверка подписи
  НИКОГДА не отключается (критерий №18, §5.1): пустое/``off``/``none``/``false``
  трактуется как «режим выключен», а не «пропустить verify».
* ``PLUGIN_RUNTIME_BACKEND_TIMEOUT`` — таймаут HTTP-прокси к backend плагина.
* ``PLUGIN_RUNTIME_STREAM_TIMEOUT`` — более длинный таймаут для SSE.

ВАЖНО (self-hosted OIDC, риск из ``.github/workflows/plugin-artifacts.yml``):
keyless-верификация требует, чтобы у раннера был OIDC-токен и cert identity
совпадал с ``COSIGN_CERTIFICATE_IDENTITY``. Если OIDC недоступен — включают
KMS-fallback, но НЕ отключают проверку подписи. Адаптер это контролирует явно.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

from .errors import RuntimeConfigError

# Имена переменных зафиксированы здесь и больше нигде не перечисляются:
# случайный «ещё один алиас» стал бы скрытым фолбэком на чужой контур.
ENV_NAMESPACE = "PLUGIN_RUNTIME_NAMESPACE"
ENV_DEPLOYMENT_PREFIX = "PLUGIN_RUNTIME_DEPLOYMENT_PREFIX"
ENV_SERVICE_SUFFIX = "PLUGIN_RUNTIME_SERVICE_SUFFIX"
ENV_SERVICE_PORT = "PLUGIN_RUNTIME_SERVICE_PORT"
ENV_HEALTH_PATH = "PLUGIN_RUNTIME_HEALTH_PATH"
ENV_STATE_CONFIGMAP = "PLUGIN_RUNTIME_STATE_CONFIGMAP"
ENV_COSIGN_BIN = "PLUGIN_RUNTIME_COSIGN_BIN"
ENV_COSIGN_TIMEOUT = "PLUGIN_RUNTIME_COSIGN_TIMEOUT"
ENV_KMS_KEY_REF = "PLUGIN_RUNTIME_KMS_KEY_REF"
ENV_BACKEND_TIMEOUT = "PLUGIN_RUNTIME_BACKEND_TIMEOUT"
ENV_STREAM_TIMEOUT = "PLUGIN_RUNTIME_STREAM_TIMEOUT"

DEFAULT_DEPLOYMENT_PREFIX = "plugin-"
DEFAULT_SERVICE_SUFFIX = ""
DEFAULT_SERVICE_PORT = 8080
DEFAULT_HEALTH_PATH = "/health"
#: ЗАФИКСИРОВАНО в Role ``plugin-gateway-activator`` (resourceNames задачи 14).
#: Изменение здесь БЕЗ правки манифеста приведёт к 403 при первом же CAS.
DEFAULT_STATE_CONFIGMAP = "plugin-activation-state"
DEFAULT_COSIGN_BIN = "cosign"
DEFAULT_COSIGN_TIMEOUT = 60.0
DEFAULT_BACKEND_TIMEOUT = 30.0
DEFAULT_STREAM_TIMEOUT = 300.0

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")

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


def _validate_namespace(namespace: str) -> str:
    if not _DNS_LABEL.fullmatch(namespace) or len(namespace) > 63:
        raise _fail(f"{ENV_NAMESPACE}={namespace!r} некорректен (RFC 1123 label)")
    return namespace


def _validate_prefix(prefix: str) -> str:
    # Префикс заканчивается '-' (``plugin-``), поэтому валидируем именно
    # «огрызок» до дефиса как DNS-label, а сам префикс — как безопасный набор.
    stem = prefix.rstrip("-")
    if (
        not prefix
        or not stem
        or prefix != prefix.lower()
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-?", prefix)
    ):
        raise _fail(f"{ENV_DEPLOYMENT_PREFIX}={prefix!r} некорректен")
    if not _DNS_LABEL.fullmatch(stem):
        raise _fail(f"{ENV_DEPLOYMENT_PREFIX}={prefix!r} некорректен (RFC 1123 label)")
    return prefix


def _validate_health_path(path: str) -> str:
    if not path.startswith("/") or ".." in path or any(ch.isspace() for ch in path):
        raise _fail(f"{ENV_HEALTH_PATH}={path!r} некорректен (ожидался абсолютный путь)")
    return path


def _validate_binary(binary: str) -> str:
    # Не разрешаем пробелы/переводы строк: argv собирается БЕЗ shell=True,
    # но пустая строка или мусор привели бы к FileNotFoundError -> VERIFY_FAILED.
    if not binary or any(ch.isspace() or ch == "\x00" for ch in binary):
        raise _fail(f"{ENV_COSIGN_BIN}={binary!r} некорректен")
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
    cosign_binary: str = DEFAULT_COSIGN_BIN
    cosign_timeout_seconds: float = DEFAULT_COSIGN_TIMEOUT
    kms_key_ref: str = ""
    backend_timeout_seconds: float = DEFAULT_BACKEND_TIMEOUT
    stream_timeout_seconds: float = DEFAULT_STREAM_TIMEOUT

    def __post_init__(self) -> None:
        _validate_namespace(self.namespace)
        _validate_prefix(self.deployment_prefix)
        _validate_health_path(self.health_path)
        _validate_binary(self.cosign_binary)
        if not self.state_configmap or not _DNS_LABEL.fullmatch(self.state_configmap):
            raise _fail(
                f"{ENV_STATE_CONFIGMAP}={self.state_configmap!r} некорректно; "
                f"имя ЗАФИКСИРОВАНО в RBAC resourceNames (по умолчанию "
                f"{DEFAULT_STATE_CONFIGMAP!r})"
            )
        if self.service_suffix and not _DNS_LABEL.fullmatch(self.service_suffix.lstrip("-")):
            raise _fail(f"{ENV_SERVICE_SUFFIX}={self.service_suffix!r} некорректен")
        if not (1 <= self.service_port <= 65535):
            raise _fail("service_port должен быть 1..65535")
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
        # ЯВНЫЙ запрет «отключить проверку подписи»: such values не Trust Root,
        # а попытка обойти критерий 18. Пустое -> keyless, остальное -> KMS.
        if kms_raw and kms_raw.lower() in _KMS_DISABLED:
            raise _fail(
                f"{ENV_KMS_KEY_REF}={kms_raw!r}: отключение проверки подписи ЗАПРЕЩЕНО "
                "(§5.1, критерий №18); используйте '' для keyless или реальный "
                "KMS/публичный ключ для fallback"
            )
        return cls(
            namespace=_validate_namespace(_text(source, ENV_NAMESPACE, default="", required=True)),
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
        )


__all__ = [
    "DEFAULT_BACKEND_TIMEOUT",
    "DEFAULT_COSIGN_BIN",
    "DEFAULT_COSIGN_TIMEOUT",
    "DEFAULT_DEPLOYMENT_PREFIX",
    "DEFAULT_HEALTH_PATH",
    "DEFAULT_SERVICE_PORT",
    "DEFAULT_SERVICE_SUFFIX",
    "DEFAULT_STATE_CONFIGMAP",
    "DEFAULT_STREAM_TIMEOUT",
    "ENV_BACKEND_TIMEOUT",
    "ENV_COSIGN_BIN",
    "ENV_COSIGN_TIMEOUT",
    "ENV_DEPLOYMENT_PREFIX",
    "ENV_HEALTH_PATH",
    "ENV_KMS_KEY_REF",
    "ENV_NAMESPACE",
    "ENV_SERVICE_PORT",
    "ENV_SERVICE_SUFFIX",
    "ENV_STATE_CONFIGMAP",
    "ENV_STREAM_TIMEOUT",
    "RuntimeConfig",
]
