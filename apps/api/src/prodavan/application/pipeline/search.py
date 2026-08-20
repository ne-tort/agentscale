"""Search/rank stubs: never invent prices; empty offers until M04/M05 catalogs."""

from __future__ import annotations


def empty_offers(*, s4b_enabled: bool) -> dict:
    return {
        "offers": [],
        "sources_log_ref": "sources.log",
        "note": "No catalog/S4B sources in I4; prices not invented",
        "s4b_eligible": s4b_enabled,
    }


def empty_selection() -> dict:
    return {"selections": []}


def search_log_lines(*, s4b_enabled: bool) -> list[str]:
    lines = ["catalog skipped=no_databases (I5 catalogs not wired)"]
    if s4b_enabled:
        lines.append("s4b skipped=missing_credentials (I5 vault); on_order never imported")
    else:
        lines.append("s4b skipped=profile_not_electronics")
    lines.append("web skipped=allowlist_not_queried (I5)")
    return lines
