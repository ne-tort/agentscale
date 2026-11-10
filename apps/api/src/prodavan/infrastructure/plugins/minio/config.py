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
  * PLUGIN_MINIO_ADMIN_ACCESS_KEY_REF / ADMIN_SECRET_KEY_REF — SecretStore refs;
  * PLUGIN_MINIO_PRESIGN_ACCESS_KEY_REF / PRESIGN_SECRET_KEY_REF — refs подписанта;
  * PLUGIN_MINIO_STS_ENABLED — typed opt-in, "false" по умолчанию;
  * PLUGIN_MINIO_STS_ENDPOINT / STS_SESSION_DURATION_SECONDS / STS_ROLE_ARN — STS limits;
  * PLUGIN_MINIO_STS_PROBE_ENABLED — async lifespan probe opt-in, "false" по умолчанию;
  * PLUGIN_MINIO_STS_PROBE_TIMEOUT_SECONDS / REVALIDATE_SECONDS / RETRY_SECONDS — probe bounds;
  * PLUGIN_MINIO_STS_PROBE_BUCKET / KEY — dedicated pre-provisioned bucket + zero-byte fixed object;
  * PLUGIN_MINIO_CHECKSUM_MAX_BYTES — hard cap fallback streaming GET;
  * PLUGIN_MINIO_REGION — "us-east-1" по умолчанию.
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
from typing import Any, Mapping
from urllib.parse import urlsplit

from prodavan.application.plugins.objects import policy as iam
from prodavan.application.plugins.objects.errors import BucketUnavailable

# Имена переменных зафиксированы здесь и больше нигде не перечисляются: случайный
# «ещё один алиас» стал бы скрытым фолбэком на root-креды.
ENV_ENDPOINT = "PLUGIN_MINIO_ENDPOINT"
ENV_ADMIN_ACCESS_KEY = "PLUGIN_MINIO_ADMIN_ACCESS_KEY"
ENV_ADMIN_SECRET_KEY = "PLUGIN_MINIO_ADMIN_SECRET_KEY"
ENV_PRESIGN_ACCESS_KEY = "PLUGIN_MINIO_PRESIGN_ACCESS_KEY"
ENV_PRESIGN_SECRET_KEY = "PLUGIN_MINIO_PRESIGN_SECRET_KEY"
ENV_ADMIN_ACCESS_KEY_REF = "PLUGIN_MINIO_ADMIN_ACCESS_KEY_REF"
ENV_ADMIN_SECRET_KEY_REF = "PLUGIN_MINIO_ADMIN_SECRET_KEY_REF"
ENV_PRESIGN_ACCESS_KEY_REF = "PLUGIN_MINIO_PRESIGN_ACCESS_KEY_REF"
ENV_PRESIGN_SECRET_KEY_REF = "PLUGIN_MINIO_PRESIGN_SECRET_KEY_REF"
ENV_PUBLIC_ENDPOINT = "PLUGIN_MINIO_PUBLIC_ENDPOINT"
ENV_STS_ENABLED = "PLUGIN_MINIO_STS_ENABLED"
ENV_STS_ENDPOINT = "PLUGIN_MINIO_STS_ENDPOINT"
ENV_STS_SESSION_DURATION_SECONDS = "PLUGIN_MINIO_STS_SESSION_DURATION_SECONDS"
ENV_STS_ROLE_ARN = "PLUGIN_MINIO_STS_ROLE_ARN"
ENV_STS_PROBE_ENABLED = "PLUGIN_MINIO_STS_PROBE_ENABLED"
ENV_STS_PROBE_TIMEOUT_SECONDS = "PLUGIN_MINIO_STS_PROBE_TIMEOUT_SECONDS"
ENV_STS_PROBE_REVALIDATE_SECONDS = "PLUGIN_MINIO_STS_PROBE_REVALIDATE_SECONDS"
ENV_STS_PROBE_RETRY_SECONDS = "PLUGIN_MINIO_STS_PROBE_RETRY_SECONDS"
ENV_STS_PROBE_BUCKET = "PLUGIN_MINIO_STS_PROBE_BUCKET"
ENV_STS_PROBE_KEY = "PLUGIN_MINIO_STS_PROBE_KEY"
ENV_CHECKSUM_MAX_BYTES = "PLUGIN_MINIO_CHECKSUM_MAX_BYTES"
ENV_REGION = "PLUGIN_MINIO_REGION"

