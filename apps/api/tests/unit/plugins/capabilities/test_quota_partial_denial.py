"""Отказ по одной размерности не должен съедать уже списанный OPS.

``_CoreQuota.allows`` перебирает размерности и вызывает ``enforce`` на каждой.
Раньше отказ по ROWS/BYTES оставлял уже разрешённый OPS списанным, поэтому
плагин, стабильно упирающийся в лимит строк, за несколько запросов сам себя
запирал в 429 — отказ превращался в расход.
"""

from __future__ import annotations

from datetime import UTC, datetime

from prodavan.application.plugins.capabilities.quotas import (
    InMemoryQuota,
    PluginQuota,
    PluginQuotaKey,
    QuotaOp,
    QuotaUsage,
)
from prodavan.application.plugins.capabilities.service import _CoreQuota

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


class _Meter:
    """Адаптер, который всегда сообщает расход сверх лимита строк."""

    def __init__(self, usage: QuotaUsage) -> None:
        self._usage = usage

    def quota_usage(self, request: object) -> QuotaUsage:
        return self._usage


class _Registry:
    def __init__(self, adapter: object) -> None:
        self._adapter = adapter

    def resolve(self, capability: str) -> object:
        return self._adapter


def _key() -> PluginQuotaKey:
    return PluginQuotaKey(
        company_id="co_x", plugin_instance_id="inst_x", artifact_digest=DIGEST,
    )


async def test_rows_denial_does_not_consume_the_ops_budget() -> None:
    quota = InMemoryQuota(now=NOW)
    meter = _Meter(QuotaUsage(ops=1, rows=5, bytes=0, keys=0, object_bytes=0, egress_bytes=0))
    core = _CoreQuota(
        quota=quota,
        key=_key(),
        limits=PluginQuota(ops_per_minute=3, max_rows=2),
        registry=_Registry(meter),  # type: ignore[arg-type]
        capability="docs",
        request=object(),
        adapter_present=True,
    )

    # Три отказа подряд: раньше они бы исчерпали лимит ops (3/мин).
    for _ in range(3):
        assert await core.allows(request=None) is False  # type: ignore[arg-type]

    # Бюджет OPS должен остаться нетронутым отказами.
    decision = await quota.enforce(_key(), QuotaOp.OPS, 0, PluginQuota(ops_per_minute=3, max_rows=2))
    assert decision.used == 0
