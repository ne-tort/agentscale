"""Static/unit checks for publication approval model and additive revision."""

from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import sqlalchemy as sa

from prodavan.application.plugins.admin.contracts import EgressMode, QuotaSet
from prodavan.infrastructure.persistence.models.publication_approvals import (
    PluginPublicationApprovalRow,
)
from prodavan.infrastructure.persistence.repositories.plugins.publication_approvals import (
    SqlPluginPublicationApprovalRepository,
)


def _load_revision():
    root = Path(__file__).resolve().parents[6]
    path = root / "apps/api/alembic/versions/2026110902_plugin_publication_approvals.py"
    spec = importlib.util.spec_from_file_location("revision_2026110902_approvals", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_is_after_ci_publication_and_has_no_pending_0802_dependency() -> None:
    module = _load_revision()
    assert module.revision == "2026110902"
    assert module.down_revision == "2026110901"
    assert module.branch_labels is None
    assert module.depends_on is None


def test_upgrade_only_creates_approval_table_matching_orm() -> None:
    module = _load_revision()
    op = Mock()
    module.op = op

    module.upgrade()

    call = op.create_table.call_args
    assert call.args[0] == "plugin_publication_approvals"
    columns = {
        item.name: item for item in call.args[1:] if isinstance(item, sa.Column)
    }
    assert set(columns) == set(PluginPublicationApprovalRow.__table__.c.keys())
    assert columns["consumed_at"].nullable is True
    assert "status" not in columns
    assert not op.execute.called
    assert not op.bulk_insert.called
    assert not op.add_column.called
    assert not op.alter_column.called
    assert not op.drop_table.called


def test_additive_revision_does_not_activate_or_backfill_legacy_grants() -> None:
    module = _load_revision()
    op = Mock()
    module.op = op

    module.upgrade()

    # No UPDATE, status default, grant insert, artifact publish or lifecycle backfill.
    assert not op.execute.called
    assert not op.bulk_insert.called
    assert not op.add_column.called
    assert not op.create_foreign_key.called
    table_call = op.create_table.call_args
    assert all(
        not isinstance(item, sa.Column) or item.name != "status"
        for item in table_call.args[1:]
    )


def test_downgrade_drops_only_approval_objects() -> None:
    module = _load_revision()
    op = Mock()
    module.op = op

    module.downgrade()

    assert op.drop_index.call_args.args[0] == (
        "ix_plugin_publication_approvals_company_plugin"
    )
    assert op.drop_table.call_args.args[0] == "plugin_publication_approvals"
    assert not op.execute.called


def test_approval_row_roundtrip_has_full_policy_and_no_grant_status() -> None:
    now = datetime(2026, 11, 9, tzinfo=UTC)
    quotas = QuotaSet(ops_per_minute=17)
    row = PluginPublicationApprovalRow(
        id="ppr_test",
        company_id="co_a",
        plugin_id="mod_equipment",
        manifest_version="1.2.0",
        artifact_digest="sha256:" + "a" * 64,
        capabilities=["cache"],
        egress_policy={
            "mode": "none",
            "allowed_hosts": [],
            "allowed_paths": [],
            "approved_for_full": False,
        },
        quota={name: getattr(quotas, name) for name in (
            "ops_per_minute", "max_rows", "max_bytes", "max_keys",
            "max_object_bytes", "max_egress_bytes",
        )},
        created_by="approver-1",
        created_at=now,
        consumed_at=None,
    )

    record = SqlPluginPublicationApprovalRepository.to_record(row)

    assert record.capabilities == ("cache",)
    assert record.egress_policy.mode is EgressMode.NONE
    assert record.quotas == quotas
    assert record.created_by == "approver-1"
    assert record.consumed_at is None
    assert not hasattr(record, "status")
    assert not hasattr(record, "is_active")
