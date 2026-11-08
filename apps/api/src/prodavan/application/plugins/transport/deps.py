"""Зависимости plugin-surface: проверка ``PluginClaims`` для каждого запроса.

Цепочка (v4 §4.1/§4.2/§7.2, критерии №12 и №17, задача F1)::

    Authorization: Bearer <JWT>
      -> verify_plugin_token(token, keyring=...)          (задача 02, identity.mint)
      -> GrantRevocation(port).check(claims)              (задача 02, revocation)
      -> assert_plugin_surface(claims, request.url.path)  (задача 02, allowlist)

**Обязательный** вызов ``assert_plugin_surface`` — именно он закрывает плагину
agent-surface (``/agent/*``, ``/internal/pods/*``, ``/projects/*/modules/*``).
Без него повторяется ошибка v2/v3: смешение trust даёт доступ к
``/internal/pods/{pod_id}/workspace-archive``.

Fail-closed:
  * нет заголовка / не Bearer / пустой токен → 401;
  * ``typ=pod_bridge`` (и любой не-plugin ``typ``) → 401/403, даже если подпись
    «похожа»: Plugin Gateway не обслуживает агентские токены (§4.1);
  * keyring / revocation не подключены → 503 (нет интеграции ≠ «пустить»);
  * ошибка verify / отзыв / чужая поверхность → 401/403 с типизированным кодом.

Входящие ``X-Plugin-*`` (и ``X-Company-ID``/``X-Project-ID``) НЕ читаются
вообще: по §7.2 их перезаписывает Plugin Gateway доверенными значениями, но
backend обязан проверять JWT, а не доверять заголовкам «по умолчанию». Все
identity-поля берутся ТОЛЬКО из claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Protocol, runtime_checkable

from fastapi import Depends, Request

from prodavan.application.plugins.identity.allowlist import (
    assert_plugin_surface,
    is_plugin_capability_token,
)
from prodavan.application.plugins.identity.mint import verify_plugin_token
from prodavan.application.plugins.identity.types import (
    PLUGIN_TOKEN_TYP,
    PluginClaims,
    PluginTokenError,
    PluginTokenErrorCode,
)
from prodavan.application.plugins.transport.errors import (
    PluginProblem,
    PluginTransportError,
)

# Заголовки, которые клиент мог прислать, чтобы подменить identity. Их просто
# не читаем; константа нужна для тестов и явной фиксации решения (§7.2).
UNTRUSTED_PLUGIN_HEADERS: frozenset[str] = frozenset(
    {
        "x-plugin-id",
        "x-plugin-instance-id",
        "x-plugin-capability-token",
        "x-company-id",
        "x-project-id",
        "x-cabinet-id",
        "x-forwarded-for",
        "x-forwarded-host",
        "cookie",
    }
)

BEARER_PREFIX = "Bearer "


@runtime_checkable
class KeyringLike(Protocol):
    """Структурный контракт KeyRing задачи 02 (``decode`` используется verify)."""

    def decode(self, token: str) -> dict: ...


@runtime_checkable
class RevocationLike(Protocol):
    """Структурный контракт ``GrantRevocation`` / ``RevocationPort`` задачи 02."""

    def check(self, claims: PluginClaims) -> PluginClaims: ...


@dataclass(frozen=True, slots=True)
class PluginIdentityConfig:
    """Что родитель обязан привязать к ``app.state`` при F11-интеграции."""

    keyring: object
    revocation: object


def _bearer_token(request: Request) -> str | None:
    """Прочитать ТОЛЬКО ``Authorization: Bearer``; token в URL query запрещён."""
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith(BEARER_PREFIX):
        return None
    token = auth[len(BEARER_PREFIX) :].strip()
    return token or None


def _unauthorized(detail: str, code: str = "UNAUTHORIZED") -> PluginTransportError:
    return PluginTransportError(PluginProblem(401, code, "Unauthorized", detail))


def _forbidden(detail: str, code: str = "FORBIDDEN") -> PluginTransportError:
    return PluginTransportError(PluginProblem(403, code, "Forbidden", detail))


def _unavailable(detail: str, code: str) -> PluginTransportError:
    return PluginTransportError(PluginProblem(503, code, "Service Unavailable", detail))


def resolve_identity_config(request: Request) -> PluginIdentityConfig:
    """Достать keyring + revocation из ``app.state`` (родитель связывает при F11).

    Поддерживаются два равнозначных варианта, чтобы не навязывать родителю
    конкретную форму интеграции:
      * ``app.state.plugin_keyring`` + ``app.state.plugin_revocation``;
      * ``app.state.plugin_identity`` — объект/маппинг с полями ``keyring`` /
        ``revocation`` (в т.ч. ``PluginIdentityConfig``).

    Ничего не найдено → 503 ``IDENTITY_NOT_CONFIGURED``. **Никаких** чтений
    ``settings`` и dev-фолбэков: задача 02 их принципиально не имеет.
    """
    state = request.app.state
    keyring = getattr(state, "plugin_keyring", None)
    revocation = getattr(state, "plugin_revocation", None)

    identity = getattr(state, "plugin_identity", None)
    if identity is not None:
        if isinstance(identity, PluginIdentityConfig):
            keyring = identity.keyring
            revocation = identity.revocation
        elif isinstance(identity, dict):
            keyring = identity.get("keyring", keyring)
            revocation = identity.get("revocation", revocation)
        else:
            keyring = getattr(identity, "keyring", keyring)
            revocation = getattr(identity, "revocation", revocation)

    if keyring is None or revocation is None:
        raise _unavailable(
            "plugin identity is not configured: keyring and revocation are required",
            "IDENTITY_NOT_CONFIGURED",
        )
    return PluginIdentityConfig(keyring=keyring, revocation=revocation)


def get_plugin_claims(
    request: Request,
    identity: Annotated[PluginIdentityConfig, Depends(resolve_identity_config)],
) -> PluginClaims:
    """Проверить capability-JWT плагина и вернуть проверенные ``PluginClaims``.

    Сигнатура ``(request, keyring, revocation) -> PluginClaims`` из ТЗ реализована
    через FastAPI-зависимость ``identity`` (keyring + revocation): саму функцию
    можно вызвать и напрямую в unit-тестах, передав конфиг явно.

    Ошибки → ``PluginTransportError`` (401/403) с типизированным ``code``; их
    перехватывает ``PluginRoute`` и превращает в problem+json.
    """
    # Явно фиксируем: входящие X-Plugin-* / X-Company-ID заголовки НЕ являются
    # источником identity. Не читаем их вообще (даже для логирования — там не
    # должно быть клиентских значений). Gateway их перезаписывает, а мы всё
    # равно доверяем только подписанным claims.
    token = _bearer_token(request)
    if token is None:
        raise _unauthorized("Authorization: Bearer plugin capability token required")

    # Ранняя отсечка чужих trust-контуров до криптографии: ``typ=pod_bridge``
    # из :8001 не должен даже доходить до verify_plugin_token (§4.1, кр. 17).
    if not is_plugin_capability_token(token):
        raise _unauthorized(
            "only typ=plugin_capability tokens are accepted on the plugin surface",
            "WRONG_TYP",
        )

    try:
        claims = verify_plugin_token(token, keyring=identity.keyring)  # type: ignore[arg-type]
    except PluginTokenError as exc:
        code = exc.code
        # FORBIDDEN_SURFACE / REVOKED — 403; прочие ошибки подписи/claim — 401.
        if code == PluginTokenErrorCode.FORBIDDEN_SURFACE:
            raise _forbidden(str(exc.detail), code.value) from exc
        if code == PluginTokenErrorCode.REVOKED:
            # Единый канонический код для HTTP-клиентов (совпадает с deny-кодом
            # ядра GRANT_REVOKED из задачи 03).
            raise _forbidden(str(exc.detail), "GRANT_REVOKED") from exc
        raise _unauthorized(str(exc.detail), code.value) from exc
    except Exception as exc:
        # Любая непредвиденная ошибка криптографии/ключа = отказ, не allow.
        raise _unauthorized("plugin token verification failed", "INVALID_SIGNATURE") from exc

    # Дополнительная страховка: verify уже проверил typ, но allowlist задачи 02
    # тоже делает это независимо. Явная проверка упрощает диагностику.
    if claims.typ != PLUGIN_TOKEN_TYP:
        raise _unauthorized("plugin token has wrong typ", "WRONG_TYP")

    # Проверка отзыва grant_id на КАЖДОМ запросе (кр. 12). Поддерживаются обе
    # формы задачи 02: GrantRevocation.check(claims) и порт is_revoked(grant_id).
    try:
        if hasattr(identity.revocation, "check"):
            claims = identity.revocation.check(claims)  # type: ignore[union-attr]
        elif hasattr(identity.revocation, "is_revoked"):
            if identity.revocation.is_revoked(claims.grant_id):  # type: ignore[union-attr]
                raise PluginTokenError(
                    code=PluginTokenErrorCode.REVOKED,
                    detail="capability grant is revoked",
                )
        elif hasattr(identity.revocation, "is_active"):
            # Тот же порт, что ждёт PluginCapabilityService (задача 03):
            # is_active(grant_id, now=...). Позволяет родителю привязать ОДИН
            # объект и к middleware, и к сервису.
            if not identity.revocation.is_active(  # type: ignore[union-attr]
                claims.grant_id, now=datetime.now(UTC)
            ):
                raise PluginTokenError(
                    code=PluginTokenErrorCode.REVOKED,
                    detail="capability grant is not active",
                )
        else:
            raise _unavailable(
                "revocation source is not usable: check()/is_revoked()/is_active() missing",
                "REVOCATION_NOT_CONFIGURED",
            )
    except PluginTransportError:
        raise
    except PluginTokenError as exc:
        canonical = "GRANT_REVOKED" if exc.code == PluginTokenErrorCode.REVOKED else exc.code.value
        raise _forbidden(str(exc.detail), canonical) from exc
    except Exception as exc:
        # Хранилище отзывов недоступно ≠ «grant активен» (fail-closed).
        raise _forbidden(
            "revocation source unavailable; refusing request",
            "GRANT_REVOKED",
        ) from exc

    # ОБЯЗАТЕЛЬНО: плагину разрешено только /capabilities/v1/*; agent-surface
    # (в т.ч. /agent/events) закрыт. Путь берём из request, НЕ из заголовков.
    try:
        claims = assert_plugin_surface(claims, request.url.path or "")
    except PluginTokenError as exc:
        raise _forbidden(str(exc.detail), exc.code.value) from exc

    return claims


PluginClaimsDep = Annotated[PluginClaims, Depends(get_plugin_claims)]

__all__ = [
    "BEARER_PREFIX",
    "KeyringLike",
    "PluginClaimsDep",
    "PluginIdentityConfig",
    "RevocationLike",
    "UNTRUSTED_PLUGIN_HEADERS",
    "get_plugin_claims",
    "resolve_identity_config",
]
