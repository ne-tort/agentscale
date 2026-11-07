"""Fixed ids for platform seed — modules, basic cabinet, bootstrap marker."""

from __future__ import annotations

CAB_BASIC_ID = "cab_basic"
BOOTSTRAP_MARKER_KEY = "platform_bootstrap.v1"

# Modules the basic cabinet gets. «Управление» carries prompts, MCP packages and
# workspace files — the three modules that used to be listed here separately.
DEFAULT_BASIC_MODULE_IDS: tuple[str, ...] = ("mod_management",)

BASIC_TEMPLATES: frozenset[str] = frozenset({"basic", "base"})
