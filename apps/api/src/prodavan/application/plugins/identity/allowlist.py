"""Отдельная ветка allowlist для plugin-токенов (F1, критерий №17).

**НЕ правка** ``core/middleware.py``: агентская ветка
``PodSurfaceAllowlistMiddleware`` / ``_POD_SURFACE_RE`` остаётся как есть и
обслуживает ``typ=pod_bridge`` на ``:8001``. Здесь — независимый контур для
``typ=plugin_capability`` на route-group Plugin Gateway ``/capabilities/v1/*``
(v4 §4.1, §13.3, §14.3).

Явно задокументировано (и проверяется тестами): для
``typ=plugin_capability`` **agent-surface ЗАКРЫТ**:

* ``/agent/*`` (и ``/api/v1/agent/*``),
* ``/internal/pods/*`` (в т.ч. ``workspace-archive``),
* ``/projects/{id}/modules/*`` (в т.ч. ``.../data/t``),
* все остальные ветки ``_POD_SURFACE_RE`` из ``core/middleware.py:36-38``
  (``projects/{id}/infra``, ``documents``, ``web-search``, ``web-fetch``).

Иначе повторяется ошибка v2/v3: смешение trust даёт плагину доступ к
``/agent/*`` и ``/internal/pods/{pod_id}/workspace-archive``.
"""

from __future__ import annotations

import re
from typing import Final

from .keys import peek_plugin_token
from .types import (
    CAPABILITIES_PATH_PATTERN,
    PLUGIN_TOKEN_TYP,
    PluginClaims,
    PluginTokenError,
    PluginTokenErrorCode,
)

_CAPABILITIES_RE: Final[re.Pattern[str]] = re.compile(CAPABILITIES_PATH_PATTERN)

# Agent-surface (deny-by-default, явно перечислен для наглядности и тестов).
# Эти шаблоны не «разрешают», а фиксируют, что закрыто даже при совпадении с
# capabilities-префиксом (защита от обхода через вложенный путь).
_AGENT_SURFACE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^/(?:api/v1/)?(?:projects/[^/]+/)?agent(?:/|$)"),
    re.compile(r"^/(?:api/v1/)?internal/pods(?:/|$)"),
    re.compile(r"^/(?:api/v1/)?projects/[^/]+/modules(?:/|$)"),
    re.compile(r"^/(?:api/v1/)?projects/[^/]+/(?:infra|documents|web-search|web-fetch)(?:/|$)"),
)

# Публичные внешние пути плагинов (§7.1) тоже не являются capability-surface:
# к ним применяются сессия / HMAC-подпись вебхука, а не capability JWT.
_EXTERNAL_PLUGIN_PATHS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^/webhook(?:/|$)"),
    re.compile(r"^/pluginpage(?:/|$)"),
    re.compile(r"^/gateway/(?:plugins|cron|events|tasks)(?:/|$)"),
)


class PluginSurfaceAllowlist:
    """Проверка: plugin-токен имеет доступ **только** к ``/capabilities/v1/*``.

    stateless, без зависимостей от FastAPI/Starlette: родитель подключает
    готовую функцию ``assert_plugin_surface`` (или
    ``is_plugin_surface_path``) в своей ветке middleware, не редактируя
    ``core/middleware.py``.
    """

    def is_allowed(self, claims: PluginClaims, path: str) -> bool:
        """Разрешён ли ``path`` для данных claim (без исключений)."""
        if claims.typ != PLUGIN_TOKEN_TYP:
            # Чужой тип токена в plugin-ветке = закрыть (fail-closed).
            return False
        return is_plugin_surface_path(path)

    def assert_plugin_surface(self, claims: PluginClaims, path: str) -> PluginClaims:
        """Fail-closed: неверный путь → ``PluginTokenError``/``FORBIDDEN_SURFACE``."""
        if not self.is_allowed(claims, path):
            raise PluginTokenError(
                code=PluginTokenErrorCode.FORBIDDEN_SURFACE,
                detail=(
                    f"plugin capability token is limited to /capabilities/v1/*; "
                    f"agent-surface and external plugin paths are closed (path={path!r})"
                ),
            )
        return claims


def is_plugin_surface_path(path: str) -> bool:
    """Проверка пути без claim (для маршрутизации / pre-filter middleware).

    * Совпадение с ``^/capabilities/v1/<capability>(/...)`` → ``True``.
    * Всё остальное, включая query-строку, ``//``, ``..``, абсолютные URL,
      agent-surface, ``/webhook``, ``/pluginpage`` → ``False``.
    """
    raw = path or ""
    if not raw.startswith("/"):
        return False
    # Query/fragment в path быть не должно; их наличие = попытка обхода.
    if "?" in raw or "#" in raw:
        return False
    # Сравниваем по исходной строке, без нормализации:
    # ``/capabilities/v1/../agent/events`` не должен превратиться в «разрешено».
    if ".." in raw or "//" in raw or "\\" in raw:
        return False
    for pattern in _AGENT_SURFACE_PATTERNS:
        if pattern.match(raw):
            return False
    for pattern in _EXTERNAL_PLUGIN_PATHS:
        if pattern.match(raw):
            return False
    return bool(_CAPABILITIES_RE.match(raw))


def is_plugin_capability_token(token: str) -> bool:
    """Определить тип токена для разветвления middleware (без проверки подписи).

    Аналог ``bridge.peek_pod_bridge_token`` + ``middleware._is_pod_bridge_token``,
    но для plugin-контура. **Только** для выбора ветки: авторизацию делает
    ``verify_plugin_token``.
    """
    peeked = peek_plugin_token(token)
    return bool(peeked and peeked.get("typ") == PLUGIN_TOKEN_TYP)


def assert_plugin_surface(claims: PluginClaims, path: str) -> PluginClaims:
    """Функция, которую родитель подключит в middleware (экспортируемый хук).

    Пример интеграции (делает родитель, НЕ этот модуль и НЕ правка
    ``core/middleware.py``)::

        token = _bearer(request)
        if token and is_plugin_capability_token(token):
            claims = verify_plugin_token(token, keyring=..., now=...)
            GrantRevocation(...).check(claims)
            assert_plugin_surface(claims, request.url.path)
    """
    return PluginSurfaceAllowlist().assert_plugin_surface(claims, path)


_DEFAULT_ALLOWLIST: Final[PluginSurfaceAllowlist] = PluginSurfaceAllowlist()

# Псевдоним на stateless-инстанс для удобства импорта в middleware.
plugin_surface_allowlist: Final[PluginSurfaceAllowlist] = _DEFAULT_ALLOWLIST
