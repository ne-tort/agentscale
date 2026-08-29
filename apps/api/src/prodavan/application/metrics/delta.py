"""Delta gating for pod metric samples."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from prodavan.config.settings import settings


def should_emit_sample(
    *,
    last: dict[str, Any] | None,
    current: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    """True when sample should be published/stored (interval, delta, or status change)."""
    if last is None:
        return True

    ts = _parse_ts(last.get("timestamp"))
    now_dt = now or datetime.now(UTC)
    interval = max(5, int(settings.metrics_sample_interval_sec))
    if ts is None or (now_dt - ts).total_seconds() >= interval:
        return True

    if last.get("phase") != current.get("phase"):
        return True
    if last.get("restarts") != current.get("restarts"):
        return True
    if last.get("ready") != current.get("ready"):
        return True

    cpu_delta = abs(_int_or_zero(current.get("cpu_millicores")) - _int_or_zero(last.get("cpu_millicores")))
    mem_delta = abs(_int_or_zero(current.get("memory_bytes")) - _int_or_zero(last.get("memory_bytes")))
    if cpu_delta >= max(1, int(settings.metrics_delta_min_cpu_millicores)):
        return True
    if mem_delta >= max(1, int(settings.metrics_delta_min_memory_bytes)):
        return True
    return False


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