DEFAULT_REGION = "us-east-1"
DEFAULT_STS_SESSION_DURATION_SECONDS = 900
DEFAULT_STS_ROLE_ARN = "arn:aws:iam::000000000000:role/plugin-object-access"
DEFAULT_STS_PROBE_TIMEOUT_SECONDS = 5
DEFAULT_STS_PROBE_REVALIDATE_SECONDS = 300
DEFAULT_STS_PROBE_RETRY_SECONDS = 30
DEFAULT_STS_PROBE_BUCKET = "tenant-sts-probe"
DEFAULT_STS_PROBE_KEY = "module-sts-probe/__authorization_canary__"
DEFAULT_CHECKSUM_MAX_BYTES = 64 * 1024 * 1024
MAX_CHECKSUM_MAX_BYTES = 1024 * 1024 * 1024

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})

# Идентификаторы as-built, которые НЕЛЬЗЯ использовать как plugin-admin:
# ``agentscale`` — root, ``agentscale-api`` — service user с ``agentscale/*``.
# Явный запрет, а не «надеемся, что родитель не перепутает Secret».
_FORBIDDEN_ADMIN_ACCESS_KEYS = frozenset(
    {"agentscale", "agentscale-api", "root", "admin", "minio", "minioadmin"}
)

_REGION_RE = re.compile(r"^[a-z0-9-]{2,32}$")
_STS_ROLE_ARN_RE = re.compile(
    r"^arn:aws:iam::[0-9]{12}:role/plugin-object-access$"
)


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


