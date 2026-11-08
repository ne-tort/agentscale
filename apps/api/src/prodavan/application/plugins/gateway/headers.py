"""Построение доверенных исходящих заголовков Plugin Gateway (§6.4, §7.2).

ГЛАВНОЕ ИСПРАВЛЕНИЕ ОТНОСИТЕЛЬНО v1
------------------------------------
В v1 клиентские заголовки распаковывались **ПОСЛЕ** доверенных
(``{**trusted, **request.headers}``), поэтому внешний клиент мог передать
``X-Company-ID: evil`` и перезаписать доверенное значение — подменить компанию,
плагин или capability-токен. Здесь порядок обратный и жёсткий:

1. ``sanitize_incoming`` отбрасывает ВСЁ, что клиент не имеет права задавать:
   входящие ``X-Plugin-*``, ``Cookie``, ``Authorization``,
   ``X-Forwarded-*``, hop-by-hop и «родственников» forwards;
2. ``build_trusted_headers`` берёт ТОЛЬКО доверенные значения из
   ``GatewayClaims`` / ``GatewayGrant`` и перезаписывает одноимённые ключи.

Таким образом клиентские значения физически не достигают downstream: они
удаляются до сборки, а доверенные выставляются последними.
"""

from __future__ import annotations

from collections.abc import Mapping

from prodavan.application.plugins.gateway.ports import GatewayClaims, GatewayGrant

# ---------------------------------------------------------------------------
# Доверенные заголовки (перезаписываются значениями из claims/grant)
# ---------------------------------------------------------------------------

HEADER_PLUGIN_ID = "X-Plugin-ID"
HEADER_COMPANY_ID = "X-Company-ID"
HEADER_PROJECT_ID = "X-Project-ID"
HEADER_CAPABILITY_TOKEN = "X-Plugin-Capability-Token"

#: Полный allowlist доверенных заголовков. Ни один из этих ключей не может
#: прийти от клиента — см. ``sanitize_incoming``.
TRUSTED_HEADERS: frozenset[str] = frozenset(
    {
        HEADER_PLUGIN_ID,
        HEADER_COMPANY_ID,
        HEADER_PROJECT_ID,
        HEADER_CAPABILITY_TOKEN,
    }
)

#: Заголовки, которые клиент не имеет права задавать. Сравнение — по нижнему
#: регистру (HTTP-заголовки регистронезависимы).
#:
#: ВАЖНО: сюда входят ВСЕ четыре доверенных заголовка. Клиент может прислать
#: ``X-Company-Id`` (другой регистр!), а ``build_trusted_headers`` оперирует
#: каноническим ``X-Company-ID``. Если оставить клиентский вариант, dict с
#: регистроразличимыми ключами сохранит ОБА значения и downstream выберет
#: не то. Поэтому доверенные имена удаляются из входящих целиком, а затем
#: выставляются заново под каноническими именами.
_STRIPPED_EXACT: frozenset[str] = frozenset(
    {
        "cookie",
        "authorization",
        "proxy-authorization",
        "proxy-authenticate",
        "x-plugin-id",
        "x-company-id",
        "x-project-id",
        "x-plugin-capability-token",
        # HMAC-подпись — артефакт аутентификации САМОГО шлюза (§7.5). Её
        # проверяет GatewayAuth; downstream её получить не должен, иначе
        # backend решит, что «тоже проверил подпись» и ослабит проверку JWT.
        "x-prodavan-signature",
    }
)

#: Префиксы: все входящие X-Plugin-* и X-Forwarded-* (включая X-Forwarded-Proto,
#: X-Forwarded-Host, X-Forwarded-For, X-Real-Ip, Forwarded, X-Original-*).
_STRIPPED_PREFIXES: tuple[str, ...] = (
    "x-plugin-",
    "x-forwarded-",
    "x-original-",
    "x-real-ip",
    "forwarded",
)

#: Hop-by-hop заголовки (RFC 7230 §6.1) + стандартные «connection»-служебные.
#: Их нельзя проксировать: они описывают конкретное плечо TCP-соединения.
_HOP_BY_HOP: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "host",
        "content-length",
    }
)


def _is_stripped(name_lower: str) -> bool:
    """Клиентский заголовок подлежит безусловному удалению."""
    if name_lower in _STRIPPED_EXACT or name_lower in _HOP_BY_HOP:
        return True
    return any(name_lower.startswith(prefix) for prefix in _STRIPPED_PREFIXES)


