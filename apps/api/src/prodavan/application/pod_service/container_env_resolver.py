"""Resolve container_env / container_env_secrets meta into Pod env bindings."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_DEFAULT_WHEN = frozenset({"project.launch", "project.sync", "project.resumed", "project.reload"})


def _list_entries(doc: Any) -> list[dict[str, Any]]:
    if not isinstance(doc, list):
        return []
    return [dict(item) for item in doc if isinstance(item, dict)]


def _when_matches(entry: dict[str, Any], lifecycle: str) -> bool:
    when = entry.get("when")
    if when is None:
        return lifecycle in _DEFAULT_WHEN
    if not isinstance(when, list):
        return False
    return lifecycle in [str(item) for item in when]


def resolve_plain_env(
    entries: Iterable[dict[str, Any]],
    *,
    lifecycle: str,
) -> list[tuple[str, str]]:
    """Merge literal container_env entries for a lifecycle event."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        if not _when_matches(entry, lifecycle):
            continue
        env_name = entry.get("env_name")
        if not isinstance(env_name, str) or not _ENV_NAME_RE.match(env_name):
            continue
        if entry.get("value_from") is not None:
            continue
        value = entry.get("value")
        if not isinstance(value, str):
            continue
        if env_name in seen:
            continue
        seen.add(env_name)
        out.append((env_name, value))
    return out


def resolve_secret_env(
    entries: Iterable[dict[str, Any]],
    *,
    lifecycle: str,
    secret_getter: Callable[[str], str],
) -> list[tuple[str, str]]:
    """Resolve static secret_ref entries from container_env_secrets."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        if not _when_matches(entry, lifecycle):
            continue
        env_name = entry.get("env_name")
        if not isinstance(env_name, str) or not _ENV_NAME_RE.match(env_name):
            continue
        if entry.get("secret_ref_from") is not None:
            continue
        secret_ref = entry.get("secret_ref")
        if not isinstance(secret_ref, str) or not secret_ref.strip():
            continue
        if env_name in seen:
            continue
        seen.add(env_name)
        out.append((env_name, secret_getter(secret_ref.strip())))
    return out


def merge_env_bindings(*groups: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Later groups override earlier env names."""
    merged: dict[str, str] = {}
    for group in groups:
        for name, value in group:
            merged[name] = value
    return tuple(sorted(merged.items(), key=lambda item: item[0]))
