"""Durable-отзыв grant'ов поверх БД (F11, стык F1/F3/A10).

``DurableGrantRevocation`` реализует ОБА порта сразу:

* ``identity.revocation.RevocationPort`` — ``is_active`` / ``revoke``
  (используется ``GrantRevocation.check(claims)`` вtransport/deps.py);
* ``capabilities.ports.GrantRevocationPort`` — ``is_active(grant_id, *, now)``
  (вызывается ``PluginCapabilityService`` на КАЖДОМ запросе);
* плюс ``is_revoked(grant_id)`` как обратный алиас для admin/``wake``.

Источник истины — ``CapabilityGrantRow.status`` (задача A10 родителя,
``ACTIVE`` / ``REVOKED``), читаемый через порт ``GrantStorePort``. Это НЕ
``InMemoryRevocation`` (задача 02 оставила её только для тестов/одного
процесса) и НЕ ``bump_pod_bridge_generation`` (механизм агентского Pod'а,
привязанный к ``pod_id`` + Redis; §1, §4.2 v4, критерий №12).

Fail-closed (обязательное требование задачи)
--------------------------------------------
* порт не передан / ``get_grant`` бросил / вернул ``None`` / вернул объект
  без понятного статуса / статус не ``ACTIVE`` / grant истёк или ещё не выдан
  -> ``is_active=False`` (закрыть доступ);
* ``revoke`` при недоступном хранилище -> ``GrantRevocationUnavailable``,
  а НЕ «тихо запомнили в кэше и продолжили»;
* in-memory кэш — ТОЛЬКО ускоритель чтения подтверждённых БД ответов. Он
  **никогда** не превращает «хранилище недоступно» в «grant активен», и
  **никогда** не кэширует неопределённость как успех. Просроченный кэш при
  недоступной БД даёт отказ, а не «переживём сбой на старом ответе».

Синхронный контракт намеренно: и ``transport/deps.py``, и
``PluginCapabilityService._grant_is_active`` вызывают его синхронно внутри
обработчика запроса. Адаптер БД (A10) предоставляет синхронный ``get_grant``;
async-обёртка — отдельная задача родителя, не этого каталога.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol, runtime_checkable

from prodavan.application.plugins.wiring.config import PluginPlatformConfigError

# Статусы ``CapabilityGrantRow.status`` (A10) и ``admin.contracts.GrantStatus``.
# Сравниваем по ``str(...).lower()``: адаптер может вернуть и StrEnum, и строку,
# и ORM-объект со своим enum — все они обязаны приводиться к этим значениям.
GRANT_STATUS_ACTIVE: Final[str] = "active"
GRANT_STATUS_REVOKED: Final[str] = "revoked"

# Значение по умолчанию: короткий TTL, чтобы отзыв доходил до всех реплик
# быстро. Это НЕ «разрешить при просрочке», а «сходить в БД ещё раз».
DEFAULT_CACHE_TTL_SECONDS: Final[float] = 1.0


class GrantRevocationUnavailable(RuntimeError):
    """Хранилище grant'ов недоступно; операция не подтверждена (fail-closed).

    Отличается от ``is_active=False``: для чтения недоступность = закрыть
    доступ, для записи (``revoke``) = нельзя сообщить администратору «готово»,
    если БД не подтвердила деактивацию (§4.5: append/positive mutation failure
    = deny/system failure, а не allow).
    """


@dataclass(frozen=True, slots=True)
class GrantSnapshot:
    """Результат чтения grant из БД (transport-neutral контракт).

    Повторяет идею ``egress.resolver.GrantLookup``, но несёт поле ``status``
    явно: для отзыва важен именно ``CapabilityGrantRow.status`` из A10, а не
    только ``egress``-политика. ``raw`` сохраняется для диагностики/ adapter'ов,
    которым нужны дополнительные поля (capabilities, expires_at и т.д.).

    ``issued_at`` / ``expires_at`` — aware UTC ``datetime`` или ``None``.
    ``revoked_at`` — для совместимости с доменным ``CapabilityGrant``.
    """

    status: str
    raw: Any = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status == GRANT_STATUS_ACTIVE


@runtime_checkable
class GrantStorePort(Protocol):
    """Порт хранилища ``CapabilityGrantRow`` (реализация — A10/родитель).

    Синхронный ``get_grant`` возвращает:

    * ``GrantSnapshot`` (или любой объект с атрибутами/ключами ``status``,
      ``issued_at``, ``expires_at``, ``revoked_at``),
    * ``None`` — grant не найден => отказ,
    * исключение — хранилище недоступно => отказ.

    ``deactivate`` подтверждает запись ``status=REVOKED``; ``False``/исключение
    = отзыв НЕ подтверждён. Никаких «тихих» успехов без БД.
    """

    def get_grant(self, grant_id: str) -> GrantSnapshot | Any | None: ...

    def deactivate(self, grant_id: str) -> bool: ...


def _attr(source: Any, name: str, default: Any = None) -> Any:
    """Прочитать атрибут или ключ mapping (ORM-объект / dataclass / dict)."""
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _aware_utc(value: Any) -> datetime | None:
    """Привести метку времени к aware UTC; неоднозначное -> отказ (``None``-ошибка)."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        # Строка/число из БД без парсинга не принимаем: это неопределённость.
        raise ValueError("grant timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        # Naive datetime = нельзя сравнивать с aware ``now`` => fail-closed.
        raise ValueError("grant timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _coerce_snapshot(value: Any) -> GrantSnapshot:
    """Нормализовать ответ порта в ``GrantSnapshot`` (fail-closed)."""
    if isinstance(value, GrantSnapshot):
        snapshot = value
    else:
        if value is None:
            raise LookupError("grant not found")
        status = _attr(value, "status")
        if status is None:
            raise ValueError("grant store returned an object without status")
        snapshot = GrantSnapshot(
            status=str(status).strip().lower(),
            raw=value,
            issued_at=_aware_utc(_attr(value, "issued_at")),
            expires_at=_aware_utc(_attr(value, "expires_at")),
            revoked_at=_aware_utc(_attr(value, "revoked_at")),
        )
    # Даже готовый GrantSnapshot мог прийти с произвольным регистром/типом.
    normalized = GrantSnapshot(
        status=str(snapshot.status).strip().lower(),
        raw=snapshot.raw,
        issued_at=_aware_utc(snapshot.issued_at),
        expires_at=_aware_utc(snapshot.expires_at),
        revoked_at=_aware_utc(snapshot.revoked_at),
    )
    if normalized.status not in (GRANT_STATUS_ACTIVE, GRANT_STATUS_REVOKED):
        # Неизвестный статус = неопределённость = отказ, НЕ «считаем активным».
        raise ValueError(f"unsupported grant status: {normalized.status!r}")
    return normalized


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    """Подтверждённый БД ответ с ограниченным сроком годности."""

    active: bool
    expires_at: datetime


class DurableGrantRevocation:
    """Проверка активности grant'а по ``CapabilityGrantRow.status`` из БД.

    Удовлетворяет структурно и ``identity.RevocationPort``, и
    ``capabilities.GrantRevocationPort``, и ``transport.deps.RevocationLike``
    (у последнего есть ``check`` — см. метод ниже). Именно её родитель кладёт
    в ``app.state.plugin_revocation``: ``GrantRevocation`` задачи 02 при
    желании можно навесить сверху через ``bind``, но дублировать in-memory
    источник истины в кластере запрещено.

    ``now`` в ``is_active`` обязан быть aware ``datetime``; ``None`` = UTC «сейчас».
    Naive ``datetime`` -> ``False`` (fail-closed).
    """

    def __init__(
        self,
        store: GrantStorePort | None,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        clock: Any = None,
    ) -> None:
        if store is None:
            # Нет интеграции = ЯВНАЯ ошибка композиции, а не «in-memory allow-all».
            raise PluginPlatformConfigError(
                "DurableGrantRevocation requires a GrantStorePort "
                "(CapabilityGrantRow.status from A10); no in-memory fallback"
            )
        if cache_ttl_seconds < 0 or cache_ttl_seconds != cache_ttl_seconds:  # NaN
            raise PluginPlatformConfigError("cache_ttl_seconds must be a non-negative finite number")
        self._store = store
        self._cache_ttl = timedelta(seconds=float(cache_ttl_seconds))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ часы
    def _now(self, now: datetime | None) -> datetime:
        if now is None:
            moment = self._clock()
            if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
                raise GrantRevocationUnavailable("revocation clock returned a naive datetime")
            return moment.astimezone(UTC)
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            # Неоднозначное время = отказ (fail-closed).
            raise ValueError("now must be a timezone-aware datetime")
        return now.astimezone(UTC)

    # --------------------------------------------------------------- кэш/БД
    @staticmethod
    def _normalize(grant_id: str) -> str:
        return (grant_id or "").strip() if isinstance(grant_id, str) else ""

    def _cache_get(self, key: str, now: datetime) -> bool | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if now >= entry.expires_at:
                # Просроченный кэш НЕ является источником решения: удаляем и
                # идём в БД. При недоступной БД это даст отказ, не allow.
                self._cache.pop(key, None)
                return None
            return entry.active

    def _cache_put(self, key: str, active: bool, now: datetime) -> None:
        with self._lock:
            self._cache[key] = _CacheEntry(active=active, expires_at=now + self._cache_ttl)

    def _read_store(self, key: str, now: datetime) -> bool:
        """Сходить в БД и вернуть ``True`` ТОЛЬКО при подтверждённом ACTIVE."""
        snapshot = _coerce_snapshot(self._store.get_grant(key))
        if not snapshot.active or snapshot.revoked_at is not None:
            return False
        if snapshot.issued_at is not None and now < snapshot.issued_at:
            return False
        if snapshot.expires_at is not None and now >= snapshot.expires_at:
            return False
        return True

    # --------------------------------------------------------------- порт F2
    def is_active(self, grant_id: str, *, now: datetime | None = None) -> bool:
        """Fail-closed проверка ``grant_id`` (контракт задачи 03).

        Любая неопределённость (нет порта, пустой id, naive ``now``, исключение
        хранилища, grant не найден/неизвестный статус/отозван/истёк) даёт
        ``False``. ``PluginCapabilityService`` превращает это в
        ``GRANT_REVOKED``/403, ``transport.deps`` — в 403.
        """
        key = self._normalize(grant_id)
        if not key:
            return False
        try:
            moment = self._now(now)
        except (ValueError, GrantRevocationUnavailable):
            return False

        cached = self._cache_get(key, moment)
        if cached is not None:
            # Кэшируется и положительный, и отрицательный подтверждённый ответ.
            return cached

        try:
            active = self._read_store(key, moment)
        except Exception:
            # Хранилище недоступно ≠ «grant активен». НИКАКОГО «возьмём из
            # кэша прошлый успех»: просроченный кэш уже удалён выше.
            return False
        self._cache_put(key, active, moment)
        return active

    # --------------------------------------------------------------- порт F1
    def is_revoked(self, grant_id: str) -> bool:
        """Обратный алиас (fail-closed: ``not is_active``)."""
        return not self.is_active(grant_id)

    def revoke(self, grant_id: str) -> None:
        """Деактивировать grant в БД (``status=REVOKED``) + сбросить кэш.

        НЕ использует ``bump_pod_bridge_generation``: отзыв плагина привязан к
        ``grant_id`` в ``CapabilityGrantRow`` (§4.2, критерий №12). Если БД не
        подтвердила запись — бросаем ``GrantRevocationUnavailable``: тихо
        «запомнить в кэше» было бы молчаливым успехом без durable-источника.
        """
        key = self._normalize(grant_id)
        if not key:
            raise ValueError("grant_id must not be empty")
        try:
            confirmed = bool(self._store.deactivate(key))
        except Exception as exc:
            self._cache_pop(key)
            raise GrantRevocationUnavailable(
                f"grant store is unavailable; revoke of {key!r} is NOT confirmed"
            ) from exc
        if not confirmed:
            self._cache_pop(key)
            raise GrantRevocationUnavailable(
                f"grant store did not confirm deactivation of {key!r}"
            )
        # Подтверждено БД: фиксируем отрицательный ответ в кэше, чтобы даже
        # in-flight токен с этим grant_id был отклонён немедленно.
        try:
            moment = self._now(None)
        except (ValueError, GrantRevocationUnavailable):
            self._cache_pop(key)
        else:
            self._cache_put(key, False, moment)

    def check(self, claims: Any) -> Any:
        """Совместимость с ``transport.deps.RevocationLike.check(claims)``.

        Позволяет положить этот объект в ``app.state.plugin_revocation`` напрямую,
        без обёртки ``GrantRevocation`` задачи 02. Отказ -> ``PluginTokenError``
        с кодом ``REVOKED`` (ровно тот код, который ``deps`` маппит в 403).
        """
        from prodavan.application.plugins.identity.types import (
            PluginTokenError,
            PluginTokenErrorCode,
        )

        grant_id = _attr(claims, "grant_id")
        if not self.is_active(grant_id if isinstance(grant_id, str) else ""):
            raise PluginTokenError(
                code=PluginTokenErrorCode.REVOKED,
                detail="capability grant is revoked or grant store unavailable",
            )
        return claims

    # ------------------------------------------------------------- диагностика
    def _cache_pop(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    @property
    def cached_ids(self) -> frozenset[str]:
        """Только для тестов/диагностики, НЕ источник решения."""
        with self._lock:
            return frozenset(self._cache)


__all__ = [
    "DEFAULT_CACHE_TTL_SECONDS",
    "GRANT_STATUS_ACTIVE",
    "GRANT_STATUS_REVOKED",
    "DurableGrantRevocation",
    "GrantRevocationUnavailable",
    "GrantSnapshot",
    "GrantStorePort",
]
