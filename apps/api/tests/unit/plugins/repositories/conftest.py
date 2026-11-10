"""Общие фикстуры/фейки для unit-тестов SQL-репозиториев плагинов.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze, план §0.2).

Большинство проверок не требуют реальной БД: используется ``FakeSession``
(записывает ``add``/``execute``/``commit``) и ``FakeRedis`` (эмулирует Lua
скользящего окна на Python). Это позволяет проверить fail-closed-логику,
маппинг колонок A10, CAS и идемпотентность без alembic/psql/запуска тестов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class _Row:
    """Универсальная ORM-строка-заглушка (атрибуты = колонки)."""

    data: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        try:
            return self.data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class FakeResult:
    """Имитация ``sqlalchemy.Result``."""

    def __init__(self, rows: list[Any] | None = None, rowcount: int = 1) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def scalars(self) -> "FakeResult":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeSession:
    """Минимальный ``AsyncSession``-дабл: без БД, но с честным учётом вызовов."""

    def __init__(self, *, rows: list[Any] | None = None, rowcount: int = 1) -> None:
        self.rows: list[Any] = list(rows or [])
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.statements: list[Any] = []
        self._rowcount = rowcount
        self.fail_on_commit: Exception | None = None
        self.fail_on_execute: Exception | None = None

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> FakeResult:
        self.statements.append(statement)
        if self.fail_on_execute is not None:
            raise self.fail_on_execute
        return FakeResult(self.rows, rowcount=self._rowcount)

    def add(self, instance: Any) -> None:
        self.added.append(instance)

    async def commit(self) -> None:
        if self.fail_on_commit is not None:
            raise self.fail_on_commit
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


class FakeSessionFactory:
    """``async_sessionmaker``-дабл: всегда отдаёт одну и ту же сессию."""

    def __init__(self, session: FakeSession | None = None) -> None:
        self.session = session or FakeSession()
        self.calls = 0

    def __call__(self) -> FakeSession:
        self.calls += 1
        return self.session


class FakeRedis:
    """Синхронный Redis-дабл: эмулирует оба Lua-скрипта на Python.

    ``available=False`` имитирует недоступность хранилища (все методы бросают).
    """

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.ops: dict[str, list[tuple[int, int]]] = {}
        self.counters: dict[str, int] = {}
        self.calls = 0
        self.members: list[str] = []

    def _check(self) -> None:
        if not self.available:
            raise ConnectionError("redis unavailable")

    async def eval(self, script: str, key_count: int, *args: Any) -> list[int]:
        self._check()
        self.calls += 1
        key = str(args[0])
        if "ZREMRANGEBYSCORE" in script:
            # ARGV = (now, window, amount, limit, member). Числовых — первые 4;
            # ARGV[5] `member` — НЕПРОЗРАЧНАЯ строка (уникальный id события),
            # которую реальный Redis кладёт в ZSET. Нельзя делать int(member):
            # адаптер передаёт f"{now_ms}-{id(object())}", и int() бросал
            # ValueError → RedisPluginQuota.enforce уходил в fail-closed deny
            # даже на ПЕРВЫЙ запрос. Фейк хранит окно списком (ts, amount),
            # поэтому сам member ему не нужен — он эмулирует только счётчики.
            self.members.append(str(args[5]))
            now, window, amount, limit = (int(args[i]) for i in range(1, 5))
            events = [(ts, qty) for ts, qty in self.ops.get(key, ()) if ts > now - window]
            used = sum(qty for _, qty in events)
            if used + amount > limit:
                retry = max(1, window - (now - events[0][0])) if events else window
                return [used, 0, retry]
            events.append((now, amount))
            self.ops[key] = events
            return [used + amount, 1, 0]
        amount, limit, _ttl = (int(args[i]) for i in range(1, 4))
        used = self.counters.get(key, 0)
        if used + amount > limit:
            return [used, 0]
        self.counters[key] = used + amount
        return [self.counters[key], 1]

    async def zcard(self, key: str) -> int:
        self._check()
        return sum(qty for _, qty in self.ops.get(key, ()))

    async def get(self, key: str) -> int:
        self._check()
        return self.counters.get(key, 0)


def grant_record(
    *,
    grant_id: str = "pcg_01",
    company_id: str = "co_123",
    plugin_id: str = "mod_equipment",
    status: str = "active",
    revoked_at: Any = None,
) -> Any:
    """``CapabilityGrantRecord`` задачи 12 (валидный, aware datetime)."""
    from prodavan.application.plugins.admin.contracts import (
        CapabilityGrantRecord,
        EgressPolicy,
        GrantStatus,
        QuotaSet,
    )

    return CapabilityGrantRecord(
        grant_id=grant_id,
        company_id=company_id,
        plugin_id=plugin_id,
        plugin_instance_id="pinst_01",
        manifest_version="1.2.0",
        artifact_digest=DIGEST,
        capabilities=("cache",),
        egress_policy=EgressPolicy(),
        quotas=QuotaSet(),
        status=GrantStatus(status),
        created_at=NOW,
        revoked_at=revoked_at,
    )


def grant_row(**overrides: Any) -> Any:
    """Строка ``CapabilityGrantRow`` A10 (реальные имена колонок)."""
    data: dict[str, Any] = {
        "id": "pcg_01",
        "company_id": "co_123",
        "plugin_id": "mod_equipment",
        "plugin_instance_id": "pinst_01",
        "manifest_version": "1.2.0",
        "artifact_digest": DIGEST,
        "capabilities": ["cache"],
        "egress_policy": {"mode": "none"},
        "quota": {
            "ops_per_minute": 60,
            "max_rows": 10_000,
            "max_bytes": 10_000_000,
            "max_keys": 1_000,
            "max_object_bytes": 50_000_000,
            "max_egress_bytes": 10_000_000,
        },
        "status": "active",
        "issued_at": NOW,
        "expires_at": None,
        "revoked_at": None,
        "created_by": "platform-admin",
    }
    data.update(overrides)
    return _Row(data)


__all__ = [
    "DIGEST",
    "FakeRedis",
    "FakeResult",
    "FakeSession",
    "FakeSessionFactory",
    "NOW",
    "OTHER_DIGEST",
    "grant_record",
    "grant_row",
]