def _parse_bounded_int(
    env: Mapping[str, str], name: str, *, default: int, minimum: int, maximum: int
) -> int:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise _fail(f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise _fail(f"{name} must be within {minimum}..{maximum}")
    return value


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


def _validate_probe_bucket(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("tenant-") or value != value.strip():
        raise _fail("sts_probe_bucket must be a canonical dedicated tenant-* bucket")
    try:
        expected = iam.bucket_name(value[len("tenant-"):])
    except Exception:
        raise _fail("sts_probe_bucket must be a canonical dedicated tenant-* bucket") from None
    if value != expected:
        raise _fail("sts_probe_bucket must be a canonical dedicated tenant-* bucket")
    return value


def _validate_probe_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("module-")
        or value != value.strip()
        or any(char in value for char in ("\\", "*", "?", "..", "\x00", "\n", "\r"))
        or "//" in value
        or value.endswith("/")
    ):
        raise _fail("sts_probe_key must be one exact safe module-<plugin>/ object key")
    prefix, separator, tail = value.partition("/")
    if not separator or not tail or "/" in tail:
        raise _fail("sts_probe_key must contain one fixed object name")
    # partition() strips the slash, so do not slice off another character here.
    plugin_id = prefix[len("module-") :]
    try:
        expected = f"{iam.module_prefix(plugin_id)}{tail}"
    except Exception:
        raise _fail("sts_probe_key must have a canonical module-<plugin>/ prefix") from None
    if value != expected:
        raise _fail("sts_probe_key must have a canonical module-<plugin>/ prefix")
    return value


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
    sts_endpoint: str | None = None
    sts_session_duration_seconds: int = DEFAULT_STS_SESSION_DURATION_SECONDS
    sts_role_arn: str = DEFAULT_STS_ROLE_ARN
    sts_probe_enabled: bool = False
    sts_probe_timeout_seconds: int = DEFAULT_STS_PROBE_TIMEOUT_SECONDS
    sts_probe_revalidate_seconds: int = DEFAULT_STS_PROBE_REVALIDATE_SECONDS
    sts_probe_retry_seconds: int = DEFAULT_STS_PROBE_RETRY_SECONDS
    sts_probe_bucket: str = DEFAULT_STS_PROBE_BUCKET
    sts_probe_key: str = DEFAULT_STS_PROBE_KEY
    checksum_max_bytes: int = DEFAULT_CHECKSUM_MAX_BYTES
    region: str = DEFAULT_REGION

    @property
    def sts_endpoint_url(self) -> str:
        """STS endpoint is explicit when configured; otherwise the MinIO endpoint."""
        return self.sts_endpoint or self.endpoint

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
        if self.sts_endpoint is not None:
            _validate_endpoint(self.sts_endpoint)
        if (
            not isinstance(self.sts_session_duration_seconds, int)
            or isinstance(self.sts_session_duration_seconds, bool)
            or not 900 <= self.sts_session_duration_seconds <= 3600
        ):
            raise _fail("sts_session_duration_seconds must be within 900..3600")
        if not isinstance(self.sts_role_arn, str) or not _STS_ROLE_ARN_RE.fullmatch(
            self.sts_role_arn
        ):
            raise _fail("sts_role_arn must name only role/plugin-object-access")
        if not isinstance(self.sts_probe_enabled, bool):
            raise _fail("sts_probe_enabled must be bool")
        _validate_probe_bucket(self.sts_probe_bucket)
        _validate_probe_key(self.sts_probe_key)
        for name, value, minimum, maximum in (
            ("sts_probe_timeout_seconds", self.sts_probe_timeout_seconds, 1, 30),
            ("sts_probe_revalidate_seconds", self.sts_probe_revalidate_seconds, 1, 3600),
            ("sts_probe_retry_seconds", self.sts_probe_retry_seconds, 1, 300),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                raise _fail(f"{name} must be within {minimum}..{maximum}")
        if (
            not isinstance(self.checksum_max_bytes, int)
            or isinstance(self.checksum_max_bytes, bool)
            or not 1 <= self.checksum_max_bytes <= MAX_CHECKSUM_MAX_BYTES
        ):
            raise _fail("checksum_max_bytes is outside the typed streaming cap")
        if not isinstance(self.region, str) or not _REGION_RE.fullmatch(self.region):
            raise _fail("region некорректен")

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, secret_store: Any
    ) -> "MinioPluginConfig":
        """Build from env refs only; plaintext credential variables are forbidden."""
        return cls.from_secret_refs(secret_store=secret_store, env=env)

    @classmethod
    def from_secret_refs(
        cls,
        *,
        secret_store: Any,
        env: Mapping[str, str] | None = None,
        settings: Any = None,
    ) -> "MinioPluginConfig":
        """Resolve each credential through the one supplied SecretStore.

        Typed settings disable raw-env fallback completely. Admin/presign
        plaintext values are never accepted.
        """
        if settings is not None and env is not None:
            raise _fail("pass either typed settings or env, not both")
        if settings is not None:
            fields = {
                ENV_ENDPOINT: "plugin_minio_endpoint",
                ENV_ADMIN_ACCESS_KEY_REF: "plugin_minio_admin_access_key_ref",
                ENV_ADMIN_SECRET_KEY_REF: "plugin_minio_admin_secret_key_ref",
                ENV_PRESIGN_ACCESS_KEY_REF: "plugin_minio_presign_access_key_ref",
                ENV_PRESIGN_SECRET_KEY_REF: "plugin_minio_presign_secret_key_ref",
                ENV_PUBLIC_ENDPOINT: "plugin_minio_public_endpoint",
                ENV_STS_ENABLED: "plugin_minio_sts_enabled",
                ENV_STS_ENDPOINT: "plugin_minio_sts_endpoint",
                ENV_STS_SESSION_DURATION_SECONDS: (
                    "plugin_minio_sts_session_duration_seconds"
                ),
                ENV_STS_ROLE_ARN: "plugin_minio_sts_role_arn",
                ENV_STS_PROBE_ENABLED: "plugin_minio_sts_probe_enabled",
                ENV_STS_PROBE_TIMEOUT_SECONDS: "plugin_minio_sts_probe_timeout_seconds",
                ENV_STS_PROBE_REVALIDATE_SECONDS: "plugin_minio_sts_probe_revalidate_seconds",
                ENV_STS_PROBE_RETRY_SECONDS: "plugin_minio_sts_probe_retry_seconds",
                ENV_STS_PROBE_BUCKET: "plugin_minio_sts_probe_bucket",
                ENV_STS_PROBE_KEY: "plugin_minio_sts_probe_key",
                ENV_CHECKSUM_MAX_BYTES: "plugin_minio_checksum_max_bytes",
                ENV_REGION: "plugin_minio_region",
            }
            source: Mapping[str, str] = {
                name: (
                    ""
                    if getattr(settings, attribute, "") is None
                    else str(getattr(settings, attribute, ""))
                )
                for name, attribute in fields.items()
            }
        else:
            source = os.environ if env is None else env
        if secret_store is None or not callable(getattr(secret_store, "get", None)):
            raise _fail("a real SecretStore is required for credential references")
        for plaintext_name in (
            ENV_ADMIN_ACCESS_KEY,
            ENV_ADMIN_SECRET_KEY,
            ENV_PRESIGN_ACCESS_KEY,
            ENV_PRESIGN_SECRET_KEY,
        ):
            if str(source.get(plaintext_name, "") or "").strip():
                raise _fail(
                    f"{plaintext_name} plaintext is forbidden; use its *_REF variable"
                )

        def resolve(name: str) -> str:
            ref = _required(source, name)
            if (
                not ref.startswith(("vault://", "file://", "k8s-secret://"))
                or any(char.isspace() or char == "\x00" for char in ref)
                or len(ref) > 2048
            ):
                raise _fail(f"{name} must be a canonical SecretStore reference")
            try:
                value = secret_store.get(ref)
            except Exception as exc:
                raise _fail(f"{name} could not be resolved ({type(exc).__name__})") from None
            if not isinstance(value, str) or not value:
                raise _fail(f"{name} resolved an empty/non-string credential")
            return value

        endpoint = _validate_endpoint(_required(source, ENV_ENDPOINT))
        admin_key = resolve(ENV_ADMIN_ACCESS_KEY_REF)
        admin_secret = resolve(ENV_ADMIN_SECRET_KEY_REF)
        presign_key = resolve(ENV_PRESIGN_ACCESS_KEY_REF)
        presign_secret = resolve(ENV_PRESIGN_SECRET_KEY_REF)
        public_raw = source.get(ENV_PUBLIC_ENDPOINT)
        public_endpoint = (
            _validate_endpoint(str(public_raw).strip())
            if public_raw is not None and str(public_raw).strip()
            else None
        )
        sts_raw = source.get(ENV_STS_ENDPOINT)
        sts_endpoint = (
            _validate_endpoint(str(sts_raw).strip())
            if sts_raw is not None and str(sts_raw).strip()
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
            sts_endpoint=sts_endpoint,
            sts_session_duration_seconds=_parse_bounded_int(
                source,
                ENV_STS_SESSION_DURATION_SECONDS,
                default=DEFAULT_STS_SESSION_DURATION_SECONDS,
                minimum=900,
                maximum=3600,
            ),
            sts_role_arn=str(source.get(ENV_STS_ROLE_ARN) or DEFAULT_STS_ROLE_ARN).strip(),
            sts_probe_enabled=_parse_bool(source, ENV_STS_PROBE_ENABLED, default=False),
            sts_probe_timeout_seconds=_parse_bounded_int(
                source, ENV_STS_PROBE_TIMEOUT_SECONDS,
                default=DEFAULT_STS_PROBE_TIMEOUT_SECONDS, minimum=1, maximum=30,
            ),
            sts_probe_revalidate_seconds=_parse_bounded_int(
                source, ENV_STS_PROBE_REVALIDATE_SECONDS,
                default=DEFAULT_STS_PROBE_REVALIDATE_SECONDS, minimum=1, maximum=3600,
            ),
            sts_probe_retry_seconds=_parse_bounded_int(
                source, ENV_STS_PROBE_RETRY_SECONDS,
                default=DEFAULT_STS_PROBE_RETRY_SECONDS, minimum=1, maximum=300,
            ),
            sts_probe_bucket=str(
                source.get(ENV_STS_PROBE_BUCKET) or DEFAULT_STS_PROBE_BUCKET
            ).strip(),
            sts_probe_key=str(source.get(ENV_STS_PROBE_KEY) or DEFAULT_STS_PROBE_KEY).strip(),
            checksum_max_bytes=_parse_bounded_int(
                source,
                ENV_CHECKSUM_MAX_BYTES,
                default=DEFAULT_CHECKSUM_MAX_BYTES,
                minimum=1,
                maximum=MAX_CHECKSUM_MAX_BYTES,
            ),
            region=_parse_region(source),
        )


__all__ = [
    "ENV_ENDPOINT",
    "ENV_ADMIN_ACCESS_KEY",
    "ENV_ADMIN_SECRET_KEY",
    "ENV_PRESIGN_ACCESS_KEY",
    "ENV_PRESIGN_SECRET_KEY",
    "ENV_ADMIN_ACCESS_KEY_REF",
    "ENV_ADMIN_SECRET_KEY_REF",
    "ENV_PRESIGN_ACCESS_KEY_REF",
    "ENV_PRESIGN_SECRET_KEY_REF",
    "ENV_PUBLIC_ENDPOINT",
    "ENV_STS_ENABLED",
    "ENV_STS_ENDPOINT",
    "ENV_STS_SESSION_DURATION_SECONDS",
    "ENV_STS_ROLE_ARN",
    "ENV_STS_PROBE_ENABLED",
    "ENV_STS_PROBE_TIMEOUT_SECONDS",
    "ENV_STS_PROBE_REVALIDATE_SECONDS",
    "ENV_STS_PROBE_RETRY_SECONDS",
    "ENV_STS_PROBE_BUCKET",
    "ENV_STS_PROBE_KEY",
    "ENV_CHECKSUM_MAX_BYTES",
    "ENV_REGION",
    "DEFAULT_REGION",
    "DEFAULT_STS_SESSION_DURATION_SECONDS",
    "DEFAULT_STS_ROLE_ARN",
    "DEFAULT_STS_PROBE_TIMEOUT_SECONDS",
    "DEFAULT_STS_PROBE_REVALIDATE_SECONDS",
    "DEFAULT_STS_PROBE_RETRY_SECONDS",
    "DEFAULT_STS_PROBE_BUCKET",
    "DEFAULT_STS_PROBE_KEY",
    "DEFAULT_CHECKSUM_MAX_BYTES",
    "MAX_CHECKSUM_MAX_BYTES",
    "PluginMinioConfigError",
    "MinioPluginConfig",
]
