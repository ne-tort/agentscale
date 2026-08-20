"""S4B skip log lines; catalog search lives in application.catalogs.search."""

from __future__ import annotations


def s4b_log_line(*, s4b_enabled: bool) -> str:
    if s4b_enabled:
        return "s4b skipped=missing_or_unvalidated_credentials; on_order never imported"
    return "s4b skipped=profile_not_electronics"
