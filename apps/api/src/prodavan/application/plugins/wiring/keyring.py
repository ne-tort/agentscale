"""Сборка ``KeyRing`` плагинов из конфигурации (F11 / F1-стык).

Активный ключ — **только** ``config.claims_secret`` (``PLUGIN_CLAIMS_SECRET``).
Ротация: старые секреты из ``PLUGIN_CLAIMS_PREVIOUS_SECRETS`` (см.
``config.read_previous_secrets``) регистрируются как ключи ТОЛЬКО для
верификации in-flight токенов; последним в ``KeyRing`` идёт активный ключ,
поэтому ``KeyRing.active`` == новый секрет (``identity/keys.py:120-123``).

Почему именно так
-----------------
* ``kid`` детерминирован через ``kid_from_secret`` — ровно тем же способом,
  что и ``mint_plugin_claims(secret=...)`` в задаче 02. Иначе токены,
  выпущенные mint'ом, не нашли бы ключ в ring (стык F1↔F11).
* Никаких dev-фолбэков: здесь НЕ читаются ``auth_test_secret``,
  ``pod_identity_bridge_secret``, ``pod_agent_bridge_auth_token`` и прочие
  секреты агентского контура (§1, §4.1 v4, критерий №17). Пустой
  ``claims_secret`` = ошибка сборки, а не «подставим что-нибудь».
* JWKS: ``KeyRing.jwks()`` намеренно НЕ публикует HS256-ключи (симметричный
  секрет нельзя отдавать наружу, ``identity/keys.py:238-253``). Для чисто
  HS256-конфигурации это ``{"keys": []}`` — корректный fail-closed ответ, а не
  «забыли реализовать». При появлении EdDSA-пары (задача F11-доработка) те же
  функции автоматически отдадут OKP/Ed25519 JWK без приватной части.

Этот модуль про ``FastAPI``/БД/settings ничего не знает: на входе — уже
провалидированный ``PluginPlatformConfig``.
"""

from __future__ import annotations

from typing import Any, Final, Mapping

from prodavan.application.plugins.identity.keys import (
    KeyRing,
    hs256_entry,
    kid_from_secret,
)
from prodavan.application.plugins.wiring.config import (
    PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV,
    PluginPlatformConfig,
    PluginPlatformConfigError,
)

ALGORITHM: Final[str] = "HS256"


def active_kid_for(config: PluginPlatformConfig) -> str:
    """``kid`` активного ключа (детерминированный отпечаток секрета)."""
    return kid_from_secret(config.claims_secret, ALGORITHM)


def verification_kids(config: PluginPlatformConfig) -> tuple[str, ...]:
    """``kid`` всех ключей ring'а: сначала ротационные, затем активный."""
    return tuple(
        kid_from_secret(secret, ALGORITHM)
        for secret in (*config.previous_claims_secrets, config.claims_secret)
    )


def build_keyring(config: PluginPlatformConfig) -> KeyRing:
    """Собрать ``KeyRing``: старые ключи для verify + активный для sign/verify.

    Порядок важен: ``KeyRing`` считает активным последний переданный ключ,
    поэтому при ротации новый секрет обязан идти последним.

    Fail-closed:
      * ``enabled=False`` -> отказ (ключи для выключенной платформы не нужны,
        а их случайное создание маскировало бы ошибку композиции);
      * пустой секрет -> ``PluginPlatformConfigError`` (НЕ dev-фолбэк);
      * дублирующиеся ``kid`` (один и тот же секрет дважды) -> явная ошибка,
        а не «последний победил».
    """
    if not isinstance(config, PluginPlatformConfig):
        raise PluginPlatformConfigError("build_keyring expects PluginPlatformConfig")
    # Валидируем ещё раз: сборку могли вызвать с объектом, который обошёл
    # ``from_settings`` (тестовый fake / прямой конструктор dataclass).
    config.validate()
    if not config.enabled:
        raise PluginPlatformConfigError(
            "refusing to build a plugin KeyRing for a disabled platform; "
            "build_plugin_platform must return None instead"
        )
    if not config.claims_secret.strip():
        raise PluginPlatformConfigError(
            "PLUGIN_CLAIMS_SECRET is empty; no AUTH_TEST_SECRET / pod-bridge "
            "fallback is allowed for the plugin trust contour"
        )

    secrets = (*config.previous_claims_secrets, config.claims_secret)
    kids = verification_kids(config)
    duplicates = sorted({kid for kid in kids if kids.count(kid) > 1})
    if duplicates:
        raise PluginPlatformConfigError(
            f"duplicate plugin signing kids in {PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV}: "
            f"{', '.join(duplicates)}; each rotated secret must be unique"
        )

    entries = [
        hs256_entry(secret, kid=kid)
        for secret, kid in zip(secrets, kids, strict=True)
    ]
    ring = KeyRing(entries, active_kid=kids[-1])

    # Инвариант стыка с mint_plugin_claims: активный kid обязан совпадать с
    # тем, что проставит mint при выпуске токена на том же секрете.
    if ring.active_kid != active_kid_for(config):
        raise PluginPlatformConfigError("active kid does not match mint_plugin_claims kid")
    return ring


def jwks_for(keyring: KeyRing) -> dict[str, Any]:
    """Публичное JWKS платформы (без приватных/симметричных ключей).

    Для HS256-конфигурации вернёт ``{"keys": []}``: это осознанное решение
    задачи 02 (симметричный секрет не публикуется), а не отсутствие JWKS.
    """
    return keyring.jwks()


def keyring_report(config: PluginPlatformConfig, keyring: KeyRing) -> Mapping[str, Any]:
    """Диагностическая проекция БЕЗ секретов (для логов/отчёта родителю)."""
    return {
        "algorithm": ALGORITHM,
        "active_kid": keyring.active_kid,
        "verification_kids": list(verification_kids(config)),
        "rotation_keys": len(config.previous_claims_secrets),
        "jwks": jwks_for(keyring),
    }


__all__ = [
    "ALGORITHM",
    "active_kid_for",
    "build_keyring",
    "jwks_for",
    "keyring_report",
    "verification_kids",
]
