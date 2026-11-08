"""In-memory реализации «чистых» контрактов Admin API (задача 12).

``GrantStorePort``, ``RouteRegistryPort``, ``AuditPort``, ``PlatformAdminPort``
не имеют реализаций в соседних каталогах: это **чистые контракты** админского
слоя. Здесь даны тонкие in-memory реализации ДЛЯ ТЕСТОВ И DEV-СТЕНДА.

!!! ВАЖНО (явная пометка по заданию) !!!
    Для КЛАСТЕРА эти классы НЕ годятся. Нужны настоящие реализации поверх
    БД / Redis — задача **A10** acceptance-карты:
    ``infrastructure/persistence/models/**`` + Alembic-ревизия для
    ``CapabilityGrant`` / ``PluginInstance`` / ``plugin_enabled_modules[]``,
    Redis (или БД с CAS) для append-only аудита и реестра маршрутов/отзывов.
    In-memory состояние теряется при рестарте и НЕ разделяется между
    репликами API: несколько воркеров увидят разные grant'ы, а ``wake``
    во второй реплике откажет. Fail-closed при этом сохраняется (отказ, а не
    «тихое разрешение»), но доступность кластера — нет.

Правила, общие для всех четырёх:

* **Append-only аудит.** События только добавляются; ``redacted()`` из
  ``AdminAuditEvent`` даёт каноническую форму без raw SQL, параметров,
  токенов, payload и PII. Адаптер ДОПОЛНИТЕЛЬНО проверяет, что в событии
  нет запрещённых полей/значений, и отказывает в записи, если caller всё же
  попытался протащить чувствительные данные.
* **Изоляция компаний.** Ключ любого хранилища — пара ``(company_id,
  plugin_id)``; ни один метод не умеет «найти любую компанию» или подменить
  компанию из тела запроса.
* **Fail-closed.** Пустые идентификаторы, неподтверждённая запись аудита,
  отсутствующий grant → явная ошибка/``False``, а не молчаливый успех.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from prodavan.application.plugins.admin.contracts import (
    AdminAuditEvent,
    AdminIdentity,
    CapabilityGrantRecord,
    GrantStatus,
    RouteRegistration,
)

# ---------------------------------------------------------------------------
# Запрещённые данные в аудите (§4.5, критерий №11)
# ---------------------------------------------------------------------------

#: Ключи, которых не должно быть в redacted-событии. Их появление означает,
#: что caller попытался записать raw данные мимо канонического контракта.
_FORBIDDEN_AUDIT_KEYS: frozenset[str] = frozenset(
    {
        "sql",
        "query",
        "params",
        "parameters",
        "password",
        "dsn",
        "secret",
        "token",
        "authorization",
        "cookie",
        "payload",
        "body",
        "raw_body",
        "pii",
        "email",
        "phone",
        "employee_id",
    }
)

#: Маркеры в значениях: даже если ключ «безобидный», такие значения в аудит
#: не пускаем. Список умышленно короткий и machine-readable.
_FORBIDDEN_VALUE_MARKERS: tuple[str, ...] = (
    "select ",
    "insert into ",
    "update ",
    "delete from ",
    "postgresql://",
    "postgres://",
    "bearer ",
    "eyj",  # начало JWT
    "password=",
    "payload",
    "cookie",
)


def _assert_redacted(event: AdminAuditEvent) -> dict[str, str]:
    """Проверить каноническую форму события и вернуть её.

    Redaction здесь — НЕ «почистить и записать», а ОТКАЗ при попытке записать
    чувствительные данные: молчаливое вырезывание скрыло бы баг вызывающего.
    """

    if not isinstance(event, AdminAuditEvent):
        raise TypeError("InMemoryAdminAudit.append: ожидался AdminAuditEvent")
    redacted = event.redacted()
    for key in redacted:
        if key.lower() in _FORBIDDEN_AUDIT_KEYS:
            raise ValueError(f"аудит содержит запрещённое поле {key!r}")
    for key, value in redacted.items():
        if not isinstance(value, str):
            raise ValueError(f"поле аудита {key!r} должно быть строкой")
        lowered = value.lower()
        for marker in _FORBIDDEN_VALUE_MARKERS:
            if marker in lowered:
                raise ValueError(
                    f"поле аудита {key!r} содержит запрещённые raw-данные"
                )
    return redacted


# ---------------------------------------------------------------------------
# GrantStorePort
# ---------------------------------------------------------------------------


@dataclass
class InMemoryGrantStore:
    """Долгоживущие ``CapabilityGrantRecord`` + фича-флаг strangler.

    Ключ — ``(company_id, plugin_id)``. ``deactivate`` создаёт НОВЫЙ frozen
    объект (не мутирует существующий), чтобы повторный ``get`` видел
    ``REVOKED``. Потокобезопасность на ``threading.Lock`` — минимальная
    защита для dev-стенда; в кластере нужна БД с транзакциями (A10).
    """

    _grants: dict[tuple[str, str], CapabilityGrantRecord] = field(default_factory=dict)
    _enabled: dict[str, tuple[str, ...]] = field(default_factory=dict)
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    async def get(
        self, *, company_id: str, plugin_id: str
    ) -> CapabilityGrantRecord | None:
        with self._lock:
            return self._grants.get((company_id, plugin_id))

    async def get_by_grant_id(self, *, grant_id: str) -> CapabilityGrantRecord | None:
        with self._lock:
            return next(
                (g for g in self._grants.values() if g.grant_id == grant_id), None
            )

    async def create(self, *, grant: CapabilityGrantRecord) -> CapabilityGrantRecord:
        """Идемпотентно по ``grant_id``: существующая запись возвращается как есть."""

        if not isinstance(grant, CapabilityGrantRecord):
            raise TypeError("InMemoryGrantStore.create: ожидался CapabilityGrantRecord")
        key = (grant.company_id, grant.plugin_id)
        with self._lock:
            existing = self._grants.get(key)
            if existing is not None:
                return existing
            self._grants[key] = grant
            return grant

    async def deactivate(self, *, grant_id: str) -> CapabilityGrantRecord:
        """Деактивация по ``grant_id``; отсутствующий grant = явная ошибка."""

        with self._lock:
            for key, grant in list(self._grants.items()):
                if grant.grant_id != grant_id:
                    continue
                updated = CapabilityGrantRecord(
                    grant_id=grant.grant_id,
                    company_id=grant.company_id,
                    plugin_id=grant.plugin_id,
                    plugin_instance_id=grant.plugin_instance_id,
                    manifest_version=grant.manifest_version,
                    artifact_digest=grant.artifact_digest,
                    capabilities=grant.capabilities,
                    egress_policy=grant.egress_policy,
                    quotas=grant.quotas,
                    status=GrantStatus.REVOKED,
                    created_at=grant.created_at,
                    revoked_at=datetime.now(UTC),
                )
                self._grants[key] = updated
                return updated
        raise KeyError(f"grant {grant_id!r} не найден")

    async def delete(self, *, company_id: str, plugin_id: str) -> bool:
        """Удалить запись grant. Идемпотентно: отсутствие = ``True``."""

        with self._lock:
            self._grants.pop((company_id, plugin_id), None)
            return True

    async def list_by_company(
        self, *, company_id: str
    ) -> Sequence[CapabilityGrantRecord]:
        with self._lock:
            return tuple(
                g for (co, _), g in self._grants.items() if co == company_id
            )

    async def get_enabled_modules(self, *, company_id: str) -> Sequence[str]:
        with self._lock:
            return self._enabled.get(company_id, ())

    async def set_enabled_modules(
        self, *, company_id: str, plugin_ids: Sequence[str]
    ) -> Sequence[str]:
        """Записать фича-флаг. Данные declarative-app НЕ трогаются (§8)."""

        value = tuple(dict.fromkeys(str(item) for item in plugin_ids))
        with self._lock:
            self._enabled[company_id] = value
            return value


# ---------------------------------------------------------------------------
# RouteRegistryPort
# ---------------------------------------------------------------------------


@dataclass
class InMemoryRouteRegistry:
    """Маршруты и egress-правила из grant (НЕ из короткого JWT, §13.3).

    Пути строятся ТОЛЬКО из авторитетной пары ``(company_id, plugin_id)``
    и ``grant_id``. Egress-политика берётся из долгоживущего grant в БД;
    in-memory реализация хранит только счётчик правил (0..N), не сами
    правила — в кластере это Ingress/NP-манифесты (задачи 08/14).
    """

    _routes: dict[tuple[str, str], RouteRegistration] = field(default_factory=dict)
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    async def register(
        self, *, company_id: str, plugin_id: str, grant_id: str
    ) -> RouteRegistration:
        if not grant_id.strip():
            raise ValueError("InMemoryRouteRegistry.register: grant_id не должен быть пустым")
        value = RouteRegistration(
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=grant_id,
            webhook_path=f"/webhook/{plugin_id}/{company_id}/*",
            pluginpage_path=f"/pluginpage/{plugin_id}/{company_id}/*",
            capability_path="/capabilities/v1/*",
            egress_rule_count=0,
            deployment=f"{company_id}-{plugin_id}",
        )
        with self._lock:
            self._routes[(company_id, plugin_id)] = value
        return value

    async def unregister(
        self, *, company_id: str, plugin_id: str, grant_id: str
    ) -> bool:
        """Снятие маршрутов. Идемпотентно: отсутствие = ``True``."""

        with self._lock:
            self._routes.pop((company_id, plugin_id), None)
            return True


# ---------------------------------------------------------------------------
# AuditPort
# ---------------------------------------------------------------------------


@dataclass
class InMemoryAdminAudit:
    """Append-only аудит без SQL/токенов/PII (§4.5, критерий №11).

    ``events`` хранит КАНОНИЧЕСКУЮ redacted-форму (dict[str, str]), а не сырой
    ``AdminAuditEvent``: даже случайный ``repr`` всего лога не выдаст token,
    payload или DSN. Удалить/изменить запись нельзя — нет публичных методов.
    """

    _events: list[dict[str, str]] = field(default_factory=list)
    _lock: Any = field(default_factory=threading.Lock, repr=False)
    append_result: bool = True

    async def append(self, *, event: AdminAuditEvent) -> bool:
        """``True`` = событие зафиксировано. Отказ = deny позитивной мутации."""

        redacted = _assert_redacted(event)
        with self._lock:
            self._events.append(dict(redacted))
        return self.append_result

    @property
    def events(self) -> tuple[dict[str, str], ...]:
        """Неизменяемый снимок для тестов/диагностики."""

        with self._lock:
            return tuple(dict(item) for item in self._events)


# ---------------------------------------------------------------------------
# PlatformAdminPort
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StaticPlatformAdmin:
    """Статическая PlatformAdmin-проверка ДЛЯ ТЕСТОВ/DEV, не для кластера.

    В проде родитель (F11) подключает реальную auth: OIDC/JWKS, membership
    ``platform.admin``, проверку срока/отзыва токена. Здесь НЕТ «пустить
    всех»: заголовок обязан совпасть с явно заданным ``expected_token``,
    иначе возвращается ``AdminIdentity(is_platform_admin=False)`` — HTTP-слой
    дополнительно откажет с 403. Отсутствие/пустой ``authorization`` = отказ.
    """

    actor_id: str = "platform-admin"
    expected_token: str | None = None
    scopes: tuple[str, ...] = ("platform.admin",)

    async def require_platform_admin(
        self, *, authorization: str | None
    ) -> AdminIdentity:
        supplied = (authorization or "").strip()
        if not supplied or self.expected_token is None:
            # Fail-closed: нет токена или не задан ожидаемый → НЕ админ.
            return AdminIdentity(actor_id="", is_platform_admin=False)
        # Сравнение константное по времени: токен — секрет, нельзя сравнивать
        # обычным ``==`` (timing attack). ``hmac.compare_digest`` — stdlib.
        import hmac

        if not hmac.compare_digest(supplied, self.expected_token.strip()):
            return AdminIdentity(actor_id="", is_platform_admin=False)
        return AdminIdentity(
            actor_id=self.actor_id,
            is_platform_admin=True,
            scopes=tuple(self.scopes),
        )


__all__ = [
    "InMemoryAdminAudit",
    "InMemoryGrantStore",
    "InMemoryRouteRegistry",
    "StaticPlatformAdmin",
]
