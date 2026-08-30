"""Unit tests for container env resolver."""

from __future__ import annotations

from prodavan.application.pod_service.container_env_resolver import (
    field_value_as_env_string,
    field_value_as_secret_ref,
    merge_env_bindings,
    resolve_plain_env,
    resolve_secret_env,
)


def test_resolve_plain_env_filters_when_and_resolves_value_from() -> None:
    entries = [
        {"env_name": "LOG_LEVEL", "value": "info", "when": ["project.launch"]},
        {"env_name": "SKIP_ME", "value": "x", "when": ["project.sync"]},
        {
            "env_name": "FROM_ROW",
            "value_from": {"table_slug": "settings", "row_id": "default", "field": "flag"},
        },
    ]
    def getter(spec: dict) -> str | None:
        return "on" if spec.get("field") == "flag" else None

    assert resolve_plain_env(entries, lifecycle="project.launch", row_field_getter=getter) == [
        ("LOG_LEVEL", "info"),
        ("FROM_ROW", "on"),
    ]


def test_resolve_secret_env_static_and_row_ref() -> None:
    entries = [
        {
            "env_name": "API_TOKEN",
            "secret_ref": "file://tok_1",
            "when": ["project.launch"],
        },
        {
            "env_name": "ROW_TOKEN",
            "secret_ref_from": {
                "table_slug": "integrations",
                "row_id": "s4b",
                "field": "token_ref",
            },
        },
    ]
    row_refs = {
        ("integrations", "s4b", "token_ref"): "file://row_tok",
    }
    def getter(spec: dict) -> str | None:
        return row_refs.get(
            (
                str(spec.get("table_slug") or ""),
                str(spec.get("row_id") or ""),
                str(spec.get("field") or ""),
            )
        )

    resolved = resolve_secret_env(
        entries,
        lifecycle="project.launch",
        secret_getter=lambda ref: f"secret:{ref}",
        row_field_getter=getter,
    )
    assert resolved == [
        ("API_TOKEN", "secret:file://tok_1"),
        ("ROW_TOKEN", "secret:file://row_tok"),
    ]


def test_field_value_helpers() -> None:
    assert field_value_as_env_string(True) == "true"
    assert field_value_as_env_string(42) == "42"
    assert field_value_as_secret_ref({"secret_ref": "file://x"}) == "file://x"
    assert field_value_as_secret_ref("file://y") == "file://y"


def test_merge_env_bindings_later_overrides() -> None:
    merged = merge_env_bindings(
        [("LOG_LEVEL", "info")],
        [("LOG_LEVEL", "debug"), ("FEATURE_X", "1")],
    )
    assert merged == (("FEATURE_X", "1"), ("LOG_LEVEL", "debug"))