def sanitize_incoming(headers: Mapping[str, str]) -> dict[str, str]:
    """Вернуть копию входящих заголовков с отброшенными недоверенными.

    Fail-closed по умолчанию: удаляется всё, что помечено в ``_STRIPPED_EXACT``,
    ``_STRIPPED_PREFIXES`` или ``_HOP_BY_HOP``. Остаются только «безобидные»
    заголовки представления (``Accept``, ``Content-Type``, ``Accept-Encoding``,
    ``User-Agent``, ``If-None-Match`` и т.п.).

    ВАЖНО: ``Content-Length`` и ``Host`` тоже отбрасываются — downstream должен
    вычислить их заново по фактическому телу/адресу, иначе клиент может
    рассогласовать длину тела и спровоцировать request smuggling.

    Функция НЕ добавляет доверенных заголовков — это делает строго
    ``build_trusted_headers`` после неё.
    """
    out: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str):
            continue
        key = name.lower()
        if _is_stripped(key):
            continue
        # Пустое имя/значение не проксируем: мусорный заголовок — не данные.
        if not value or not isinstance(value, str):
            continue
        out[name] = value
    return out


def build_trusted_headers(
    claims: GatewayClaims, grant: GatewayGrant
) -> dict[str, str]:
    """Собрать исходящие заголовки по allowlist'у из доверенных значений.

    Порядок критичен: доверенные значения выставляются **последними** и
    перезаписывают всё, что могло остаться после ``sanitize_incoming``.
    В v1 было наоборот — клиентские заголовки шли после доверенных.

    - ``X-Plugin-ID``, ``X-Company-ID`` — всегда из ``claims``;
    - ``X-Project-ID`` — из ``claims`` (у anon отсутствует: ключ удаляется);
    - ``X-Plugin-Capability-Token`` — из активного ``grant`` (у anon пуст,
      ключ удаляется). Клиентский токен отброшен ещё в ``sanitize_incoming``.

    Возвращается НОВЫЙ dict: вызывающий не может случайно изменить исходные
    заголовки и «протащить» клиентское значение обратно.
    """
    trusted: dict[str, str] = {
        HEADER_PLUGIN_ID: claims.plugin_id,
        HEADER_COMPANY_ID: claims.company_id,
    }

    # Проект есть только у авторизованного запроса; урезанный anon-scope его
    # не содержит. Явное удаление защищает от «остатков» в клиентских данных.
    if claims.project_id and not claims.anonymous:
        trusted[HEADER_PROJECT_ID] = claims.project_id
    else:
        trusted.pop(HEADER_PROJECT_ID, None)

    # Capability-токен выдаётся только при активном grant и только
    # авторизованному запросу. Публичная страница его не получает.
    if grant.active and grant.capability_token and not claims.anonymous:
        trusted[HEADER_CAPABILITY_TOKEN] = grant.capability_token
    else:
        trusted.pop(HEADER_CAPABILITY_TOKEN, None)

    return trusted


def build_forward_headers(
    incoming: Mapping[str, str],
    claims: GatewayClaims,
    grant: GatewayGrant,
) -> dict[str, str]:
    """Полный конвейер: сначала санитизация, затем доверенная перезапись.

    Единственная точка, которую должен использовать ``GatewayProxy``.
    Именно здесь зафиксирован правильный порядок (в v1 он был инвертирован):

        sanitize_incoming(incoming)      # клиентские X-Plugin-* удалены
        → update(build_trusted_headers)  # доверенные значения ПОСЛЕДНИМИ
    """
    out = sanitize_incoming(incoming)
    # Доверенные заголовки идут последними и перезаписывают любые совпадения.
    # Дополнительная страховка от регистроразличимых дублей (``x-company-id``
    # vs ``X-Company-ID``): удаляем любые ключи, чьё нижнее представление
    # совпадает с доверенным именем, и только потом выставляем канонические.
    trusted_lower = {name.lower() for name in TRUSTED_HEADERS}
    for key in [k for k in out if isinstance(k, str) and k.lower() in trusted_lower]:
        out.pop(key, None)
    out.update(build_trusted_headers(claims, grant))
    return out


__all__ = [
    "HEADER_CAPABILITY_TOKEN",
    "HEADER_COMPANY_ID",
    "HEADER_PLUGIN_ID",
    "HEADER_PROJECT_ID",
    "TRUSTED_HEADERS",
    "build_forward_headers",
    "build_trusted_headers",
    "sanitize_incoming",
]
