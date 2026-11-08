"""``DbGrantRevocation`` — проверка отзыва поверх БД с TTL-кэшем (F1, кр. №12).

Совместим сразу с тремя контрактами:

* ``identity.revocation.RevocationPort`` (задача 02): sync
  ``is_active(grant_id, *, now: aware datetime) -> bool`` + ``revoke(grant_id)``;
* ``capabilities.ports.GrantRevocationPort`` (задача 03): тот же sync
  ``is_active(grant_id, *, now)``;
* ``admin.ports.RevocationPort`` (задача 12): async
  ``revoke(*, grant_id, reason) -> bool`` / ``is_revoked(*, grant_id) -> bool``.

Почему кэш
----------
gateway проверяет ``grant_id`` на КАЖДОМ запросе; ходить в PostgreSQL на каждый
запрос нельзя. Но ``RevocationPort`` — СИНХРОННЫЙ, а ``AsyncSession`` требует
``await``. Решение: in-memory TTL-кэш авторитетных ответов, который:

* заполняется асинхронно (:meth:`refresh` / :meth:`is_active_async`) при
  первом обращении или протухании TTL;
* sync :meth:`is_active` читает кэш; ПРОМАХ/ПРОТУХАНИЕ = ``False``
  (fail-closed), при этом выставляется флаг ``needs_refresh`` — композиция
  дёргает :meth:`refresh` в async-точке (middleware/deps);
* :meth:`revoke` инвалидирует запись НЕМЕДЛЕННО (критерий №12: старый
  ``PluginClaims`` перестаёт действовать сразу, не дожидаясь TTL);
* ошибка БД = ``False``, а не «активен по умолчанию».

Никакого «разрешить, если хранилище недоступно» — это ровно тот fail-open,
который запрещён планом миграции.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ._schema import (
    FLAG_SENTINEL_GRANT_ID,
    PluginRepositoryError,
    text,
)
from .grants import SqlCapabilityGrantRepository

DEFAULT_TTL_SECONDS = 30.0
"""TTL кэша активности. Короткий: revoke инвалидирует сразу, но и без revoke
отзыв из другой реплики/админ-процесса подхватывается не позже 30 с."""


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    active: bool
    expires_at: datetime


class DbGrantRevocation:
    """Обёртка над ``SqlCapabilityGrantRepository`` с TTL-кэшем в памяти.

    ``repository`` обязателен (fail-closed на сборке). ``ttl_seconds`` > 0.
    Потокобезопасна: gateway проверяет отзывы параллельно с admin-``revoke``.
    """

    def __init__(
        self,
        repository: SqlCapabilityGrantRepository,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Any = None,
    ) -> None:
        if not isinstance(repository, SqlCapabilityGrantRepository):
            raise PluginRepositoryError("DbGrantRevocation требует SqlCapabilityGrantRepository")
        if not isinstance(ttl_seconds, (int, float)) or isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            raise PluginRepositoryError("ttl_seconds обязан быть положительным числом")
        self._repository = repository
        self._ttl = float(ttl_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._cache: dict[str, _CacheEntry] = {}
        #: Флаг «нужен async-refresh»; снимается :meth:`refresh`.
        self._needs_refresh: bool = True
        #: Счётчик попаданий/промахов — для тестов и диагностики, не для решения.
        self._hits = 0
        self._misses = 0

    @property
    def repository(self) -> SqlCapabilityGrantRepository:
        return self._repository

    @property
    def needs_refresh(self) -> bool:
        with self._lock:
            return self._needs_refresh

    @property
    def stats(self) -> dict[str, int]:
        """Диагностика кэша (не источник решения)."""
        with self._lock:
            return {"hits": self._hits, "misses": self._misses, "size": len(self._cache)}

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PluginRepositoryError("часы DbGrantRevocation обязаны возвращать aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _normalize(grant_id: str) -> str:
        return (grant_id or "").strip()

    # ------------------------------------------------------------------
    # Sync-контракт (задачи 02 / 03)
    # ------------------------------------------------------------------

    def is_active(self, grant_id: str, *, now: datetime | None = None) -> bool:
        """Fail-closed ``GrantRevocationPort.is_active`` / ``RevocationPort.is_active``.

        ``now`` — aware UTC datetime (задача 03). Naive ``datetime`` считается
        неоднозначным → ``False``. Промах кэша = ``False`` + пометка на refresh.
        """
        key = self._normalize(grant_id)
        if not key or key == FLAG_SENTINEL_GRANT_ID:
            return False
        moment = now or self._now()
        if moment.tzinfo is None or moment.utcoffset() is None:
            # Неоднозначное время не может подтвердить активность.
            return False
        moment = moment.astimezone(UTC)
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and moment < entry.expires_at:
                self._hits += 1
                return entry.active
            self._misses += 1
            self._needs_refresh = True
        # Промах = «не знаю» = отказ. Async-refresh выполнит вызывающая сторона.
        return False

    def is_revoked(self, grant_id: str) -> bool:
        """Обратный алиас (используется F1-тестами и ``GrantRevocation``)."""
        return not self.is_active(grant_id)

    def revoke(self, grant_id: str) -> None:
        """Sync-отзыв: НЕМЕДЛЕННАЯ инвалидация кэша + деактивация в репозитории.

        Durable-запись в БД выполняется :meth:`revoke_async` (или ``flush``
        репозитория). Здесь гарантируется главное: после этого метода ни один
        sync-запрос не вернёт ``True`` для отозванного grant_id.
        """
        key = text(grant_id, field="grant_id")
        self._repository.revoke(key)  # инвалидирует снимок + ставит durable-очередь
        with self._lock:
            self._cache[key] = _CacheEntry(active=False, expires_at=self._now() + timedelta(seconds=self._ttl))
            self._needs_refresh = True

    def invalidate(self, grant_id: str | None = None) -> None:
        """Явная инвалидация одной записи или всего кэша."""
        with self._lock:
            if grant_id is None:
                self._cache.clear()
            else:
                self._cache.pop(self._normalize(grant_id), None)
            self._needs_refresh = True

    # ------------------------------------------------------------------
    # Async-источник истины
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Обновить кэш авторитетными ответами из БД (вызывать в async-точке).

        Ошибка чтения БД НЕ очищает уже известные «активные» записи молча:
        кэш помечается на повторный refresh, а все НЕизвестные grant_id
        по-прежнему дают fail-closed ``False``. Явное исключение пробрасывается,
        чтобы композиция видела деградацию, а не «всё хорошо».
        """
        await self._repository.refresh()
        moment = self._now()
        now = self._now()
        with self._lock:
            snapshot = {
                record.grant_id: _CacheEntry(
                    active=SqlCapabilityGrantRepository.record_is_active(record, now=now),
                    expires_at=moment + timedelta(seconds=self._ttl),
                )
                for record in self._repository.snapshot_records()
            }
            # Сохраняем уже закэшированные REVOKED (инвалидация при revoke не
            # должна «откатываться» чтением устаревшей реплики).
            for key, entry in list(self._cache.items()):
                if not entry.active and key not in snapshot:
                    snapshot[key] = entry
            self._cache = snapshot
            self._needs_refresh = False

    async def is_active_async(self, grant_id: str, *, now: datetime | None = None) -> bool:
        """Авторитетный async-ответ с прогревом кэша (источник истины)."""
        key = self._normalize(grant_id)
        if not key or key == FLAG_SENTINEL_GRANT_ID:
            return False
        active = await self._repository.is_active_async(key, now=now)
        with self._lock:
            self._cache[key] = _CacheEntry(
                active=active, expires_at=self._now() + timedelta(seconds=self._ttl)
            )
        return active

    async def revoke_async(self, *, grant_id: str, reason: str = "") -> bool:
        """``admin.ports.RevocationPort.revoke`` (durable + инвалидация).

        ``True`` = запись подтверждена. При ``False`` (строка не найдена) кэш
        всё равно инвалидируется — неизвестный grant не должен стать активным.
        """
        key = text(grant_id, field="grant_id")
        confirmed = await self._repository.revoke_async(key, reason=reason)
        self.revoke(key)
        return confirmed

    async def is_revoked_async(self, *, grant_id: str) -> bool:
        """``admin.ports.RevocationPort.is_revoked``."""
        return not await self.is_active_async(grant_id)

    # ------------------------------------------------------------------
    # check(claims) — совместимость с identity.GrantRevocation
    # ------------------------------------------------------------------

    def check(self, claims: Any) -> Any:
        """Отклонить токен, если его ``grant_id`` деактивирован.

        Повторяет семантику ``identity.GrantRevocation.check`` (кр. №12), но
        поверх БД. Возвращает ``claims`` для цепочки ``verify → check → allowlist``.
        """
        from prodavan.application.plugins.identity.revocation import (
            PluginTokenError,
            PluginTokenErrorCode,
        )

        grant_id = (getattr(claims, "grant_id", None) or "").strip()
        if not self.is_active(grant_id):
            raise PluginTokenError(
                code=PluginTokenErrorCode.REVOKED,
                detail="capability grant is revoked or revocation source unavailable",
            )
        return claims

    async def check_async(self, claims: Any) -> Any:
        """Async-вариант ``check`` с авторитетным ответом из БД."""
        from prodavan.application.plugins.identity.revocation import (
            PluginTokenError,
            PluginTokenErrorCode,
        )

        grant_id = (getattr(claims, "grant_id", None) or "").strip()
        if not await self.is_active_async(grant_id):
            raise PluginTokenError(
                code=PluginTokenErrorCode.REVOKED,
                detail="capability grant is revoked or revocation source unavailable",
            )
        return claims


__all__ = ["DEFAULT_TTL_SECONDS", "DbGrantRevocation"]
