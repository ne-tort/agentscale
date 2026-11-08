"""Чистые детерминированные рендеры трёх controller-managed артефактов.

Три функции НЕ делают сетевых вызовов, НЕ читают k8s/БД и НЕ имеют состояния:

* ``render_tenant_hba(grants)``     → содержимое ``tenant_hba.conf``;
* ``render_pgbouncer_userlist(grants, secrets)`` → ``userlist.txt``;
* ``render_envoy_config(policies)`` → Envoy-конфиг (dict) через
  ``EgressConfigGenerator.build_envoy_routes(policy)`` задачи 08.

Детерминированность и идемпотентность
-------------------------------------
Вход сортируется по стабильному ключу (``grant_id`` / ``role`` / ``database``),
поэтому одинаковый набор grant'ов ВСЕГДА даёт байт-в-байт одинаковую строку.
Это обязательно для ``controller.sync()``: сравнение «рендер == текущий /data»
должно давать «без изменений» и НЕ дёргать reload. Любая случайность (порядок
словаря, set, UUID, время) здесь запрещена.

Fail-closed
-----------
* ``tenant_hba`` завершается ЯВНЫМ ``reject all`` для IPv4/IPv6, а ``map=``
  НЕ используется (``scram-sha-256`` несовместим с ident/peer map — postgres
  упадёт, см. ``tenant-postgres.yaml:91-98``);
* ``userlist`` содержит ТОЛЬКО SCRAM-секреты, проверяется формат; plaintext
  пароль не принимается и не попадает ни в результат, ни в repr/исключения;
* Envoy-конфиг при отсутствии активных grant'ов с egress-политикой СОВПАДАЕТ
  со стартовым fail-closed состоянием: ``clusters: []`` + ``direct_response 403``
  (``egress-proxy.yaml``); НИКАКОГО ``webhooks.example.com``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import yaml

from prodavan.application.plugins.egress.errors import EgressPolicyError
from prodavan.application.plugins.egress.generator import EgressConfigGenerator
from prodavan.application.plugins.egress.policy import EgressPolicy, resolve_policy
from prodavan.infrastructure.plugins.controllers.errors import RenderError
from prodavan.infrastructure.plugins.controllers.ports import (
    PGBOUNCER_ADMIN_USER,
    POD_CIDRS,
    ActiveGrant,
)

# ---------------------------------------------------------------------------
# Константы формата
# ---------------------------------------------------------------------------

#: Метод аутентификации pg_hba. ``map=`` с ним НЕДОПУСТИМ (SCRAM, не ident).
HBA_AUTH_METHOD = "scram-sha-256"

#: Финальный reject-all. Директивы include подставляются НА МЕСТЕ, т.е. ДО
#: reject-all основного pg_hba.conf, но дублирующий reject в include-файле
#: гарантирует fail-closed даже если основной файл был изменён вручную.
HBA_REJECT_ALL_LINES: tuple[str, ...] = (
    "host    all       all                     0.0.0.0/0       reject",
    "host    all       all                     ::/0             reject",
)

#: Формат SCRAM-секрета PgBouncer (pgbouncer.yaml:36):
#: ``SCRAM-SHA-256$<iterations>:<salt>$<stored>:<server>``.
_SCRAM_RE = re.compile(r"^SCRAM-SHA-256\$\d+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$")

#: Идентификатор PostgreSQL: ``[a-z0-9_]``, ≤63 байт. То же правило, что
#: ``tenantdb/naming.py`` (defense in depth: рендер не должен записать в pg_hba
#: строку с кавычкой/пробелом/``;``, даже если адаптер прислал «сырой» grant).
#: Оно же применяется к именам ролей в ``userlist.txt``: PgBouncer парсит
#: строки как ``"user" "secret"``, кавычка внутри имени сломала бы файл.
_PG_IDENT_RE = re.compile(r"^[a-z0-9_]+$")
_PG_MAX_IDENT_BYTES = 63

#: Комментарии-заголовки. Содержат ТОЛЬКО статический текст: никаких grant_id,
#: company_id, имён хостов или секретов (иначе audit/repr мог бы их унаследовать).
#: ВНИМАНИЕ: подстрока ``map=`` здесь ЗАПРЕЩЕНА — acceptance-тест проверяет её
#: отсутствие во всём файле, чтобы исключить ident/peer-маппинг с SCRAM.
_HBA_HEADER = (
    "# tenant_hba.conf — controller-managed, НЕ редактировать вручную.",
    "# Источник: активные CapabilityGrant. Пары (role, tenant_<company>)",
    "# заданы ЯВНО полями DATABASE/USER: опция map с scram-sha-256",
    "# недопустима (postgres упадёт при загрузке hba_file).",
    "# Финальный reject all — fail-closed для любых невыданных пар.",
)

_USERLIST_HEADER = (
    "# userlist.txt — controller-managed, НЕ редактировать вручную.",
    "# Только SCRAM-секреты активных CapabilityGrant. Plaintext запрещён.",
)

_ENVOY_DENY_BODY = "egress denied: no CapabilityGrant rule\n"

#: JSON access_log ЕДИНОЙ схемы (§6.3), идентичен стартовому манифесту
#: ``egress-proxy.yaml:103-119`` и ``EgressConfigGenerator._access_log()``.
#: Дублируем КОНСТАНТОЙ, а не дёргаем приватный метод чужого модуля: конфиг
#: сравнения «без изменений» должен быть байт-в-байт стабильным и не зависеть
#: от внутренней реализации задачи 08.
_ENVOY_ACCESS_LOG: dict[str, Any] = {
    "name": "envoy.access_loggers.stdout",
    "typed_config": {
        "@type": "type.googleapis.com/envoy.extensions.access_loggers.stream.v3.StdoutAccessLog",
        "log_format": {
            "json_format": {
                "event": "plugin_egress_request",
                "host": "%REQ(:AUTHORITY)%",
                "path": "%REQ(:PATH)%",
                "method": "%REQ(:METHOD)%",
                "status": "%RESPONSE_CODE%",
                "bytes": "%BYTES_SENT%",
                "decision": "%RESPONSE_CODE%",
                "tenant": "%REQ(X-Company-ID)%",
                "subject": "%REQ(X-Plugin-ID)%",
            }
        },
    },
}


# ---------------------------------------------------------------------------
# Внутренние валидаторы (fail-closed, без утечки значений в ошибки)
# ---------------------------------------------------------------------------


def _validate_identifier(value: object, *, field_name: str) -> str:
    """Проверить уже нормализованный PG-идентификатор.

    НЕ «санитайзер»: при любом отклонении — ``RenderError``, а не тихая
    замена символов. Исходное значение в текст ошибки НЕ подставляется
    (защита от отражения инъекции в логи/аудит).
    """
    if not isinstance(value, str) or not value:
        raise RenderError(reason=f"{field_name}_missing")
    if _PG_IDENT_RE.fullmatch(value) is None or not value.isascii():
        raise RenderError(reason=f"{field_name}_invalid")
    if len(value.encode("utf-8")) > _PG_MAX_IDENT_BYTES:
        raise RenderError(reason=f"{field_name}_too_long")
    return value


def _validate_scram(secret: object) -> str:
    """Проверить, что значение — SCRAM-секрет, а НЕ plaintext-пароль.

    Секрет НИКОГДА не попадает в текст исключения и не логируется: только
    стабильный код причины. Проверка формата — последний рубеж перед записью
    в Secret (pgbouncer.yaml явно требует SCRAM-SHA-256$...).
    """
    if not isinstance(secret, str) or not secret:
        raise RenderError(reason="scram_secret_missing")
    if secret.startswith("SCRAM-SHA-256$"):
        if _SCRAM_RE.fullmatch(secret) is None:
            raise RenderError(reason="scram_secret_malformed")
    else:
        # Всё, что не начинается с префикса SCRAM, считается plaintext-паролем
        # и ОТВЕРГАЕТСЯ. Dev-исключение из pgbouncer.yaml («plaintext допустим
        # только в dev») контроллер намеренно НЕ поддержал: запись пароля в
        # Secret расширяет поверхность утечки и ломает требование задачи.
        raise RenderError(reason="plaintext_password_forbidden")
    return secret


def _sorted_grants(grants: Iterable[ActiveGrant]) -> list[ActiveGrant]:
    """Привести к списку и отсортировать по стабильному ключу.

    Дополнительно отсеиваем неактивные записи и дубликаты ``grant_id``: адаптер
    мог вернуть отозванный grant (тогда пара НЕ должна появиться) или две версии
    одной записи. Сортировка по ``(grant_id, role, database)`` детерминирована
    независимо от порядка хранилища.
    """
    if isinstance(grants, (str, bytes)) or not isinstance(grants, Iterable):
        raise RenderError(reason="grants_not_iterable")
    items: list[ActiveGrant] = []
    for grant in grants:
        if not isinstance(grant, ActiveGrant):
            raise RenderError(reason="grant_type_invalid")
        if not getattr(grant, "active", True):
            continue
        items.append(grant)
    # Дубликаты grant_id с РАЗНЫМИ role/database — ошибка контракта, а не
    # «первый выиграл»: иначе один grant молча перезаписал бы другой.
    seen: dict[str, tuple[str, str]] = {}
    for grant in items:
        key = (grant.role, grant.database)
        previous = seen.setdefault(grant.grant_id, key)
        if previous != key:
            raise RenderError(reason="grant_id_collision")
    items.sort(key=lambda g: (g.grant_id, g.role, g.database))
    return items


# ---------------------------------------------------------------------------
# 1) tenant_hba.conf
# ---------------------------------------------------------------------------


def render_tenant_hba(grants: Iterable[ActiveGrant]) -> str:
    """Собрать ``tenant_hba.conf`` из АКТИВНЫХ grant'ов.

    Формат строки (как в ``tenant-postgres.yaml:139-140``)::

        host  tenant_<company>  tenant_<company>_plugin_<plugin>  10.42.0.0/16  scram-sha-256
        host  tenant_<company>  tenant_<company>_plugin_<plugin>  10.43.0.0/16  scram-sha-256

    * пара (DATABASE, USER) задана ЯВНО, БЕЗ ``map=``;
    * по одной строке на pod/service CIDR (``POD_CIDRS``);
    * финальный ``reject all`` (IPv4 + IPv6) — даже если основной pg_hba.conf
      был изменён, include-файл сам запрещает всё невыданное;
    * при пустом списке grant'ов возвращается ТОЛЬКО заголовок + reject all:
      ни один плагин не подключится (fail-closed);
    * только активные grant'ы: отозванный исчезает из файла на следующем sync.

    Возвращаемая строка ВСЕГДА заканчивается ``\\n`` (иначе kubelet/PostgreSQL
    могут по-разному трактовать последнюю строку и сравнение «без изменений»
    даст ложный patch).
    """
    lines: list[str] = list(_HBA_HEADER)
    for grant in _sorted_grants(grants):
        database = _validate_identifier(grant.database, field_name="database")
        role = _validate_identifier(grant.role, field_name="role")
        for cidr in POD_CIDRS:
            lines.append(f"host    {database}  {role}  {cidr}  {HBA_AUTH_METHOD}")
    lines.extend(HBA_REJECT_ALL_LINES)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 2) userlist.txt
# ---------------------------------------------------------------------------


def render_pgbouncer_userlist(
    grants: Iterable[ActiveGrant],
    secrets: Mapping[str, str] | None = None,
) -> str:
    """Собрать ``userlist.txt``: SCRAM-секреты ролей активных grant'ов.

    ``secrets`` — опциональное отображение ``role -> SCRAM-секрет``. Если оно
    передано, значение из ``secrets`` ИМЕЕТ ПРИОРИТЕТ над ``grant.scram_secret``
    (ротация секрета без изменения записи grant'а). Если не передано — берём
    ``grant.scram_secret``. Оба пути проходят одну и ту же проверку
    ``_validate_scram``: plaintext-пароль НЕ принимается ни откуда.

    БЕЗОПАСНОСТЬ representation:
      * результат — это и есть секрет; его НЕЛЬЗЯ логировать, но он ОБЯЗАН быть
        записан в Secret /data (иначе PgBouncer не узнает пароль роли);
      * в строку НЕ попадают ``grant_id`` / ``company_id`` / DSN / plaintext;
      * ``ActiveGrant.__repr__`` скрывает ``scram_secret``, а ``RenderError``
        никогда не содержит его значения. Тест проверяет, что ``repr`` пары
        и исключения не содержат plaintext-пароль.

    Роль ``pgbouncer_admin`` НЕ рендерится из grant'ов: это учётная запись
    admin-консоли, её секрет лежит в стартовом Secret (``pgbouncer.yaml:50``).
    Для сохранения этой строки при обновлении Secret используйте
    ``render_pgbouncer_userlist_preserving_admin`` (контроллер вызывает именно
    её): полная замена ``userlist.txt`` без админской строки сделала бы
    команду ``RELOAD;`` недоступной.

    Пустой список grant'ов = файл только с заголовком: ни одна роль плагина
    не пройдёт аутентификацию (fail-closed).
    """
    if secrets is not None and not isinstance(secrets, Mapping):
        raise RenderError(reason="secrets_not_mapping")

    ordered = _sorted_grants(grants)
    lines: list[str] = list(_USERLIST_HEADER)
    for grant in ordered:
        role = _validate_identifier(grant.role, field_name="role")
        if role == PGBOUNCER_ADMIN_USER:
            # Роль admin-консоли не может быть ролью плагина.
            raise RenderError(reason="pgbouncer_admin_role_forbidden")
        raw_secret = secrets.get(role, grant.scram_secret) if secrets is not None else grant.scram_secret
        secret = _validate_scram(raw_secret)
        # Имя роли и секрет не содержат кавычек (проверено regex выше и форматом
        # SCRAM), поэтому экранирование в userlist.txt не требуется.
        lines.append(f'"{role}" "{secret}"')
    return "\n".join(lines) + "\n"


def render_pgbouncer_userlist_preserving_admin(
    grants: Iterable[ActiveGrant],
    *,
    current: str,
    secrets: Mapping[str, str] | None = None,
) -> str:
    """То же, что ``render_pgbouncer_userlist``, но СОХРАНЯЕТ админскую строку.

    Стартовый Secret (``pgbouncer.yaml:49-52``) содержит
    ``"pgbouncer_admin" "..."`` — без неё консоль ``RELOAD;`` станет недоступна
    и контроллер не сможет применить следующий sync. Функция вытаскивает из
    ``current`` строки, чьё имя НЕ совпадает с ролями активных grant'ов и не
    является комментарием/пустой, и оставляет только ``pgbouncer_admin``.

    Секрет админской строки НЕ логируется и НЕ валидируется как SCRAM: это
    учётные данные консоли, а не роль tenant-БД (в dev это plaintext по
    pgbouncer.yaml). В результат попадает ровно исходная строка без изменений.
    """
    rendered = render_pgbouncer_userlist(grants, secrets)
    grant_roles = {g.role for g in _sorted_grants(grants)}
    preserved = ""
    for raw_line in (current or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r'^\s*"([^"]+)"\s+"([^"]*)"\s*$', line)
        if match is None:
            continue
        name = match.group(1)
        if name == PGBOUNCER_ADMIN_USER and name not in grant_roles:
            preserved = raw_line.rstrip("\n")
            break
    if not preserved:
        # Fail-closed: без admin-консоли RELOAD невозможен. Не «пропустим»,
        # а явно откажем — иначе следующий sync упрётся в недоступную консоль.
        raise RenderError(reason="pgbouncer_admin_line_missing")
    # Админская строка идёт ПЕРВОЙ (как в стартовом манифесте), затем header и
    # роли плагинов. Порядок фиксирован → детерминированность сравнения.
    body = rendered.splitlines(keepends=True)
    return f"{preserved}\n" + "".join(body)


# ---------------------------------------------------------------------------
# 3) Envoy-конфиг
# ---------------------------------------------------------------------------


def build_envoy_policies(grants: Iterable[ActiveGrant]) -> tuple[EgressPolicy, ...]:
    """Разобрать egress-секции активных grant'ов в ``EgressPolicy`` задачи 08.

    Это ЕДИНСТВЕННАЯ точка связи с задачей 08: ``resolve_policy(grant)`` сам
    валидирует режим, allowlist, wildcard, IP-литералы и approval для ``full``.
    Grant с ``egress=None`` / без секции ``egress`` даёт режим ``none`` → НЕ
    создаёт ни одного кластера (такой grant просто пропускается).

    Ошибка валидации политики = ``RenderError`` (fail-closed): битая политика
    НЕ должна превратиться в «разрешить всё» или в тихо пропущенный grant.
    """
    policies: list[EgressPolicy] = []
    for grant in _sorted_grants(grants):
        raw = grant.egress
        if raw is None:
            continue
        # resolve_policy понимает dict / dataclass / ORM-объект и сам достаёт
        # секцию `egress`. Привязываем grant_id для _meta даже если в сырых
        # данных его нет (§4.2: источник правил — долгоживущий grant в БД).
        payload: dict[str, Any]
        if isinstance(raw, Mapping):
            payload = dict(raw)
        else:
            payload = {"egress": raw}
        payload.setdefault("grant_id", grant.grant_id)
        if isinstance(payload.get("egress"), Mapping):
            section = dict(payload["egress"])
            section.setdefault("grant_id", grant.grant_id)
            payload["egress"] = section
        try:
            policy = resolve_policy(payload)
        except EgressPolicyError as exc:
            # НЕ подставляем exc: в нём может быть хост из allowlist. Только
            # стабильный код, пригодный для аудита без утечки конфигурации.
            raise RenderError(reason="egress_policy_invalid") from exc
        if policy.allows_any:
            policies.append(policy)
    return tuple(policies)


def _envoy_deny_route() -> dict[str, Any]:
    """``direct_response: 403`` для несовпадения (идентичен задаче 08)."""
    return {
        "match": {"prefix": "/"},
        "direct_response": {"status": 403, "body": {"inline_string": _ENVOY_DENY_BODY}},
    }


def _deny_vhost() -> dict[str, Any]:
    """Стартовый fail-closed vhost: ``domains: ["*"]`` + ``direct_response 403``."""
    return {
        "name": "default_deny",
        "domains": ["*"],
        "routes": [_envoy_deny_route()],
    }


def _empty_envoy_config() -> dict[str, Any]:
    """Стартовый конфиг из ``egress-proxy.yaml``: ``clusters: []`` + 403.

    Возвращается, когда активных egress-политик НЕТ. Байт-в-байт повторяет
    структуру манифеста (admin + listener + access_log + route_config), чтобы
    сравнение с текущим ConfigMap не давало ложный patch при первом sync.
    """
    return {
        "admin": {"address": {"socket_address": {"address": "127.0.0.1", "port_value": 9901}}},
        "static_resources": {
            "listeners": [
                {
                    "name": "egress_listener",
                    "address": {"socket_address": {"address": "0.0.0.0", "port_value": 8080}},
                    "filter_chains": [
                        {
                            "filters": [
                                {
                                    "name": "envoy.filters.network.http_connection_manager",
                                    "typed_config": {
                                        "@type": "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
                                        "stat_prefix": "plugin_egress",
                                        "http_filters": [
                                            {
                                                "name": "envoy.filters.http.router",
                                                "typed_config": {
                                                    "@type": "type.googleapis.com/envoy.extensions.filters.http.router.v3.Router",
                                                },
                                            }
                                        ],
                                        "route_config": {
                                            "name": "egress_routes",
                                            "virtual_hosts": [_deny_vhost()],
                                        },
                                        "access_log": [dict(_ENVOY_ACCESS_LOG)],
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
            "clusters": [],
        },
    }


def render_envoy_config(policies: Sequence[EgressPolicy] | None = None) -> dict[str, Any]:
    """Собрать Envoy-конфиг из ``EgressPolicy`` через генератор задачи 08.

    КЛЮЧЕВОЕ: кластеры строятся ТОЛЬКО через
    ``EgressConfigGenerator.build_envoy_routes(policy)`` (задача 08), НЕ из JWT
    и НЕ из статического ``webhooks.example.com`` (исправление v1/v3, §6.3).

    Поведение:
      * ``policies`` пуст/``None`` → стартовый fail-closed конфиг:
        ``clusters: []`` и ``direct_response 403``;
      * одна политика → её конфиг как есть (с ``_meta`` контроллера задачи 08);
      * НЕСКОЛЬКО политик (несколько активных grant'ов) → детерминированное
        ОБЪЕДИНЕНИЕ: кластеры и vhost'ы сортируются по имени, дубликаты кластеров
        склеиваются (один хост может быть разрешён двум grant'ам), финальный
        catch-all ``default_deny`` с 403 добавляется ровно один раз.

    fail-closed: у КАЖДОГО vhost (в т.ч. сгенерированного задачей 08) уже есть
    свой ``deny_route``; общий ``default_deny`` закрывает чужие ``Host``.
    Несовпадение → 403, а не «пропустить дальше».

    Результат детерминирован: одинаковый набор политик → одинаковый dict
    (порядок ключей фиксирован конструкцией, списки отсортированы).
    """
    if policies is None:
        policies = ()
    if isinstance(policies, (str, bytes)) or not isinstance(policies, Sequence):
        raise RenderError(reason="policies_not_sequence")
    for policy in policies:
        if not isinstance(policy, EgressPolicy):
            raise RenderError(reason="policy_type_invalid")

    active = tuple(sorted(policies, key=lambda p: (p.grant_id or "", p.mode.value)))
    if not active:
        return _empty_envoy_config()

    generator = EgressConfigGenerator()
    merged: dict[str, Any] | None = None
    clusters: dict[str, dict[str, Any]] = {}
    vhosts: dict[str, dict[str, Any]] = {}
    for policy in active:
        built = generator.build_envoy_routes(policy)
        # Убираем _meta из итогового envoy.yaml: это контрольная информация
        # контроллера задачи 08, а не поле конфигурации Envoy (см. generator.py).
        built.pop("_meta", None)
        if merged is None:
            merged = built
        for cluster in built["static_resources"]["clusters"]:
            clusters.setdefault(cluster["name"], cluster)
        listener = built["static_resources"]["listeners"][0]
        route_config = listener["filter_chains"][0]["filters"][0]["typed_config"]["route_config"]
        for vhost in route_config["virtual_hosts"]:
            if vhost["name"] == "default_deny":
                continue  # общий catch-all добавим один раз ниже
            existing = vhosts.get(vhost["name"])
            if existing is None:
                # Копируем, чтобы не мутировать dict генератора задачи 08.
                # deny_route пока оставляем в списке (см. нормализацию ниже).
                vhosts[vhost["name"]] = {
                    "name": vhost["name"],
                    "domains": list(vhost["domains"]),
                    "routes": list(vhost["routes"]),
                }
                continue
            # Один и тот же хост может быть разрешён двум grant'ам с РАЗНЫМИ
            # путями/методами. Склеиваем ТОЛЬКО разрешающие маршруты и
            # дедуплицируем по содержимому; deny_route переносится в КОНЕЦ
            # после нормализации (иначе Envoy first-match дал бы 403 до
            # второго разрешающего правила).
            allow_routes = [r for r in existing["routes"] if "direct_response" not in r]
            seen_routes = {json.dumps(r, sort_keys=True) for r in allow_routes}
            for route in vhost["routes"]:
                if "direct_response" in route:
                    continue
                marker = json.dumps(route, sort_keys=True)
                if marker not in seen_routes:
                    allow_routes.append(route)
                    seen_routes.add(marker)
            existing["routes"] = allow_routes

    assert merged is not None  # активные политики не пусты
    # Финальная нормализация: у КАЖДОГО allow-vhost ровно ОДИН deny_route
    # ПОСЛЕДНИМ (fail-closed для несовпадения path/method), плюс общий
    # catch-all default_deny для чужих Host.
    for vhost in vhosts.values():
        vhost["routes"] = [r for r in vhost["routes"] if "direct_response" not in r]
        vhost["routes"].append(_envoy_deny_route())
    ordered_vhosts = [vhosts[name] for name in sorted(vhosts)]
    ordered_vhosts.append(_deny_vhost())
    merged["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0]["typed_config"][
        "route_config"
    ]["virtual_hosts"] = ordered_vhosts
    merged["static_resources"]["clusters"] = [clusters[name] for name in sorted(clusters)]
    return merged


def render_envoy_yaml(config: Mapping[str, Any]) -> str:
    """Сериализовать Envoy-конфиг в YAML для ключа ``envoy.yaml`` ConfigMap'а.

    ``yaml.safe_dump(sort_keys=False)`` сохраняет порядок ключей построения и
    НЕ добавляет Python-теги; ``allow_unicode=True`` безопасен для комментариев.
    Строка ВСЕГДА заканчивается ``\\n``. В результат НЕ попадают секреты:
    конфиг содержит только allowlist хостов/путей и 403-fallback.
    """
    if not isinstance(config, Mapping):
        raise RenderError(reason="envoy_config_not_mapping")
    return yaml.safe_dump(dict(config), sort_keys=False, allow_unicode=True)


__all__ = [
    "HBA_AUTH_METHOD",
    "HBA_REJECT_ALL_LINES",
    "render_tenant_hba",
    "render_pgbouncer_userlist",
    "render_pgbouncer_userlist_preserving_admin",
    "build_envoy_policies",
    "render_envoy_config",
    "render_envoy_yaml",
]
