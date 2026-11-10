"""Конфигурация сборки plugin-платформы (F11, единая точка wiring).

``PluginPlatformConfig`` — неизменяемый снимок всех ``PLUGIN_*`` полей
``prodavan.config.settings.Settings`` (задача A3 родителя). Слой сборки НЕ
читает ``settings`` внутри сервисов: он один раз проецирует настройки сюда и
дальше работает только с этим dataclass. Это позволяет тестам подменять
конфиг целиком, не трогая глобальный ``settings``.

Дублирование guard'ов (§0.5 плана, fail-closed)
------------------------------------------------
В ``Settings._guard_non_dev_flags`` уже есть проверки «включено => есть
секрет подписи / static-origin», «режим egress из whitelist», «TTL в 60..900».
Они **продублированы** здесь намеренно: платформа обязана отказаться
собираться, даже если её вызвали с кастомным/фейковым объектом настроек
(например, в тесте или при частичной конфигурации), где guard не отработал.
Отсутствие секрета при enabled=True = ``PluginPlatformConfigError``, а не
«возьмём что-нибудь из AUTH_TEST_SECRET» — у плагинов отдельный trust-контур
(§1, §4.1 v4, критерий №17).

``enabled=False`` -> ``build_plugin_platform`` возвращает ``None`` и НЕ создаёт
ни одного сервиса; роутеры в этом случае отдают 503 (см. ``state.py``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from typing import Any, Final, Mapping

from prodavan.domain.plugins.origins import PluginOriginError, exact_plugin_origins

# Все PLUGIN_* поля Settings (config/settings.py:112-138). Список фиксирован:
# новое поле настроек должно быть явно добавлено сюда, иначе сборка о нём
# «забудет» молча — это был бы тихий конфигурационный дрейф.
PLUGIN_SETTING_FIELDS: Final[tuple[str, ...]] = (
    "plugin_platform_enabled",
    "plugin_claims_secret",
    "plugin_event_signing_secret_ref",
    "plugin_claims_ttl_seconds",
    "plugin_claims_issuer",
    "plugin_claims_audience",
    "plugin_static_origin",
    "plugin_shell_origin",
    "plugin_tenant_db_admin_url",
    "plugin_tenant_db_host",
    "plugin_tenant_db_port",
    "plugin_egress_mode",
    "plugin_egress_approved",
    "plugin_idle_ttl_seconds",
    "plugin_ready_timeout_seconds",
    "plugin_max_concurrent_wakes",
    "plugin_oci_registry",
    "plugin_cosign_identity",
    "plugin_cosign_issuer",
    "plugin_min_platform_version",
)

# Допустимые режимы egress (§6, guard в settings.py:688).
ALLOWED_EGRESS_MODES: Final[frozenset[str]] = frozenset(
    {"none", "webhooks-only", "full"}
)

# Границы TTL короткого PluginClaims (§4.2, guard в settings.py:697).
MIN_CLAIMS_TTL_SECONDS: Final[int] = 60
MAX_CLAIMS_TTL_SECONDS: Final[int] = 900

# Env для ротации ключей подписи. Не является полем Settings: родитель НЕ
# добавлял его в config/settings.py, поэтому читаем окружение напрямую.
# Формат: список секретов через запятую (старые ключи, от самого старого к
# более новому). См. keyring.build_keyring.
PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV: Final[str] = "PLUGIN_CLAIMS_PREVIOUS_SECRETS"
PLUGIN_EVENT_SIGNING_SECRET_REF_SETTING: Final[str] = "PLUGIN_EVENT_SIGNING_SECRET_REF"


class PluginPlatformConfigError(RuntimeError):
    """Ошибка конфигурации/валидации сборки платформы (fail-closed).

    Наследник ``RuntimeError``: это ошибка композиции на старте приложения,
    а не штатный отказ запроса. Родитель должен НЕ подключать платформу и
    уронить startup, а не «продолжить без плагинов» при enabled=True.
    """


@dataclass(frozen=True, slots=True)
class PluginPlatformConfig:
    """Полный снимок ``PLUGIN_*`` настроек для одной сборки платформы.

    ``previous_claims_secrets`` — список секретов для верификации in-flight
    токенов после ротации (только проверка, НЕ подпись). Попадает сюда из env,
    чтобы ``build_keyring`` не дёргал ``os.environ`` сам.
    """

    enabled: bool = False
    claims_secret: str = ""
    event_signing_secret_ref: str = ""
    previous_claims_secrets: tuple[str, ...] = ()
    claims_ttl_seconds: int = 300
    claims_issuer: str = "agentscale-plugins"
    claims_audience: str = "capability-gateway"
    static_origin: str = ""
    shell_origin: str = ""
    tenant_db_admin_url: str = ""
    tenant_db_host: str = "tenant-postgres"
    tenant_db_port: int = 5432
    egress_mode: str = "none"
    egress_approved: bool = False
    idle_ttl_seconds: int = 300
    ready_timeout_seconds: int = 120
    max_concurrent_wakes: int = 2
    oci_registry: str = "ghcr.io/ne-tort/plugins"
    cosign_identity: str = ""
    cosign_issuer: str = "https://token.actions.githubusercontent.com"
    min_platform_version: str = "4.0.0"

    # ------------------------------------------------------------------ сборка
    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "PluginPlatformConfig":
        """Спроецировать ``Settings`` (или любой structural fake) в конфиг.

        Читаются ТОЛЬКО поля из ``PLUGIN_SETTING_FIELDS``: слой wiring не знает
        ни про БД, ни про auth-секреты агентского контура. Отсутствующее поле
        = ошибка конфигурации, а не дефолт «на всякий случай» (fail-closed).

        ``environ`` — явная подмена окружения для детерминированных тестов;
        по умолчанию читается ``os.environ`` (переменная
        ``PLUGIN_CLAIMS_PREVIOUS_SECRETS`` не является полем ``Settings``).
        """
        environment = os.environ if environ is None else environ
        values: dict[str, Any] = {}
        for name in PLUGIN_SETTING_FIELDS:
            if not hasattr(settings, name):
                raise PluginPlatformConfigError(
                    f"settings is missing required PLUGIN_* field {name!r}; "
                    "refusing to silently fall back to defaults"
                )
            values[name] = getattr(settings, name)

        config = cls(
            enabled=bool(values["plugin_platform_enabled"]),
            claims_secret=str(values["plugin_claims_secret"] or ""),
            event_signing_secret_ref=str(values["plugin_event_signing_secret_ref"] or ""),
            previous_claims_secrets=read_previous_secrets(
                environment.get(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, "")
            ),
            claims_ttl_seconds=int(values["plugin_claims_ttl_seconds"]),
            claims_issuer=str(values["plugin_claims_issuer"]),
            claims_audience=str(values["plugin_claims_audience"]),
            static_origin=str(values["plugin_static_origin"]),
            shell_origin=str(values["plugin_shell_origin"]),
            tenant_db_admin_url=str(values["plugin_tenant_db_admin_url"] or ""),
            tenant_db_host=str(values["plugin_tenant_db_host"]),
            tenant_db_port=int(values["plugin_tenant_db_port"]),
            egress_mode=str(values["plugin_egress_mode"]),
            egress_approved=bool(values["plugin_egress_approved"]),
            idle_ttl_seconds=int(values["plugin_idle_ttl_seconds"]),
            ready_timeout_seconds=int(values["plugin_ready_timeout_seconds"]),
            max_concurrent_wakes=int(values["plugin_max_concurrent_wakes"]),
            oci_registry=str(values["plugin_oci_registry"]),
            cosign_identity=str(values["plugin_cosign_identity"] or ""),
            cosign_issuer=str(values["plugin_cosign_issuer"]),
            min_platform_version=str(values["plugin_min_platform_version"]),
        )
        config.validate()
        return config

    def with_overrides(self, **changes: Any) -> "PluginPlatformConfig":
        """Вернуть копию с изменёнными полями (для тестов/инъекции портов).

        Неизвестные поля отсекаются ``dataclasses.replace`` (``TypeError``) —
        опечатка в имени override не превратится в тихо проигнорированный
        параметр. Валидация выполняется повторно.
        """
        known = {item.name for item in fields(self)}
        unknown = sorted(set(changes) - known)
        if unknown:
            raise PluginPlatformConfigError(
                f"unknown PluginPlatformConfig override(s): {', '.join(unknown)}"
            )
        updated = replace(self, **changes)
        updated.validate()
        return updated

    # --------------------------------------------------------------- валидация
    def validate(self) -> None:
        """Продублировать guard'ы ``Settings`` на уровне сборки.

        При ``enabled=False`` валидируются только «всегда безопасные» инварианты
        (режим egress, TTL, порт, идентификаторы): платформа выключена, но
        некорректная конфигурация всё равно не должна собираться, если родитель
        позже включит флаг без перезапуска. Требования к секрету и origin
        проверяются только для включённой платформы — ровно как в settings.
        """
        if self.claims_ttl_seconds < MIN_CLAIMS_TTL_SECONDS or self.claims_ttl_seconds > MAX_CLAIMS_TTL_SECONDS:
            raise PluginPlatformConfigError(
                f"PLUGIN_CLAIMS_TTL_SECONDS must be within "
                f"{MIN_CLAIMS_TTL_SECONDS}..{MAX_CLAIMS_TTL_SECONDS}, "
                f"got {self.claims_ttl_seconds!r}"
            )
        if self.egress_mode not in ALLOWED_EGRESS_MODES:
            raise PluginPlatformConfigError(
                f"PLUGIN_EGRESS_MODE must be one of {sorted(ALLOWED_EGRESS_MODES)}, "
                f"got {self.egress_mode!r}"
            )
        if self.egress_mode == "full" and not self.egress_approved:
            raise PluginPlatformConfigError(
                "PLUGIN_EGRESS_MODE=full requires PLUGIN_EGRESS_APPROVED=true "
                "(явное разрешение прямого выхода в интернет)"
            )
        if not (1 <= self.tenant_db_port <= 65535):
            raise PluginPlatformConfigError(
                f"PLUGIN_TENANT_DB_PORT must be 1..65535, got {self.tenant_db_port!r}"
            )
        if self.idle_ttl_seconds < 1 or self.ready_timeout_seconds < 1 or self.max_concurrent_wakes < 1:
            raise PluginPlatformConfigError(
                "plugin idle/ready/wake limits must be positive integers"
            )
        # Пустые идентификаторы контура = потом не соберётся mint/verify/audit.
        for name in (
            "claims_issuer",
            "claims_audience",
            "tenant_db_host",
            "oci_registry",
            "cosign_issuer",
            "min_platform_version",
        ):
            if not str(getattr(self, name) or "").strip():
                raise PluginPlatformConfigError(f"{name} must not be empty")

        if self.cosign_identity.strip() and self.cosign_identity == self.claims_secret.strip():
            # Секрет подписи JWT не должен случайно оказаться OCI identity:
            # это разные trust-контуры, и в логах/манифестах identity виден.
            raise PluginPlatformConfigError(
                "PLUGIN_COSIGN_IDENTITY must not equal PLUGIN_CLAIMS_SECRET"
            )

        validate_event_signing_secret_ref(
            self.event_signing_secret_ref, required=False
        )

        if not self.enabled:
            # Выключено: сборка не создаёт сервисы, секрет/origin не нужны.
            return

        if not self.claims_secret.strip():
            # КЛЮЧЕВОЕ: никаких dev-фолбэков на AUTH_TEST_SECRET /
            # pod_identity_bridge_secret. Это ДРУГОЙ trust-контур (§4.1 v4).
            raise PluginPlatformConfigError(
                "PLUGIN_PLATFORM_ENABLED=true requires PLUGIN_CLAIMS_SECRET; "
                "dev-фолбэки на AUTH_TEST_SECRET запрещены (другой trust-контур)"
            )
        if not self.static_origin.strip():
            raise PluginPlatformConfigError(
                "PLUGIN_PLATFORM_ENABLED=true requires PLUGIN_STATIC_ORIGIN "
                "(отдельный origin для iframe, не agentscale.dev)"
            )
        if not self.shell_origin.strip():
            raise PluginPlatformConfigError(
                "PLUGIN_PLATFORM_ENABLED=true requires PLUGIN_SHELL_ORIGIN "
                "(exact server-derived HTTPS shell origin)"
            )
        try:
            exact_plugin_origins(
                static_origin=self.static_origin,
                shell_origin=self.shell_origin,
            )
        except PluginOriginError as exc:
            raise PluginPlatformConfigError(str(exc)) from exc
        if not self.cosign_identity.strip():
            # Keyless cosign: без --certificate-identity сборка verifier'а
            # возможна, но каждый артефакт ушёл бы в карантин. Честнее упасть
            # на старте (settings.py такого guard'а не имеет — это дубль F11).
            raise PluginPlatformConfigError(
                "PLUGIN_PLATFORM_ENABLED=true requires PLUGIN_COSIGN_IDENTITY "
                "(keyless cosign --certificate-identity)"
            )
        validate_event_signing_secret_ref(
            self.event_signing_secret_ref, required=True
        )
        for index, secret in enumerate(self.previous_claims_secrets):
            if not secret.strip():
                raise PluginPlatformConfigError(
                    f"PLUGIN_CLAIMS_PREVIOUS_SECRETS[{index}] is empty; "
                    "refusing to register an empty verification key"
                )
        if self.claims_secret in self.previous_claims_secrets:
            raise PluginPlatformConfigError(
                "active PLUGIN_CLAIMS_SECRET must not be listed in "
                "PLUGIN_CLAIMS_PREVIOUS_SECRETS (duplicate kid)"
            )

    # ----------------------------------------------------------------- отчёт
    def as_report(self) -> Mapping[str, Any]:
        """Безопасная проекция для логов/диагностики (БЕЗ секретов).

        Секреты, admin URL и cosign identity в отчёт NEVER не попадают:
        аудит/логи не должны содержать plaintext (§4.5).
        """
        return {
            "enabled": self.enabled,
            "claims_secret_configured": bool(self.claims_secret.strip()),
            "event_signing_secret_configured": bool(self.event_signing_secret_ref.strip()),
            "previous_keys_count": len(self.previous_claims_secrets),
            "claims_ttl_seconds": self.claims_ttl_seconds,
            "claims_issuer": self.claims_issuer,
            "claims_audience": self.claims_audience,
            "static_origin": self.static_origin,
            "shell_origin": self.shell_origin,
            "tenant_db_host": self.tenant_db_host,
            "tenant_db_port": self.tenant_db_port,
            "tenant_db_admin_url_configured": bool(self.tenant_db_admin_url.strip()),
            "egress_mode": self.egress_mode,
            "egress_approved": self.egress_approved,
            "idle_ttl_seconds": self.idle_ttl_seconds,
            "ready_timeout_seconds": self.ready_timeout_seconds,
            "max_concurrent_wakes": self.max_concurrent_wakes,
            "oci_registry": self.oci_registry,
            "cosign_identity_configured": bool(self.cosign_identity.strip()),
            "cosign_issuer": self.cosign_issuer,
            "min_platform_version": self.min_platform_version,
        }


def validate_event_signing_secret_ref(value: Any, *, required: bool) -> str:
    """Validate an opaque event-key reference, never a plaintext fallback."""

    if not isinstance(value, str) or value != value.strip() or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        raise PluginPlatformConfigError(
            "PLUGIN_EVENT_SIGNING_SECRET_REF must be a trimmed SecretStore reference"
        )
    if not value:
        if required:
            raise PluginPlatformConfigError(
                "PLUGIN_PLATFORM_ENABLED=true requires PLUGIN_EVENT_SIGNING_SECRET_REF; "
                "PLUGIN_CLAIMS_SECRET and AUTH_TEST_SECRET are not fallbacks"
            )
        return ""
    if len(value) > 2048:
        raise PluginPlatformConfigError("PLUGIN_EVENT_SIGNING_SECRET_REF is too long")
    if value.startswith("k8s-secret://"):
        parts = value[len("k8s-secret://"):].split("/")
        name, key = parts if len(parts) == 2 else ("", "")
        if (
            len(name) > 253
            or len(key) > 253
            or not name
            or not key
            or any(
                not segment
                or len(segment) > 63
                or not segment[0].isalnum()
                or not segment[-1].isalnum()
                or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in segment)
                for segment in name.split(".")
            )
            or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in key)
            or key in {".", ".."}
        ):
            raise PluginPlatformConfigError(
                "PLUGIN_EVENT_SIGNING_SECRET_REF must be k8s-secret://<secret-name>/<key>"
            )
        return value
    if value.startswith(("vault://", "file://")) and value.split("://", 1)[1]:
        return value
    raise PluginPlatformConfigError(
        "PLUGIN_EVENT_SIGNING_SECRET_REF must use vault://, file://, or "
        "k8s-secret://<secret-name>/<key>; plaintext secrets are forbidden"
    )


def read_previous_secrets(raw: str | None) -> tuple[str, ...]:
    """Разобрать ``PLUGIN_CLAIMS_PREVIOUS_SECRETS`` (секреты через запятую).

    Пустые элементы отбрасываются, пробелы по краям снимаются. Порядок
    сохраняется: от самого старого ключа к более новому — ``build_keyring``
    сделает последним активный ключ. Никаких значений по умолчанию.
    """
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


__all__ = [
    "ALLOWED_EGRESS_MODES",
    "MAX_CLAIMS_TTL_SECONDS",
    "MIN_CLAIMS_TTL_SECONDS",
    "PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV",
    "PLUGIN_EVENT_SIGNING_SECRET_REF_SETTING",
    "PLUGIN_SETTING_FIELDS",
    "PluginPlatformConfig",
    "PluginPlatformConfigError",
    "read_previous_secrets",
    "validate_event_signing_secret_ref",
]
