"""Module domain types."""

from __future__ import annotations

from enum import StrEnum


class ModuleCompanyGrantScope(StrEnum):
    SELECTED = "selected"
    ALL = "all"


class ModuleBindKind(StrEnum):
    LOCAL = "local"
    GLOBAL = "global"


# Product modules that typically share cabinet SoT via global cabinet→project binds.
GLOBAL_DEFAULT_PROJECT_MODULES = frozenset({"mod_prompts", "mod_mcp", "mod_files"})


def default_project_bind_kind(module_id: str) -> ModuleBindKind:
    """UI/API default for new project binds (not a hard constraint)."""
    if module_id in GLOBAL_DEFAULT_PROJECT_MODULES:
        return ModuleBindKind.GLOBAL
    return ModuleBindKind.LOCAL


def default_cabinet_bind_kind(module_id: str) -> ModuleBindKind:
    """Default MC bind: management modules share parent SoT; others fork locally."""
    return default_project_bind_kind(module_id)


def default_child_may_edit(bind_kind: ModuleBindKind | str) -> bool:
    return bind_kind == ModuleBindKind.LOCAL or bind_kind == "local"


class ModuleStatus:
    ACTIVE = "active"
    ARCHIVED = "archived"


# Canonical meta slugs (see docs/target/05-cabinets/meta-and-ui.md)
CANONICAL_META_SLUGS = frozenset({"tables", "columns", "views", "tabs"})
# Optional: seed_rows, actions, materialize, mcp_tools — see meta-syntax/01-overview.md
OPTIONAL_META_SLUGS = frozenset({"actions", "materialize", "mcp_tools", "seed_rows"})
