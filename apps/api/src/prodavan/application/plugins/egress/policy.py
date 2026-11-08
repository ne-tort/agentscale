"""Egress-политика из ``CapabilityGrant`` (v4, §4.2, §6.1, §6.3).

Источник истины — **долгоживущий grant в БД**, а не краткоживущий JWT: токен несёт
только ``grant_id``. Иначе истечение TTL пересобирало бы сетевые правила (кластеры
Envoy, NetworkPolicy) посреди активного запроса (критерий 22, §13.3).

Fail-closed: ``none`` запрещает всё; ``webhooks-only`` требует непустого allowlist
валидных публичных DNS-имён; ``full`` требует явного флага ``approved``. Любая
неоднозначность (wildcard, частный IP, metadata-адрес, битая строка) = отказ.

Публичный контракт: ``EgressMode``, ``EgressRule``, ``EgressPolicy``,
``resolve_policy(grant)``, ``match(rule, host, path, method)``, ``validate_host``,
``validate_rule_hosts``, ``is_host_allowed``.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence

from prodavan.application.plugins.egress.errors import EgressPolicyError

#: Явный флаг approval для режима ``full``. Имя совпадает с формулировкой §6.3
#: («любой egress, но с обязательным approval») и проверяется в ``resolve_policy``.
APPROVED_FLAG = "approved"

#: Link-local metadata-сервисы облаков. Даже если ``ipaddress`` считает адрес
#: «не глобальным», проверяем явно: это самый частый SSRF-вектор.
_METADATA_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal", "metadata", "metadata.goog"})

#: RFC 1123 hostname: метки 1..63, буквы/цифры/дефис, без ведущего/ведомого дефиса,
#: без подчёркиваний, без пробелов, без ``user@``, без ``:port``. Точка на конце
#: (FQDN-root) нормализуется, но «голый» wildcard запрещён отдельно.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(?:\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$"
)

#: Wildcard в любом виде (``*``, ``*.example.com``, ``hooks.*``) запрещён при
#: ``webhooks-only``: allowlist должен быть исчерпывающим, иначе «разрешить всё»
#: достигается одной строкой.
_WILDCARD_RE = re.compile(r"\*")

#: Допустимые HTTP-методы. Сравнение регистронезависимое, но значение нормализуется
#: к верхнему регистру ещё на этапе сборки правил.
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class EgressMode(StrEnum):
    """Регулируемый egress (§6.3)."""

    NONE = "none"
    WEBHOOKS_ONLY = "webhooks-only"
    FULL = "full"

    @classmethod
    def parse(cls, value: Any) -> EgressMode:
        """Разобрать режим; отсутствие/опечатка = отказ, а не ``none``-фолбэк.

        Осознанно НЕ подставляем ``none`` при неизвестном значении: «неизвестно» —
        это ошибка конфигурации, которую надо увидеть, а не тихо сузить доступ.
        """
        if isinstance(value, EgressMode):
            return value
        if not isinstance(value, str) or not value.strip():
            raise EgressPolicyError(
                "egress mode is missing or not a string",
                reason="mode_missing",
            )
        raw = value.strip()
        try:
            return cls(raw)
        except ValueError as exc:
            raise EgressPolicyError(
                f"unknown egress mode: {raw!r}",
                reason="mode_unknown",
            ) from exc


@dataclass(frozen=True, slots=True)
class EgressRule:
    """Одно разрешающее правило: хост + префикс пути + метод.

    ``path_prefix`` сравнивается как префикс **нормализованного** пути, а не как
    regex/wildcard: ``/services`` не должно пускать ``/servicesevil``. См. ``match``.
    """

    host: str
    path_prefix: str = "/"
    method: str = "GET"

    def __post_init__(self) -> None:
        # Нормализация через object.__setattr__ — dataclass заморожен.
        host = self.host.strip().lower().rstrip(".")
        object.__setattr__(self, "host", host)
        if not host:
            raise EgressPolicyError("egress rule host must not be empty", reason="host_empty")
        if _WILDCARD_RE.search(host):
            raise EgressPolicyError(
                f"wildcard host is not allowed in an egress rule: {host!r}",
                reason="host_wildcard",
            )

        prefix = self.path_prefix.strip() or "/"
        if not prefix.startswith("/"):
            raise EgressPolicyError(
                f"egress path_prefix must start with '/': {prefix!r}",
                reason="path_invalid",
            )
        # ``//`` и ``..`` в префиксе создают неоднозначность при сравнении с
        # нормализованным путём запроса — запрещаем на этапе конфигурации.
        if "//" in prefix or ".." in prefix or "?" in prefix or "#" in prefix:
            raise EgressPolicyError(
                f"egress path_prefix is ambiguous: {prefix!r}",
                reason="path_invalid",
            )
        object.__setattr__(self, "path_prefix", prefix)

        method = self.method.strip().upper()
        if method not in _ALLOWED_METHODS:
            raise EgressPolicyError(
                f"egress rule method is not supported: {method!r}",
                reason="method_invalid",
            )
        object.__setattr__(self, "method", method)


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """Итоговая политика, полученная из grant (не из токена).

    ``rules`` — развёрнутый список (хост × путь × метод), используемый и генератором
    Envoy-кластеров, и ``EgressService``. Пустой ``rules`` при ``none`` — ожидаемое
    состояние: правил нет, всё запрещено.
    """

    mode: EgressMode
    rules: tuple[EgressRule, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    max_egress_bytes: int = 0
    grant_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, EgressMode):
            raise EgressPolicyError("egress policy mode must be an EgressMode", reason="mode_invalid")
        if self.max_egress_bytes < 0:
            raise EgressPolicyError("max_egress_bytes must be >= 0", reason="quota_invalid")
        # Fail-closed: невалидные данные запрещено «протаскивать» в политику.
        validate_rule_hosts(self.rules)
        for host in self.allowed_hosts:
            validate_host(host, allow_wildcard=False)

    @property
    def allows_any(self) -> bool:
        """Есть ли хоть одно разрешающее правило (``none`` / пустой allowlist)."""
        return bool(self.rules)

    @property
    def is_full(self) -> bool:
        return self.mode is EgressMode.FULL


def _as_mapping(grant: Any) -> Mapping[str, Any]:
    """Привести grant к mapping, не зависимому от ORM-реализации (только чтение).

    Поддерживаются: объект с ``.egress`` (dict/dataclass), self-описывающий dict,
    ``model_dump()``/``dict()`` (Pydantic), dataclass через ``__dict__``. Ничего не
    импортируется из ``domain/**`` или ``infra/**``: контур владеет только своим
    каталогом и читает контракт по structurally-typed протоколу.
    """
    if grant is None:
        raise EgressPolicyError("capability grant is missing", reason="grant_missing")
    if isinstance(grant, Mapping):
        return grant
    model_dump = getattr(grant, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dumped
    as_dict = getattr(grant, "dict", None)
    if callable(as_dict):
        dumped = as_dict()
        if isinstance(dumped, Mapping):
            return dumped
    raw = getattr(grant, "__dict__", None)
    if isinstance(raw, Mapping):
        return raw
    raise EgressPolicyError(
        "capability grant has no readable egress contract",
        reason="grant_unreadable",
    )


def _egress_section(grant: Mapping[str, Any]) -> Mapping[str, Any]:
    """Достать секцию ``egress``; при отсутствии — пустая (=> режим ``none``).

    Отсутствие секции НЕ ошибка: это легальный «egress выключен». Ошибка — секция
    есть, но не mapping (тогда нельзя доказать её безопасность).
    """
    section = grant.get("egress")
    if section is None:
        # Альтернатива для плоских grant-объектов, где поля лежат в корне.
        if any(key in grant for key in ("mode", "allowed_hosts", "allowed_paths", "max_egress_bytes", APPROVED_FLAG)):
            return grant
        return {}
    if not isinstance(section, Mapping):
        raise EgressPolicyError("grant.egress must be an object", reason="policy_invalid")
    return section


def _string_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    """Нормализовать список строк; ``None`` -> пустой кортеж."""
    if value is None:
        return ()
    if isinstance(value, str):
        # Строка вместо списка — частая ошибка конфигурации; не «разворачиваем» её
        # по символам, а принимаем как единственный элемент, но явно проверяем ниже.
        items: Sequence[Any] = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = value
    else:
        raise EgressPolicyError(
            f"grant.egress.{field_name} must be a list of strings",
            reason="policy_invalid",
        )
    result: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise EgressPolicyError(
                f"grant.egress.{field_name} contains an empty or non-string entry",
                reason="policy_invalid",
            )
        result.append(item.strip())
    return tuple(result)


def _coerce_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise EgressPolicyError("grant.egress.max_egress_bytes must be an integer", reason="quota_invalid")
    return value


def _explicit_approved(section: Mapping[str, Any], grant: Mapping[str, Any]) -> bool:
    """Явный флаг ``approved`` для режима ``full``.

    Флаг ищется и в секции ``egress``, и в корне grant (на случай плоской схемы), но
    должен быть **булевым ``True``**: строка ``"true"``, ``1``, список или отсутствие
    значения approval'ом не считаются. Иначе «почти approval» расширяет egress.
    """
    raw = section.get(APPROVED_FLAG, grant.get(APPROVED_FLAG) if grant is not section else None)
    return raw is True


def resolve_policy(grant: Any) -> EgressPolicy:
    """Построить ``EgressPolicy`` из ``CapabilityGrant`` в БД.

    Валидация (все условия — fail-closed, §6.3, критерии 8/22):

    * ``none`` — правил нет, ``allowed_hosts`` игнорируется (но не ошибочен);
    * ``webhooks-only`` — **обязан** иметь непустой allowlist, запрещены wildcard,
      невалидный DNS, частные/loopback/link-local/metadata адреса;
    * ``full`` — **обязан** иметь явный ``approved: true``; при этом правила всё равно
      строятся из allowlist, если он задан (квота байт и аудит сохраняются).

    ``grant_id`` забирается из grant для аудита. Никакого чтения ``egress`` из токена
    здесь нет и быть не может: функция принимает только grant.
    """
    root = _as_mapping(grant)
    section = _egress_section(root)

    mode_raw = section.get("mode", EgressMode.NONE)
    mode = EgressMode.parse(mode_raw)

    hosts = _string_tuple(section.get("allowed_hosts"), field_name="allowed_hosts")
    paths = _string_tuple(section.get("allowed_paths"), field_name="allowed_paths")
    for prefix in paths:
        if not prefix.startswith("/") or "//" in prefix or ".." in prefix or "?" in prefix or "#" in prefix:
            raise EgressPolicyError(
                f"grant.egress.allowed_paths contains an invalid prefix: {prefix!r}",
                reason="path_invalid",
            )

    max_bytes = _coerce_int(section.get("max_egress_bytes"), default=0)
    if max_bytes < 0:
        raise EgressPolicyError("max_egress_bytes must be >= 0", reason="quota_invalid")

    raw_grant_id = root.get("grant_id")
    grant_id = raw_grant_id.strip() if isinstance(raw_grant_id, str) and raw_grant_id.strip() else None

    if mode is EgressMode.NONE:
        # Строгий режим: даже если в grant по ошибке лежат хосты, правила не строим.
        return EgressPolicy(
            mode=mode,
            rules=(),
            allowed_hosts=(),
            allowed_paths=(),
            max_egress_bytes=max_bytes,
            grant_id=grant_id,
        )

    if mode is EgressMode.FULL and not _explicit_approved(section, root):
        raise EgressPolicyError(
            "egress mode 'full' requires an explicit approved=true flag",
            reason="approval_missing",
        )

    if not hosts:
        raise EgressPolicyError(
            "egress mode 'webhooks-only' requires a non-empty allowed_hosts allowlist"
            if mode is EgressMode.WEBHOOKS_ONLY
            else "egress mode 'full' requires a non-empty allowed_hosts allowlist",
            reason="allowlist_empty",
        )

    # Валидация хостов ДО сборки правил: ни один небезопасный хост не должен попасть
    # ни в Envoy-кластеры, ни в match().
    normalized_hosts: list[str] = []
    for host in hosts:
        normalized_hosts.append(validate_host(host, allow_wildcard=False))

    effective_paths = paths or ("/",)
    rules = tuple(
        EgressRule(host=host, path_prefix=prefix, method=method)
        for host in normalized_hosts
        for prefix in effective_paths
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
    )

    return EgressPolicy(
        mode=mode,
        rules=rules,
        allowed_hosts=tuple(normalized_hosts),
        allowed_paths=tuple(effective_paths),
        max_egress_bytes=max_bytes,
        grant_id=grant_id,
    )


def validate_host(host: str, *, allow_wildcard: bool = False) -> str:
    """Проверить и нормализовать хост (защита от SSRF / подмены ``Host``).

    Запрещено:

    * wildcard ``*`` в любом виде (при ``allow_wildcard=False``);
    * не-DNS строки: схема, порт, ``user@``,backslash, пробелы, подчёркивания,
      пустые/длинные метки, ведущий/ведомый дефис;
    * IP-литералы (IPv4/IPv6): ``127.0.0.1``, ``10.0.0.1``, ``::1``, ``[::1]``,
      а также decimal/octal/hex-формы IPv4, которые ``ipaddress`` не ловит;
    * частные, loopback, link-local, reserved, multicast, unspecified адреса;
    * ``169.254.169.254`` и прочие cloud metadata-имена;
    * ``localhost``, ``*.localhost``, ``*.internal``, ``*.local``, одноуровневые
      имена без точки (внутренние service-имена k8s).

    Возвращает нормализованный lowercase-хост без завершающей точки.
    """
    if not isinstance(host, str) or not host.strip():
        raise EgressPolicyError("host must be a non-empty string", reason="host_empty")
    candidate = host.strip().lower().rstrip(".")
    if not candidate:
        raise EgressPolicyError("host must be a non-empty string", reason="host_empty")

    if not allow_wildcard and _WILDCARD_RE.search(candidate):
        raise EgressPolicyError(
            f"wildcard host is forbidden: {candidate!r}",
            reason="host_wildcard",
        )
    if allow_wildcard and candidate == "*":
        # Даже в «разрешающем» режиме одиночный ``*`` не валиден: это не DNS-имя.
        raise EgressPolicyError("bare '*' is not a valid host", reason="host_wildcard")

    # Защита от ``http://``, ``host:port``, ``user@host``, путей, backslash и т.п.
    if any(marker in candidate for marker in ("/", "\\", ":", "@", "?", "#", " ", "%")):
        raise EgressPolicyError(
            f"host contains forbidden characters: {candidate!r}",
            reason="host_invalid",
        )
    if not _HOSTNAME_RE.match(candidate):
        raise EgressPolicyError(
            f"host is not a valid DNS name: {candidate!r}",
            reason="host_invalid",
        )
    if "." not in candidate:
        raise EgressPolicyError(
            f"single-label host is not allowed: {candidate!r}",
            reason="host_internal",
        )
    if candidate in _METADATA_HOSTS or any(
        candidate.endswith("." + suffix) for suffix in ("internal", "localhost", "local")
    ):
        raise EgressPolicyError(
            f"internal or metadata host is not allowed: {candidate!r}",
            reason="host_internal",
        )
    # ``.internal``/``.local``/``.localhost`` как сам TLD без префикса уже отсечены
    # проверкой ``endswith`` выше для составных имён; одноуровневые — правилом выше.
    if candidate.startswith("metadata.") or candidate == "localhost":
        raise EgressPolicyError(
            f"metadata host is not allowed: {candidate!r}",
            reason="host_metadata",
        )

    # Явно разбираем IP-литералы (включая IPv6 без скобок) до DNS-ветки.
    _reject_ip_literal(candidate)
    return candidate


def _reject_ip_literal(candidate: str) -> None:
    """Отклонить IP-литерал и «замаскированные» формы IPv4.

    ``ipaddress.ip_address`` ловит канонические формы, но не ``2130706433``,
    ``0177.0.0.1`` или ``0x7f000001`` — их резолвер ОС всё равно превратит в
    loopback. Такие строки не проходят DNS-regex (нет точек/не DNS), но проверка
    оставлена явной и для documenting-целей, и на случай ослабления regex.
    """
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        address = None
    if address is not None:
        _raise_for_ip(address, candidate)

    # Десятичная / hex-форма IPv4 (одно число) и octal-метки уже не проходят
    # ``_HOSTNAME_RE``? Проходят (это цифры и точки) — проверяем дополнительно.
    if _looks_like_numeric_ipv4(candidate):
        try:
            address = ipaddress.ip_address(_numeric_ipv4_to_dotted(candidate))
        except (ValueError, OverflowError):
            raise EgressPolicyError(
                f"numeric IP literal is not allowed: {candidate!r}",
                reason="host_ip_literal",
            ) from None
        _raise_for_ip(address, candidate)


def _looks_like_numeric_ipv4(candidate: str) -> bool:
    """Похоже ли имя на числовую (в т.ч. octal/hex) форму IPv4."""
    if re.fullmatch(r"\d+", candidate):
        return True
    labels = candidate.split(".")
    if not 2 <= len(labels) <= 4:
        return False
    return all(re.fullmatch(r"0[xX][0-9a-fA-F]+|0[0-7]*|\d+", label or "x") for label in labels)


def _numeric_ipv4_to_dotted(candidate: str) -> str:
    """Привести ``2130706433`` / ``0177.0.0.1`` / ``0x7f.1`` к dotted-quad."""
    if re.fullmatch(r"\d+", candidate):
        value = int(candidate, 10)
        if value > 0xFFFFFFFF:
            raise ValueError("out of range")
        return str(ipaddress.IPv4Address(value))

    parts: list[str] = []
    for label in candidate.split("."):
        if label.lower().startswith("0x"):
            value = int(label, 16)
        elif label.startswith("0") and len(label) > 1:
            value = int(label, 8)
        else:
            value = int(label, 10)
        if not 0 <= value <= 255:
            raise ValueError("octet out of range")
        parts.append(str(value))
    while len(parts) < 4:
        # Короткая форма (``127.1``) — резолверы трактуют как ``127.0.0.1``.
        parts.append("0")
    return ".".join(parts)


def _raise_for_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address, candidate: str) -> None:
    """Запретить любые не-глобальные адреса (SSRF, metadata, loopback)."""
    if address.is_private or address.is_loopback or address.is_link_local:
        raise EgressPolicyError(
            f"private/loopback/link-local address is not allowed: {candidate!r}",
            reason="host_private",
        )
    if address.is_reserved or address.is_multicast or address.is_unspecified:
        raise EgressPolicyError(
            f"reserved/multicast/unspecified address is not allowed: {candidate!r}",
            reason="host_private",
        )
    if not address.is_global:
        raise EgressPolicyError(
            f"non-global address is not allowed: {candidate!r}",
            reason="host_private",
        )
    # Публичный IP-литерал тоже запрещён: allowlist — это DNS-имена, а обход по IP
    # (§6.3 «защита от обхода по IP») должен быть невозможен.
    raise EgressPolicyError(
        f"IP literals are not allowed in the egress allowlist: {candidate!r}",
        reason="host_ip_literal",
    )


def validate_rule_hosts(rules: Sequence[EgressRule]) -> None:
    """Повторно проверить хосты уже собранных правил (defence in depth)."""
    for rule in rules:
        if not isinstance(rule, EgressRule):
            raise EgressPolicyError("egress rules must be EgressRule instances", reason="policy_invalid")
        if validate_host(rule.host, allow_wildcard=False) != rule.host:
            raise EgressPolicyError(
                f"egress rule host is not normalized: {rule.host!r}",
                reason="host_invalid",
            )


def normalize_path(path: str) -> str:
    """Нормализовать путь запроса для сравнения с ``path_prefix``.

    Отсекаем query/fragment, добавляем ведущий ``/``, убираем дублирующиеся ``/`` и
    разрешаем ``.``/``..`` **только вниз**, не давая выйти выше корня: иначе
    ``/services/../admin`` мог бы обойти префиксную проверку.
    """
    if not isinstance(path, str):
        raise EgressPolicyError("path must be a string", reason="path_invalid")
    raw = path.split("?", 1)[0].split("#", 1)[0].strip()
    if not raw:
        raw = "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    # Убираем ``\\`` (Windows-подобный разделитель) — частый обход нормализации.
    raw = raw.replace("\\", "/")
    segments: list[str] = []
    for segment in raw.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)
    normalized = "/" + "/".join(segments)
    return normalized if normalized != "/" else "/"


def normalize_host(host: str) -> str:
    """Нормализовать входящий ``Host`` (без валидации безопасности).

    Здесь только приведение вида: lowercase, обрезка trailing dot и ``:port``.
    Проверка безопасности выполняется отдельно (``validate_host``) — в том числе для
    финального resolved host после редиректов/DNS-rebinding (R-10, ``service.py``).
    """
    if not isinstance(host, str) or not host.strip():
        raise EgressPolicyError("host must be a non-empty string", reason="host_empty")
    candidate = host.strip().lower().rstrip(".")
    if candidate.startswith("["):
        # IPv6 в скобках: ``[::1]:443`` -> ``::1`` (дальше ``validate_host`` откажет).
        end = candidate.find("]")
        candidate = candidate[1:end] if end > 0 else candidate[1:]
    elif ":" in candidate:
        candidate = candidate.rsplit(":", 1)[0]
    return candidate.rstrip(".")


def match(rule: EgressRule, host: str, path: str, method: str) -> bool:
    """Совпадает ли запрос с правилом (хост + префикс пути + метод).

    * хост — **точное** совпадение после нормализации (никаких suffix/wildcard:
      ``evil-hooks.slack.com`` и ``hooks.slack.com.evil.com`` не проходят);
    * путь — префикс по **сегментам**: ``/services`` пускает ``/services`` и
      ``/services/xxx``, но не ``/servicesevil``;
    * метод — точное совпадение в верхнем регистре.

    Функция никогда не «разрешает по умолчанию»: любая ошибка нормализации
    (битая строка, ``Host`` с запрещёнными символами) даёт ``False``.
    """
    if not isinstance(rule, EgressRule):
        return False
    try:
        candidate_host = normalize_host(host)
        candidate_path = normalize_path(path)
    except EgressPolicyError:
        return False
    if not candidate_host or not isinstance(method, str):
        return False
    if candidate_host != rule.host:
        return False
    if method.strip().upper() != rule.method:
        return False
    if rule.path_prefix == "/":
        return True
    # Сравнение по границе сегмента: префикс должен закончиться ровно перед ``/``.
    return candidate_path == rule.path_prefix or candidate_path.startswith(rule.path_prefix.rstrip("/") + "/")


def is_host_allowed(policy: EgressPolicy, host: str) -> bool:
    """Входит ли хост в allowlist политики (для финального resolved host, R-10)."""
    try:
        candidate = normalize_host(host)
    except EgressPolicyError:
        return False
    return candidate in policy.allowed_hosts


__all__ = [
    "APPROVED_FLAG",
    "EgressMode",
    "EgressPolicy",
    "EgressRule",
    "is_host_allowed",
    "match",
    "normalize_host",
    "normalize_path",
    "resolve_policy",
    "validate_host",
    "validate_rule_hosts",
]
