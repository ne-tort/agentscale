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


def test_row_eligible_for_env_respects_project_ids_and_enabled() -> None:
    from prodavan.application.pod_service.container_env_loader import row_eligible_for_env

    assert row_eligible_for_env({"base_url": "https://x"}, "proj_a")
    assert row_eligible_for_env({"enabled": True, "project_ids": []}, "proj_a")
    assert not row_eligible_for_env({"enabled": False}, "proj_a")
    assert row_eligible_for_env(
        {"enabled": True, "project_ids": ["proj_a"]}, "proj_a"
    )
    assert not row_eligible_for_env(
        {"enabled": True, "project_ids": ["proj_b"]}, "proj_a"
    )


def test_resolve_secret_env_skips_foreach_rows_entries() -> None:
    entries = [
        {
            "foreach_rows": {
                "table_slug": "catalogs",
                "field": "remote_dsn",
                "env_name_prefix": "EQUIPMENT_CATALOG_DSN_",
            },
            "when": ["project.launch"],
        },
        {"env_name": "API_TOKEN", "secret_ref": "file://tok", "when": ["project.launch"]},
    ]
    resolved = resolve_secret_env(
        entries,
        lifecycle="project.launch",
        secret_getter=lambda ref: f"secret:{ref}",
    )
    assert resolved == [("API_TOKEN", "secret:file://tok")]


def test_foreach_rows_emits_multi_dsn_and_registry() -> None:
    """ContainerEnvLoader expands one DSN env per ready remote catalog row."""
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock

    from prodavan.application.pod_service.container_env_loader import ContainerEnvLoader

    secrets = MagicMock()
    secrets.get = MagicMock(side_effect=lambda ref: f"dsn:{ref}")
    loader = ContainerEnvLoader(session=MagicMock(), secrets=secrets)
    loader._sot_instance_id = AsyncMock(return_value="inst_1")  # type: ignore[method-assign]
    loader._instances.list_data_rows = AsyncMock(
        return_value=[
            {
                "row_id": "row_local",
                "body": {
                    "name": "Local CSV",
                    "source_kind": "local",
                    "status": "ready",
                    "paused": False,
                },
            },
            {
                "row_id": "row_a",
                "body": {
                    "name": "PG A",
                    "source_kind": "remote",
                    "status": "ready",
                    "paused": False,
                    "remote_table": "public.prices",
                    "remote_dsn": {"secret_ref": "file://cabinet_secrets/cab1/a"},
                    "column_map": {"title": "name"},
                    "row_count": 10,
                },
            },
            {
                "row_id": "row_b",
                "body": {
                    "name": "PG B",
                    "source_kind": "remote",
                    "status": "ready",
                    "paused": False,
                    "remote_table": "sales.items",
                    "remote_dsn": "file://cabinet_secrets/cab1/b",
                    "column_map": {},
                    "row_count": 3,
                },
            },
            {
                "row_id": "row_paused",
                "body": {
                    "name": "Paused",
                    "source_kind": "remote",
                    "status": "ready",
                    "paused": True,
                    "remote_dsn": "file://cabinet_secrets/cab1/c",
                    "remote_table": "t",
                },
            },
        ]
    )

    plain, secret_bindings = asyncio.run(
        loader._resolve_foreach_rows(
            [
                {
                    "foreach_rows": {
                        "table_slug": "catalogs",
                        "field": "remote_dsn",
                        "env_name_prefix": "EQUIPMENT_CATALOG_DSN_",
                        "match": {"source_kind": "remote", "status": "ready"},
                        "registry_env_name": "EQUIPMENT_REMOTE_CATALOGS",
                    },
                    "when": ["project.launch"],
                }
            ],
            lifecycle="project.launch",
            module_id="mod_equipment",
            project_id="proj_1",
            cabinet_id="cab1",
        )
    )
    secret_names = [n for n, _ in secret_bindings]
    assert secret_names == [
        "EQUIPMENT_CATALOG_DSN_ROW_A",
        "EQUIPMENT_CATALOG_DSN_ROW_B",
    ]
    assert dict(secret_bindings)["EQUIPMENT_CATALOG_DSN_ROW_A"] == (
        "dsn:file://cabinet_secrets/cab1/a"
    )
    assert len(plain) == 1
    assert plain[0][0] == "EQUIPMENT_REMOTE_CATALOGS"
    registry = json.loads(plain[0][1])
    assert [r["id"] for r in registry] == ["row_a", "row_b"]
    assert registry[0]["dsn_env"] == "EQUIPMENT_CATALOG_DSN_ROW_A"
    assert registry[0]["table"] == "public.prices"
