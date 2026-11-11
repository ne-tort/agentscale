"""Allow canonical direct protected-branch workflow identities.

Revision ID: 2026111201
Revises: 2026110909

Reusable workflows remain pinned to a full lowercase commit SHA. Direct runs may
use only the canonical platform workflow at refs/heads/main; ModuleSource also
requires that exact ref to be the canonical repository's protected default ref.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "2026111201"
down_revision: str | Sequence[str] | None = "2026110909"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_WORKFLOW_RE = (
    "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/"
    "[.]github/workflows/[A-Za-z0-9_.-]+[.]ya?ml@[0-9a-f]{40}$"
)
_NEW_WORKFLOW_RE = (
    "^ne-tort/agentscale/[.]github/workflows/plugin-artifacts[.]yml@"
    "([0-9a-f]{40}|refs/heads/main)$"
)
_CONSTRAINTS = (
    ("plugin_module_sources", "ck_plugin_module_source_workflow_ref"),
    ("plugin_source_reservations", "ck_plugin_source_reservation_workflow_ref"),
)


def _replace_checks(pattern: str) -> None:
    for table, name in _CONSTRAINTS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, f"trusted_workflow_ref ~ '{pattern}'")


def upgrade() -> None:
    _replace_checks(_NEW_WORKFLOW_RE)


def downgrade() -> None:
    _replace_checks(_OLD_WORKFLOW_RE)
