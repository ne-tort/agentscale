"""Выпуск и проверка ``PluginClaims`` (F1: отдельный trust-контур).

Контракт (v4 §4.2): короткоживущий подписанный JWT с ``kid``/JWKS,
``iss=agentscale-plugins``, ``aud=capability-gateway``,
``typ=plugin_capability``, привязкой к ``artifact_digest`` и ``grant_id``.
**Никаких** ``egress`` / ``quotas`` в токене — они в ``CapabilityGrant`` в БД
(§4.2, §6.1, критерий №22). Отзыв = деактивация grant по ``grant_id``
(``revocation.py``), а не ``bump_pod_bridge_generation``.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any, Final, Mapping, Protocol, runtime_checkable
from uuid import uuid4

from .keys import KeyEntry, KeyRing, kid_from_secret, peek_plugin_token as _peek_plugin_token
from .types import (
    ARTIFACT_DIGEST_PATTERN,
    DEFAULT_PLUGIN_TOKEN_TTL_SECONDS,
    FORBIDDEN_CLAIMS,
    JTI_PATTERN,
    PLUGIN_AUDIENCE,
    PLUGIN_ISSUER,
    PLUGIN_TOKEN_TYP,
    REQUIRED_CLAIMS,
    PluginClaims,
    PluginTokenError,
    PluginTokenErrorCode,
)

# Небольшой допуск на рассинхрон часов при проверке exp (секунды).
EXPIRATION_LEEWAY_SECONDS: Final[int] = 5

_ARTIFACT_DIGEST_RE: Final[re.Pattern[str]] = re.compile(ARTIFACT_DIGEST_PATTERN)
_JTI_RE: Final[re.Pattern[str]] = re.compile(JTI_PATTERN)
_SAFE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
# ``quota_ref`` — ссылка вида ``co_123/plan_pro`` (§4.2), слеш допустим.
_SAFE_REF_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._:\-/]{1,128}$")


@runtime_checkable
class MintGrant(Protocol):
    """Минимальный контракт ``CapabilityGrant``, нужный для выпуска токена.

    Protocol намеренно: F0/задача 01 создаёт доменные типы в ``domain/plugins``,
    а F2 переиспользует ``CapabilityGrant`` из ``capability_gateway_service``.
    Мы не импортируем ни тот, ни другой, чтобы не связывать trust-контур с ещё
    не существующим кодом и не завязываться на БД/ORM. Достаточно атрибутов.
    """

    @property
    def grant_id(self) -> str: ...

    @property
    def plugin_id(self) -> str: ...

    @property
    def company_id(self) -> str: ...

    @property
    def scopes(self) -> Any: ...

    @property
    def manifest_version(self) -> str: ...

    @property
    def artifact_digest(self) -> str: ...

    @property
    def quota_ref(self) -> str: ...


@runtime_checkable
class MintInstance(Protocol):
    """Минимальный контракт установленного экземпляра плагина."""

    @property
    def plugin_instance_id(self) -> str: ...

    @property
    def cabinet_id(self) -> str | None: ...

    @property
    def project_id(self) -> str | None: ...


def _attr(source: Any, name: str, *, default: Any = None) -> Any:
    """Прочитать атрибут или ключ mapping (grant может быть и dataclass, и dict)."""
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _string(
    source: Any,
    name: str,
    *,
    code: PluginTokenErrorCode,
    required: bool = True,
    missing_code: PluginTokenErrorCode | None = None,
) -> str:
    value = _attr(source, name)
    if value is None:
        if required:
            raise PluginTokenError(
                code=missing_code or code,
                detail=f"plugin grant missing {name}",
            )
        return ""
    text = str(value).strip()
    if not text and required:
        raise PluginTokenError(
            code=missing_code or code,
            detail=f"plugin grant has empty {name}",
        )
    if text:
        # ``quota_ref`` — ссылка, а не идентификатор: в v4 это ``co_123/plan_pro``.
        pattern = _SAFE_REF_RE if name == "quota_ref" else _SAFE_ID_RE
        if name in {
            "plugin_id",
            "company_id",
            "plugin_instance_id",
            "grant_id",
            "quota_ref",
            "cabinet_id",
            "project_id",
        } and not pattern.fullmatch(text):
            raise PluginTokenError(code=code, detail=f"plugin grant has malformed {name}")
    return text


def _scopes(source: Any) -> tuple[str, ...]:
    raw = _attr(source, "scopes", default=())
    if raw is None:
        raw = ()
    if isinstance(raw, str):
        # Строку не принимаем: «cache,objects» легко превратить в один scope
        # и случайно выдать больше прав, чем предполагал grant.
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="plugin grant scopes must be a sequence, not a string",
        )
    if not isinstance(raw, (list, tuple, frozenset, set)):
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="plugin grant scopes must be a sequence",
        )
    out: list[str] = []
    for item in raw:
        scope = str(item).strip()
        if not scope:
            continue
        if not _SAFE_ID_RE.fullmatch(scope):
            raise PluginTokenError(
                code=PluginTokenErrorCode.MALFORMED,
                detail=f"plugin grant has malformed scope {scope!r}",
            )
        if scope not in out:
            out.append(scope)
    return tuple(out)


def build_payload(
    *,
    grant: Any,
    instance: Any,
    now: int,
    ttl_seconds: int,
) -> dict[str, Any]:
    """Собрать и провалидировать payload токена (без подписи)."""
    if ttl_seconds <= 0:
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="plugin token ttl_seconds must be positive",
        )

    grant_id = _string(grant, "grant_id", code=PluginTokenErrorCode.MISSING_CLAIM)
    plugin_id = _string(grant, "plugin_id", code=PluginTokenErrorCode.MISSING_CLAIM)
    company_id = _string(grant, "company_id", code=PluginTokenErrorCode.MISSING_CLAIM)
    manifest_version = _string(grant, "manifest_version", code=PluginTokenErrorCode.MISSING_CLAIM)
    # Пустой/битый digest — это MALFORMED, а не «claim отсутствует»; формат
    # ``sha256:<64 lowercase hex>`` проверяется отдельно ниже. При полностью
    # отсутствующем ключе — MISSING_CLAIM.
    artifact_digest = _string(
        grant,
        "artifact_digest",
        code=PluginTokenErrorCode.MALFORMED,
        missing_code=PluginTokenErrorCode.MISSING_CLAIM,
    )
    quota_ref = _string(grant, "quota_ref", code=PluginTokenErrorCode.MISSING_CLAIM)
    scopes = _scopes(grant)

    plugin_instance_id = _string(instance, "plugin_instance_id", code=PluginTokenErrorCode.MISSING_CLAIM)
    cabinet_id = _string(instance, "cabinet_id", code=PluginTokenErrorCode.MALFORMED, required=False) or None
    project_id = _string(instance, "project_id", code=PluginTokenErrorCode.MALFORMED, required=False) or None

    if not _ARTIFACT_DIGEST_RE.fullmatch(artifact_digest):
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="artifact_digest must be sha256:<64 lowercase hex>",
        )

    payload: dict[str, Any] = {
        "iss": PLUGIN_ISSUER,
        "aud": PLUGIN_AUDIENCE,
        "typ": PLUGIN_TOKEN_TYP,
        "sub": PluginClaims.build_sub(plugin_id, company_id, plugin_instance_id),
        "company_id": company_id,
        "cabinet_id": cabinet_id,
        "project_id": project_id,
        "plugin_id": plugin_id,
        "plugin_instance_id": plugin_instance_id,
        "manifest_version": manifest_version,
        "artifact_digest": artifact_digest,
        "grant_id": grant_id,
        "scopes": list(scopes),
        "quota_ref": quota_ref,
        "jti": uuid4().hex,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    # Дополнительная страховка: даже если grant — произвольный dict с полями
    # egress/quotas, в токен они не попадают (критерий №22). Payload собран
    # из фиксированного набора ключей, поэтому чужие поля не проходят.
    for name in FORBIDDEN_CLAIMS:
        payload.pop(name, None)
    return payload


def claims_from_payload(payload: Mapping[str, Any]) -> PluginClaims:
    """Построить ``PluginClaims`` из уже проверенного payload (fail-closed).

    Вызывается только после проверки подписи; тем не менее валидирует наличие,
    типы и формат каждого claim — «подпись верна» ≠ «claim корректны».
    """
    missing = sorted(name for name in REQUIRED_CLAIMS if name not in payload or payload[name] is None)
    if missing:
        raise PluginTokenError(
            code=PluginTokenErrorCode.MISSING_CLAIM,
            detail=f"plugin token missing claims: {', '.join(missing)}",
        )
    for name in FORBIDDEN_CLAIMS:
        if name in payload:
            raise PluginTokenError(
                code=PluginTokenErrorCode.MALFORMED,
                detail=f"plugin token must not carry long-lived policy claim {name!r}",
            )

    iss = str(payload["iss"])
    aud = str(payload["aud"])
    typ = str(payload["typ"])
    if iss != PLUGIN_ISSUER:
        raise PluginTokenError(code=PluginTokenErrorCode.WRONG_ISSUER, detail="wrong iss for plugin token")
    if aud != PLUGIN_AUDIENCE:
        raise PluginTokenError(code=PluginTokenErrorCode.WRONG_AUDIENCE, detail="wrong aud for plugin token")
    if typ != PLUGIN_TOKEN_TYP:
        raise PluginTokenError(code=PluginTokenErrorCode.WRONG_TYP, detail="wrong typ for plugin token")

    try:
        iat = int(payload["iat"])
        exp = int(payload["exp"])
    except (TypeError, ValueError) as exc:
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="plugin token iat/exp must be integer epoch seconds",
        ) from exc
    if isinstance(payload["iat"], bool) or isinstance(payload["exp"], bool):
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="plugin token iat/exp must not be booleans",
        )
    if exp <= iat:
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="plugin token exp must be after iat",
        )

    jti = str(payload["jti"])
    if not _JTI_RE.fullmatch(jti):
        raise PluginTokenError(code=PluginTokenErrorCode.MALFORMED, detail="plugin token jti is malformed")

    artifact_digest = str(payload["artifact_digest"])
    if not _ARTIFACT_DIGEST_RE.fullmatch(artifact_digest):
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="plugin token artifact_digest must be sha256:<64 lowercase hex>",
        )

    scopes_raw = payload["scopes"]
    if not isinstance(scopes_raw, (list, tuple)):
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="plugin token scopes must be a list",
        )
    scopes = tuple(str(item) for item in scopes_raw if str(item).strip())

    sub = str(payload["sub"])
    expected_sub = PluginClaims.build_sub(
        str(payload["plugin_id"]),
        str(payload["company_id"]),
        str(payload["plugin_instance_id"]),
    )
    if sub != expected_sub:
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="plugin token sub does not match plugin/company/instance claims",
        )

    for name in (
        "company_id",
        "plugin_id",
        "plugin_instance_id",
        "manifest_version",
        "grant_id",
        "quota_ref",
    ):
        if not str(payload[name]).strip():
            raise PluginTokenError(
                code=PluginTokenErrorCode.MISSING_CLAIM,
                detail=f"plugin token has empty {name}",
            )

    cabinet_id = payload.get("cabinet_id")
    project_id = payload.get("project_id")
    return PluginClaims(
        iss=iss,
        aud=aud,
        typ=typ,
        sub=sub,
        company_id=str(payload["company_id"]),
        cabinet_id=str(cabinet_id) if cabinet_id else None,
        project_id=str(project_id) if project_id else None,
        plugin_id=str(payload["plugin_id"]),
        plugin_instance_id=str(payload["plugin_instance_id"]),
        manifest_version=str(payload["manifest_version"]),
        artifact_digest=artifact_digest,
        grant_id=str(payload["grant_id"]),
        scopes=scopes,
        quota_ref=str(payload["quota_ref"]),
        jti=jti,
        iat=iat,
        exp=exp,
    )


def mint_plugin_claims(
    *,
    grant: Any,
    instance: Any,
    secret: str | bytes,
    ttl_seconds: int = DEFAULT_PLUGIN_TOKEN_TTL_SECONDS,
) -> str:
    """Выпустить короткий capability-JWT плагина.

    ``secret`` передаётся **явно** (HS256). Никаких чтений ``settings`` и
    dev-фолбэков, в отличие от ``bridge.py::_signing_secret``: пустой секрет =
    ``PluginTokenError``. ``kid`` детерминирован по отпечатку секрета, поэтому
    ``verify_plugin_token`` с ``keyring_from_secret(secret)`` проверит токен.
    """
    if not secret:
        raise PluginTokenError(
            code=PluginTokenErrorCode.INVALID_SIGNATURE,
            detail="plugin token signing secret must be provided explicitly; no dev fallback",
        )
    now = int(time.time())
    payload = build_payload(grant=grant, instance=instance, now=now, ttl_seconds=int(ttl_seconds))
    entry = KeyEntry(
        kid=kid_from_secret(secret, "HS256"),
        algorithm="HS256",
        secret=secret,
    )
    return KeyRing([entry]).sign(payload)


def peek_plugin_token(token: str) -> dict[str, Any] | None:
    """Реэкспорт ``keys.peek_plugin_token`` для обратной совместимости.

    Каноническая реализация живёт в ``keys.py``: родитель импортирует её оттуда
    для plugin-ветки ``core/middleware.py`` (аналог
    ``peek_pod_bridge_token``). Результат — **только** для выбора ветки, не для
    авторизации; на битом токене возвращается ``None``.
    """
    return _peek_plugin_token(token)


def verify_plugin_token(
    token: str,
    *,
    keyring: KeyRing,
    now: int | float | datetime | None = None,
) -> PluginClaims:
    """Проверить подпись, ``kid``, issuer/audience/typ, claim, ``exp``, ``jti``.

    ``now`` — Unix epoch seconds или aware ``datetime``; ``None`` = ``time.time()``.
    Leeway = ``EXPIRATION_LEEWAY_SECONDS`` (защита от рассинхрона часов, не
    «продление» токена). Любая проблема = ``PluginTokenError`` с ``code``.
    """
    if not isinstance(token, str) or not token.strip():
        raise PluginTokenError(code=PluginTokenErrorCode.MALFORMED, detail="plugin token is empty")
    payload = keyring.decode(token)
    claims = claims_from_payload(payload)

    # Проверка обязательных claim/форматов и времени — fail-closed на каждом шаге.
    now_epoch = _epoch_seconds(now)
    # Leeway применяется только к exp: токен считается истёкшим, когда
    # now >= exp + leeway. ``iat`` в будущем (с тем же допуском) — тоже отказ.
    if now_epoch >= claims.exp + EXPIRATION_LEEWAY_SECONDS:
        raise PluginTokenError(code=PluginTokenErrorCode.EXPIRED, detail="plugin capability token expired")
    if now_epoch < claims.iat - EXPIRATION_LEEWAY_SECONDS:
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="plugin capability token iat is in the future",
        )
    return claims


def _epoch_seconds(now: int | float | datetime | None) -> float:
    if now is None:
        return time.time()
    if isinstance(now, datetime):
        if now.tzinfo is None:
            raise PluginTokenError(
                code=PluginTokenErrorCode.MALFORMED,
                detail="verify_plugin_token now datetime must be timezone-aware",
            )
        return now.astimezone(UTC).timestamp()
    return float(now)
