"""Module domain types."""

from __future__ import annotations

from enum import StrEnum


class ModuleCompanyGrantScope(StrEnum):
    SELECTED = "selected"
    ALL = "all"


class ModuleBindKind(StrEnum):
    LOCAL = "local"
    GLOBAL = "global"


# Opt-out: modules that still default to a forked project leaf on cabinet→project bind.
# Empty — product modules inherit cabinet SoT via global MP by default.
LOCAL_DEFAULT_PROJECT_MODULES: frozenset[str] = frozenset()

# Back-compat alias (was an allowlist of global defaults; now unused for defaults).
GLOBAL_DEFAULT_PROJECT_MODULES = frozenset({"mod_prompts", "mod_mcp", "mod_files"})

# Global MP binds that still allow writes into parent SoT (agent fills chat-scoped
# rows like request_lines / found_offers while sharing catalogs).
WRITABLE_GLOBAL_PROJECT_MODULES: frozenset[str] = frozenset({"mod_equipment"})


def default_project_bind_kind(module_id: str) -> ModuleBindKind:
    """UI/API default for new project binds (not a hard constraint).

    Cabinet→project binds default to **global** (share cabinet SoT). Opt into
    local fork via :data:`LOCAL_DEFAULT_PROJECT_MODULES`.
    """
    if module_id in LOCAL_DEFAULT_PROJECT_MODULES:
        return ModuleBindKind.LOCAL
    return ModuleBindKind.GLOBAL


def default_cabinet_bind_kind(module_id: str) -> ModuleBindKind:
    """Cabinet binds always fork local SoT; projects may share it via global MP binds."""
    _ = module_id
    return ModuleBindKind.LOCAL


def default_company_grant_bind_kind(module_id: str) -> ModuleBindKind:
    """Admin→company grants default to a local company copy."""
    _ = module_id
    return ModuleBindKind.LOCAL


def default_child_may_edit(
    bind_kind: ModuleBindKind | str,
    module_id: str | None = None,
) -> bool:
    """Default write flag for a new bind.

    Local binds are always writable. Global binds are read-only unless the
    module opts into :data:`WRITABLE_GLOBAL_PROJECT_MODULES` (or the caller
    passes an explicit ``child_may_edit``).
    """
    if bind_kind == ModuleBindKind.LOCAL or bind_kind == "local":
        return True
    if module_id and module_id in WRITABLE_GLOBAL_PROJECT_MODULES:
        return True
    return False


class ModuleStatus:
    ACTIVE = "active"
    ARCHIVED = "archived"


# Canonical meta slugs (see docs/target/05-cabinets/meta-and-ui.md)
CANONICAL_META_SLUGS = frozenset({"tables", "columns", "views", "tabs"})
# Optional: seed_rows, actions, materialize, mcp_tools — see meta-syntax/01-overview.md
OPTIONAL_META_SLUGS = frozenset({"actions", "materialize", "mcp_tools", "seed_rows"})
