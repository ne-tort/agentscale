"""Static regression checks for direct/reusable workflow refs; no DB execution."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from unittest.mock import Mock, call

import pytest
from prodavan.infrastructure.persistence.models.plugin_platform import (
    MODULE_SOURCE_WORKFLOW_SQL,
    ModuleSourceRow,
    PluginSourceReservationRow,
)

_WORKFLOW_PREFIX = "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@"


def _load():
    root = Path(__file__).resolve().parents[6]
    path = root / "apps/api/alembic/versions/2026111201_plugin_source_direct_workflow_refs.py"
    spec = importlib.util.spec_from_file_location("revision_2026111201_workflow_refs", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_workflow_regex_matches_source_and_reservation_orm() -> None:
    module = _load()
    assert module._NEW_WORKFLOW_RE == MODULE_SOURCE_WORKFLOW_SQL
    expected = f"trusted_workflow_ref ~ '{MODULE_SOURCE_WORKFLOW_SQL}'"
    for row, constraint_name in (
        (ModuleSourceRow, "ck_plugin_module_source_workflow_ref"),
        (PluginSourceReservationRow, "ck_plugin_source_reservation_workflow_ref"),
    ):
        constraint = next(
            item for item in row.__table__.constraints if item.name == constraint_name
        )
        assert str(constraint.sqltext) == expected


@pytest.mark.parametrize("suffix", ["a" * 40, "refs/heads/main"])
def test_new_workflow_regex_accepts_full_sha_and_direct_main(suffix: str) -> None:
    module = _load()
    assert re.fullmatch(module._NEW_WORKFLOW_RE, _WORKFLOW_PREFIX + suffix) is not None


@pytest.mark.parametrize(
    "suffix",
    ["refs/heads/release", "refs/tags/main", "a" * 39, "A" * 40],
)
def test_new_workflow_regex_rejects_mutable_or_noncanonical_refs(suffix: str) -> None:
    module = _load()
    assert re.fullmatch(module._NEW_WORKFLOW_RE, _WORKFLOW_PREFIX + suffix) is None


def test_revision_replaces_both_checks_and_links_to_installation_scope() -> None:
    module = _load()
    assert module.revision == "2026111201"
    assert module.down_revision == "2026110909"
    assert module.branch_labels is None
    assert module.depends_on is None

    op = Mock()
    module.op = op
    module.upgrade()
    new_condition = f"trusted_workflow_ref ~ '{module._NEW_WORKFLOW_RE}'"
    assert op.method_calls == [
        call.drop_constraint(
            "ck_plugin_module_source_workflow_ref",
            "plugin_module_sources",
            type_="check",
        ),
        call.create_check_constraint(
            "ck_plugin_module_source_workflow_ref",
            "plugin_module_sources",
            new_condition,
        ),
        call.drop_constraint(
            "ck_plugin_source_reservation_workflow_ref",
            "plugin_source_reservations",
            type_="check",
        ),
        call.create_check_constraint(
            "ck_plugin_source_reservation_workflow_ref",
            "plugin_source_reservations",
            new_condition,
        ),
    ]

    op.reset_mock()
    module.downgrade()
    old_condition = f"trusted_workflow_ref ~ '{module._OLD_WORKFLOW_RE}'"
    assert op.method_calls == [
        call.drop_constraint(
            "ck_plugin_module_source_workflow_ref",
            "plugin_module_sources",
            type_="check",
        ),
        call.create_check_constraint(
            "ck_plugin_module_source_workflow_ref",
            "plugin_module_sources",
            old_condition,
        ),
        call.drop_constraint(
            "ck_plugin_source_reservation_workflow_ref",
            "plugin_source_reservations",
            type_="check",
        ),
        call.create_check_constraint(
            "ck_plugin_source_reservation_workflow_ref",
            "plugin_source_reservations",
            old_condition,
        ),
    ]
    assert not op.execute.called
