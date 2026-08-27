"""Module domain types."""

from __future__ import annotations

from enum import StrEnum


class ModuleCompanyGrantScope(StrEnum):
    SELECTED = "selected"
    ALL = "all"


class ModuleStatus:
    ACTIVE = "active"
    ARCHIVED = "archived"


# Canonical meta slugs (see docs/target/05-cabinets/meta-and-ui.md)
CANONICAL_META_SLUGS = frozenset({"tables", "columns", "views", "tabs"})
# Optional: seed_rows, actions, materialize, mcp_tools — see meta-syntax/01-overview.md
OPTIONAL_META_SLUGS = frozenset({"actions", "materialize", "mcp_tools", "seed_rows"})
