"""Fixed ids for platform seed — modules, basic cabinet, bootstrap marker."""

from __future__ import annotations

CAB_BASIC_ID = "cab_basic"
BOOTSTRAP_MARKER_KEY = "platform_bootstrap.v1"

DEFAULT_BASIC_MODULE_IDS: tuple[str, ...] = (
    "mod_prompts",
    "mod_mcp",
    "mod_files",
)

BASIC_TEMPLATES: frozenset[str] = frozenset({"basic", "base"})
