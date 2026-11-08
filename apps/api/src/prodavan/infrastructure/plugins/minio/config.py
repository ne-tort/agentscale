"""Конфигурация MinIO-адаптеров объектной capability плагинов (v4 §5.4, F3).

Источник значений — ТОЛЬКО переменные окружения ниже. Никаких чтений
``settings`` / ``S3_*`` / ``MINIO_ROOT_*`` / ``configmap``: as-built service user
``agentscale-api`` имеет ``agentscale/*`` (``minio-api-policy.json``), root-креды
лежат в том же Secret, и любой «фолбэк на них» мгновенно превращает
bucket-per-tenant обратно в один общий бакет. Поэтому конфигурация **fail-closed**:
нет admin-кредов / некорректный endpoint / неопознанный флаг STS → явная ошибка,
а не дефолт и не «подставим root из configmap».

Связь с задачей 06: контракт ``PluginObjectStore`` не знает об окружении; эта
конфигурация нужна только фабрике ``build_plugin_object_store`` и адаптерам.

Переменные:
  * ``PLUGIN_MINIO_ENDPOINT``              — http(s)://хост:порт MinIO :9000;
  * ``PLUGIN_MINIO_ADMIN_ACCESS_KEY``     — доступ **plugin-object-admin** (НЕ root);
  * ``PLUGIN_MINIO_ADMIN_SECRET_KEY``     — секрет того же service user;
  * ``PLUGIN_MINIO_PRESIGN_ACCESS_KEY``  — доступ **plugin-object-presigner**;
  * ``PLUGIN_MINIO_PRESIGN_SECRET_KEY``  — секрет подписывающего principal;
  * ``PLUGIN_MINIO_STS_ENABLED``          — "false" по умолчанию (см. ниже);
  * ``PLUGIN_MINIO_REGION``               — "us-east-1" по умолчанию.
  * ``PLUGIN_MINIO_PUBLIC_ENDPOINT``      — НЕобязательный внешний host (см. ниже).

ВАЖНО (вердикт родителя: сеть недоступна, официальная документация MinIO STS
не подтверждена): ``PLUGIN_MINIO_STS_ENABLED`` по умолчанию выключен, фабрика
НЕ делает STS основным путём, ``issue_credentials`` падает ``StsUnavailable``,
и единственный рабочий путь — **scoped presigned** от ОТДЕЛЬНОГО principal.
Причина разделения ключей: as-built ``minio-api-policy.json`` даёт service user
``agentscale-api`` весь ``arn:aws:s3:::agentscale/*``; подпись presigned URL
таким ключом унаследовала бы слишком широкие права, и «политика на префикс»
осталась бы только на бумаге. Подписывающий principal имеет СОБСТВЕННУЮ узкую
IAM-политику (``plugin-buckets-init.yaml`` → ``plugin-presign-policy``):
только ``GetObject``/``PutObject`` на ``tenant-*/module-*``.

ВАЖНО (внешние клиенты): in-cluster ``agentscale-minio:9000`` не резолвится
снаружи. Для внешних клиентов нужен ``proxy=1`` ИЛИ подпись СРАЗУ на внешний
host через ``PLUGIN_MINIO_PUBLIC_ENDPOINT`` (перепись Host ПОСЛЕ подписи
инвалидирует SigV4). По умолчанию её нет → подпись на in-cluster endpoint.
Presigned URL и ``X-Amz-Signature`` НИКОГДА не логируются: в аудите только
``key_digest`` (§4.5, кр. 11).

ВАЖНО (отзыв, к чужому ``service.py`` задачи 06 НЕ прикасаемся): его
``revoke()`` пишет «STS-сессии не продлеваются». В presign-режиме это неточно:
выданные URL живут до ``X-Amz-Expires`` ≤ 3600 с и НЕ отзываются мгновенно.
Отзыв = запрет НОВЫХ URL + деактивация ``CapabilityGrant``; при необходимости
мгновенного отзыва — РОТАЦИЯ ключа ``plugin-object-presigner``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit

from prodavan.application.plugins.objects.errors import BucketUnavailable

# Имена переменных зафиксированы здесь и больше нигде не перечисляются: случайный
# «ещё один алиас» стал бы скрытым фолбэком на root-креды.
ENV_ENDPOINT = "PLUGIN_MINIO_ENDPOINT"
ENV_ADMIN_ACCESS_KEY = "PLUGIN_MINIO_ADMIN_ACCESS_KEY"
ENV_ADMIN_SECRET_KEY = "PLUGIN_MINIO_ADMIN_SECRET_KEY"
ENV_PRESIGN_ACCESS_KEY = "PLUGIN_MINIO_PRESIGN_ACCESS_KEY"
ENV_PRESIGN_SECRET_KEY = "PLUGIN_MINIO_PRESIGN_SECRET_KEY"
ENV_PUBLIC_ENDPOINT = "PLUGIN_MINIO_PUBLIC_ENDPOINT"
ENV_STS_ENABLED = "PLUGIN_MINIO_STS_ENABLED"
ENV_REGION = "PLUGIN_MINIO_REGION"

DEFAULT_REGION = "us-east-1"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})

# Идентификаторы as-built, которые НЕЛЬЗЯ использовать как plugin-admin:
# ``agentscale`` — root, ``agentscale-api`` — service user с ``agentscale/*``.
# Явный запрет, а не «надеемся, что родитель не перепутает Secret».
_FORBIDDEN_ADMIN_ACCESS_KEYS = frozenset(
    {"agentscale", "agentscale-api", "root", "admin", "minio", "minioadmin"}
)

_REGION_RE = re.compile(r"^[a-z0-9-]{2,32}$")


class PluginMinioConfigError(BucketUnavailable):
    """Конфигурация отсутствует/некорректна: отказ, а не деградация до root."""

    CODE = "PLUGIN_MINIO_CONFIG_INVALID"
    STATUS = 503
    TITLE = "Plugin MinIO config invalid"
    MESSAGE = "Конфигурация MinIO для объектной capability плагинов неполна или некорректна"


def _fail(detail: str) -> "PluginMinioConfigError":
    return PluginMinioConfigError(detail)


def _required(env: Mapping[str, str], name: str) -> str:
    """Прочитать ОБЯЗАТЕЛЬНУЮ переменную. Пустое значение = её нет."""
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        raise _fail(
            f"{name} не задана; фолбэк на root/service-креды из configmap ЗАПРЕЩЁН"
        )
    return str(raw).strip()


def _parse_bool(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    """Строгий разбор флага. Неизвестное значение — ошибка, а не ``False``."""
    raw = env.get(name)
    if raw is None:
        return default
    lowered = str(raw).strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise _fail(f"{name}={raw!r} не распознано; молча деградировать запрещено")


def _parse_region(env: Mapping[str, str]) -> str:
    raw = env.get(ENV_REGION)
    if raw is None or not str(raw).strip():
        return DEFAULT_REGION
    region = str(raw).strip()
    if not _REGION_RE.fullmatch(region):
        raise _fail(f"{ENV_REGION}={region!r} некорректен")
    return region


def _validate_endpoint(endpoint: str) -> str:
    """Только http(s) endpoint без кредов в URL, path/query/fragment."""
    try:
        parsed = urlsplit(endpoint)
    except ValueError as exc:
        raise _fail(f"{ENV_ENDPOINT} не распарсен: {type(exc).__name__}") from exc
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise _fail(f"{ENV_ENDPOINT} должен быть http(s)://хост[:порт]")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise _fail(f"{ENV_ENDPOINT} не должен содержать креды в URL")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise _fail(f"{ENV_ENDPOINT} не должен содержать path/query/fragment")
    if any(ch.isspace() for ch in endpoint):
        raise _fail(f"{ENV_ENDPOINT} содержит пробельные символы")
    return endpoint.rstrip("/")


def _validate_admin_credentials(access_key: str, secret_key: str, *, kind: str) -> None:
    if access_key.lower() in _FORBIDDEN_ADMIN_ACCESS_KEYS:
        raise _fail(
            f"{kind}: access key {access_key!r} — это root/API-креды as-built; "
            "используйте отдельные service user plugin-object-admin / "
            "plugin-object-presigner"
        )
    for name, value in (
        (kind, access_key),
        (f"{kind} (secret)", secret_key),
    ):
        if any(ch.isspace() for ch in value) or "\x00" in value:
            raise _fail(f"{name} содержит пробельные/NUL-символы")
        if len(value) > 256:
            raise _fail(f"{name} подозрительно длинный")
    if secret_key == access_key:
        raise _fail("секрет не должен совпадать с access key")


@dataclass(frozen=True, slots=True)
class MinioPluginConfig:
    """Снимок конфигурации адаптеров. Секреты скрыты из ``repr``/логов."""

    endpoint: str
    admin_access_key: str = field(repr=False)
    admin_secret_key: str = field(repr=False)
    presign_access_key: str = field(repr=False)
    presign_secret_key: str = field(repr=False)
    public_endpoint: str | None = None
    sts_enabled: bool = False
    region: str = DEFAULT_REGION

    @property
    def presign_endpoint(self) -> str:
        """Endpoint, на который ПОДПИСЫВАЕМ URL (не переписываем Host после).

        Без ``PLUGIN_MINIO_PUBLIC_ENDPOINT`` это in-cluster ``endpoint`` — для
        внешних клиентов тогда обязателен ``proxy=1``. При заданном внешнем
        host подпись сразу идёт на него, иначе SigV4 инвалидируется прокси.
        """
        return self.public_endpoint or self.endpoint

    def __post_init__(self) -> None:
        # Валидируем и при ПРЯМОМ конструировании: обойти ``from_env`` нельзя.
        _validate_endpoint(self.endpoint)
        _validate_admin_credentials(
            self.admin_access_key, self.admin_secret_key, kind=ENV_ADMIN_ACCESS_KEY
        )
        _validate_admin_credentials(
            self.presign_access_key, self.presign_secret_key, kind=ENV_PRESIGN_ACCESS_KEY
        )
        # Ключи ОБЯЗАНЫ быть разными: один principal на администрирование и
        # подпись = та же ошибка, что as-built agentscale-api с agentscale/*.
        if (
            self.admin_access_key == self.presign_access_key
            or self.admin_secret_key == self.presign_secret_key
        ):
            raise _fail(
                "admin и presigner должны быть РАЗНЫМИ principal (вердикт родителя: "
                "иначе presigned URL наследуют слишком широкие права admin)"
            )
        if self.public_endpoint is not None:
            _validate_endpoint(self.public_endpoint)
        if not isinstance(self.sts_enabled, bool):
            raise _fail("sts_enabled должен быть bool")
        if not isinstance(self.region, str) or not _REGION_RE.fullmatch(self.region):
            raise _fail("region некорректен")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MinioPluginConfig":
        """Построить конфигурацию из окружения (fail-closed).

        ``env=None`` → ``os.environ``. Передача явного mapping нужна тестам и
        делает конфигурацию детерминированной. НИ ОДНА другая переменная не
        читается: ``MINIO_ROOT_USER``, ``MINIO_ROOT_PASSWORD``, ``S3_*``,
        ``S3_BUCKET`` намеренно игнорируются.
        """
        source: Mapping[str, str] = os.environ if env is None else env
        endpoint = _validate_endpoint(_required(source, ENV_ENDPOINT))
        admin_key = _required(source, ENV_ADMIN_ACCESS_KEY)
        admin_secret = _required(source, ENV_ADMIN_SECRET_KEY)
        _validate_admin_credentials(admin_key, admin_secret, kind=ENV_ADMIN_ACCESS_KEY)
        presign_key = _required(source, ENV_PRESIGN_ACCESS_KEY)
        presign_secret = _required(source, ENV_PRESIGN_SECRET_KEY)
        _validate_admin_credentials(
            presign_key, presign_secret, kind=ENV_PRESIGN_ACCESS_KEY
        )
        # Внешний endpoint НЕ обязателен: без него in-cluster подпись + proxy=1.
        public_raw = source.get(ENV_PUBLIC_ENDPOINT)
        public_endpoint = (
            _validate_endpoint(str(public_raw).strip())
            if public_raw is not None and str(public_raw).strip()
            else None
        )
        return cls(
            endpoint=endpoint,
            admin_access_key=admin_key,
            admin_secret_key=admin_secret,
            presign_access_key=presign_key,
            presign_secret_key=presign_secret,
            public_endpoint=public_endpoint,
            sts_enabled=_parse_bool(source, ENV_STS_ENABLED, default=False),
            region=_parse_region(source),
        )


__all__ = [
    "ENV_ENDPOINT",
    "ENV_ADMIN_ACCESS_KEY",
    "ENV_ADMIN_SECRET_KEY",
    "ENV_PRESIGN_ACCESS_KEY",
    "ENV_PRESIGN_SECRET_KEY",
    "ENV_PUBLIC_ENDPOINT",
    "ENV_STS_ENABLED",
    "ENV_REGION",
    "DEFAULT_REGION",
    "PluginMinioConfigError",
    "MinioPluginConfig",
]
