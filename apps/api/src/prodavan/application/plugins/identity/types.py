"""Identity-контракт плагинов: ``PluginClaims`` и ошибки trust-контура.

Это **отдельный trust-контур**, а не ``PodBridgeClaims`` (см.
``docs/ARCHITECTURE/plugin-architecture-complete.md`` v4, §1 / §4.1 / §4.2):

* у агентского Bridge JWT ``sub=pod:{id}``, ``require_project()``, revocation по
  ``pod_id`` + Redis и общий HS256-секрет с dev-фолбэками (``bridge.py:111-128``);
* плагин — **иной principal**: company-scoped, stateless, ``instance`` + версия +
  артефакт. Поэтому здесь свой ``iss`` / ``aud`` / ``typ`` / ``kid`` / ключ.

Токен намеренно **не** несёт ``egress`` и ``quotas``: это долгоживущие политики,
они хранятся в ``CapabilityGrant`` в БД (§4.2, §6.1, критерий №22). Токен несёт
только ``grant_id`` и короткий TTL, иначе истечение JWT пересобирало бы сетевые
правила посреди запроса.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# --- Константы контура -------------------------------------------------------
# Жёстко отличаются от pod_bridge (``aud=prodavan-pod-bridge``,
# ``typ=pod_bridge``). Совпадение хотя бы одного поля было бы дырой allowlist.
PLUGIN_ISSUER: Final[str] = "agentscale-plugins"
PLUGIN_AUDIENCE: Final[str] = "capability-gateway"
PLUGIN_TOKEN_TYP: Final[str] = "plugin_capability"

# Короткий TTL по умолчанию (§4.2: 300 с, новый при wake-up).
DEFAULT_PLUGIN_TOKEN_TTL_SECONDS: Final[int] = 300

# Формат ``artifact_digest`` единообразен с остальным репозиторием:
# ``sha256:<64 lowercase hex>`` (dynamic_apps/types.py, crud_service.py).
ARTIFACT_DIGEST_PATTERN: Final[str] = r"^sha256:[0-9a-f]{64}$"

# ``jti`` — hex (uuid4().hex) или UUID с дефисами; иные значения считаем мусором.
JTI_PATTERN: Final[str] = r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$|^[0-9a-fA-F]{32}$"

# Путь capability-surface плагинов (§4.1, §7.2). Агентские пути закрыты.
CAPABILITIES_PATH_PATTERN: Final[str] = r"^/capabilities/v1/[A-Za-z0-9._\-]+(?:/[A-Za-z0-9._~!$&'()*+,;=:@%\-]+)*/?$"

# Обязательные claim. ``iss``/``aud``/``typ`` проверяются и по наличию, и по
# значению; ``iat`` нужен, чтобы нельзя было выпустить «вечный» токен без метки.
REQUIRED_CLAIMS: Final[frozenset[str]] = frozenset(
    {
        "iss",
        "aud",
        "typ",
        "sub",
        "company_id",
        "plugin_id",
        "plugin_instance_id",
        "manifest_version",
        "artifact_digest",
        "grant_id",
        "scopes",
        "quota_ref",
        "jti",
        "iat",
        "exp",
    }
)

# Claim, которые запрещено класть в токен: политики живут в БД (критерий №22).
FORBIDDEN_CLAIMS: Final[frozenset[str]] = frozenset({"egress", "quotas"})


class PluginTokenErrorCode(StrEnum):
    """Стабильные machine-readable коды отказов (fail-closed)."""

    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    WRONG_AUDIENCE = "WRONG_AUDIENCE"
    WRONG_TYP = "WRONG_TYP"
    WRONG_ISSUER = "WRONG_ISSUER"
    EXPIRED = "EXPIRED"
    MALFORMED = "MALFORMED"
    MISSING_CLAIM = "MISSING_CLAIM"
    UNKNOWN_KID = "UNKNOWN_KID"
    REVOKED = "REVOKED"
    FORBIDDEN_SURFACE = "FORBIDDEN_SURFACE"


class PluginTokenError(Exception):
    """Ошибка проверки plugin-токена / поверхности.

    Никаких «тихих» фолбэков: каждая ошибка несёт ``code``, по которому caller
    (gateway / middleware) принимает решение ``401`` или ``403``. Наследник
    ``Exception``, а не ``AppError``: модуль identity не должен тащить за собой
    конфиг и HTTP-слой; маппинг в problem+json делает родитель при интеграции.
    """

    def __init__(self, *, code: PluginTokenErrorCode | str, detail: str) -> None:
        self.code = PluginTokenErrorCode(str(code))
        self.detail = detail
        super().__init__(f"{self.code.value}: {detail}")


@dataclass(frozen=True, slots=True)
class PluginClaims:
    """Проверенные claim capability-токена плагина (§4.2).

    ``cabinet_id`` / ``project_id`` опциональны: плагин company-wide по дизайну.
    ``sub`` канонически равен ``plugin:<plugin_id>:<company_id>:<instance_id>`` —
    совпадение с ``pod:{id}`` исключено конструктивно.

    Инварианты (значения, типы, формат) проверяются в ``mint.build_claims`` при
    сборке из словаря; сам dataclass остаётся «чистым» контейнером, чтобы его
    можно было свободно создавать в тестах и передавать без IO.
    """

    # Константы контура хранятся как поля со значением по умолчанию: так токен
    # всегда сериализует их явно и verify может сравнить без внешних глобалов.
    iss: str = PLUGIN_ISSUER
    aud: str = PLUGIN_AUDIENCE
    typ: str = PLUGIN_TOKEN_TYP

    sub: str = ""
    company_id: str = ""
    cabinet_id: str | None = None
    project_id: str | None = None
    plugin_id: str = ""
    plugin_instance_id: str = ""
    manifest_version: str = ""
    artifact_digest: str = ""
    grant_id: str = ""
    scopes: tuple[str, ...] = ()
    quota_ref: str = ""
    jti: str = ""
    iat: int = 0
    exp: int = 0

    @staticmethod
    def build_sub(plugin_id: str, company_id: str, plugin_instance_id: str) -> str:
        """Канонический ``sub`` плагина (не ``pod:{id}``)."""
        return f"plugin:{plugin_id}:{company_id}:{plugin_instance_id}"

    def has_scope(self, scope: str) -> bool:
        """Есть ли capability-scope (без exceptions, для ветвлений)."""
        return scope in self.scopes

    def require_scope(self, scope: str) -> None:
        """Fail-closed: отсутствие scope = ``PluginTokenError``/``MISSING_CLAIM``."""
        if not self.has_scope(scope):
            raise PluginTokenError(
                code=PluginTokenErrorCode.MISSING_CLAIM,
                detail=f"plugin capability token missing scope {scope!r}",
            )

    def require_company(self, company_id: str) -> None:
        """Fail-closed: привязка токена к компании (не ``require_project``)."""
        if self.company_id != company_id:
            raise PluginTokenError(
                code=PluginTokenErrorCode.WRONG_AUDIENCE,
                detail="plugin capability token company_id mismatch",
            )

    def is_expired(self, now: int | float) -> bool:
        """Истёк ли токен. ``now`` — Unix epoch seconds (UTC), как ``exp``/``iat``.

        Без leeway: решает чётко. Небольшой leeway применяется только в
        ``verify_plugin_token`` как защита от рассинхрона часов.
        """
        return float(now) >= float(self.exp)
