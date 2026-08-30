"""Resolve container_env / container_env_secrets meta into Pod env bindings."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_DEFAULT_WHEN = frozenset({"project.launch", "project.sync", "project.resumed", "project.reload"})

RowFieldGetter = Callable[[dict[str, Any]], str | None]


def _when_matches(entry: dict[str, Any], lifecycle: str) -> bool:
    when = entry.get("when")
    if when is None:
        return lifecycle in _DEFAULT_WHEN
    if not isinstance(when, list):
        return False
    return lifecycle in [str(item) for item in when]


def field_value_as_env_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def field_value_as_secret_ref(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        ref = value.get("secret_ref")
        if isinstance(ref, str) and ref.strip():
            return ref.strip()
    return None


def resolve_plain_env(
    entries: Iterable[dict[str, Any]],
    *,
    lifecycle: str,
    row_field_getter: RowFieldGetter | None = None,
) -> list[tuple[str, str]]:
    """Merge literal and row-backed container_env entries for a lifecycle event."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        if not _when_matches(entry, lifecycle):
            continue
        env_name = entry.get("env_name")
        if not isinstance(env_name, str) or not _ENV_NAME_RE.match(env_name):
            continue
        if env_name in seen:
            continue

        value: str | None = None
        value_from = entry.get("value_from")
        if isinstance(value_from, dict):
            if row_field_getter is None:
                continue
            value = row_field_getter(value_from)
        else:
            raw = entry.get("value")
            value = raw if isinstance(raw, str) else None

        if value is None:
            continue
        seen.add(env_name)
        out.append((env_name, value))
    return out


def resolve_secret_env(
    entries: Iterable[dict[str, Any]],
    *,
    lifecycle: str,
    secret_getter: Callable[[str], str],
    row_field_getter: RowFieldGetter | None = None,
) -> list[tuple[str, str]]:
    """Resolve static and row-backed container_env_secrets entries."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        if not _when_matches(entry, lifecycle):
            continue
        env_name = entry.get("env_name")
        if not isinstance(env_name, str) or not _ENV_NAME_RE.match(env_name):
            continue
        if env_name in seen:
            continue

        secret_ref: str | None = None
        secret_ref_from = entry.get("secret_ref_from")
        if isinstance(secret_ref_from, dict):
            if row_field_getter is None:
                continue
            secret_ref = row_field_getter(secret_ref_from)
        else:
            raw = entry.get("secret_ref")
            secret_ref = raw.strip() if isinstance(raw, str) and raw.strip() else None

        if secret_ref is None:
            continue
        seen.add(env_name)
        out.append((env_name, secret_getter(secret_ref)))
    return out


def merge_env_bindings(*groups: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Later groups override earlier env names."""
    merged: dict[str, str] = {}
    for group in groups:
        for name, value in group:
            merged[name] = value
    return tuple(sorted(merged.items(), key=lambda item: item[0]))
