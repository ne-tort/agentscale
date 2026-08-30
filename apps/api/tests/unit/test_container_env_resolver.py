"""Unit tests for container env resolver."""

from __future__ import annotations

from prodavan.application.pod_service.container_env_resolver import (
    merge_env_bindings,
    resolve_plain_env,
    resolve_secret_env,
)


def test_resolve_plain_env_filters_when_and_skips_value_from() -> None:
    entries = [
        {"env_name": "LOG_LEVEL", "value": "info", "when": ["project.launch"]},
        {"env_name": "SKIP_ME", "value": "x", "when": ["project.sync"]},
        {"env_name": "FROM_ROW", "value_from": {"table_slug": "settings", "field": "x"}},
    ]
    assert resolve_plain_env(entries, lifecycle="project.launch") == [("LOG_LEVEL", "info")]


def test_resolve_secret_env_static_ref() -> None:
    entries = [
        {
            "env_name": "API_TOKEN",
            "secret_ref": "file://tok_1",
            "when": ["project.launch"],
        }
    ]
    resolved = resolve_secret_env(
        entries,
        lifecycle="project.launch",
        secret_getter=lambda ref: f"secret:{ref}",
    )
    assert resolved == [("API_TOKEN", "secret:file://tok_1")]


def test_merge_env_bindings_later_overrides() -> None:
    merged = merge_env_bindings(
        [("LOG_LEVEL", "info")],
        [("LOG_LEVEL", "debug"), ("FEATURE_X", "1")],
    )
    assert merged == (("FEATURE_X", "1"), ("LOG_LEVEL", "debug"))
