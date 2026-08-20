"""Build effective capabilities snapshot from cabinet profile manifest."""

from __future__ import annotations

from typing import Any


def has_capability(capabilities: list[str], name: str) -> bool:
    return name in capabilities


def build_capabilities_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
    """Map canonical capability list to effective JSON for API/UI."""
    raw: list[str] = list(profile.get("capabilities", []))
    s4b = has_capability(raw, "procurement.s4b")
    kp = has_capability(raw, "procurement.kp")
    pipeline = has_capability(raw, "procurement.pipeline")
    equipment = has_capability(raw, "procurement.equipment")
    # User catalogs are part of procurement packs, not generic assistant.
    catalogs_user = kp or pipeline or s4b
    return {
        "raw": raw,
        "integrations": {"s4b": {"enabled": s4b}},
        "modules": {
            "specs_kp": {"enabled": kp},
            "equipment_cards": {"enabled": equipment},
            "prompts": {"enabled": True},
            "catalogs_user": {"enabled": catalogs_user},
            "catalogs_system_s4b": {"enabled": s4b},
            "pipeline": {"enabled": pipeline},
            "agent_chat": {"enabled": True},
        },
        "agent": {
            "default_profile_path": profile.get("seeds", {}).get("prompts", "prompts/"),
            "mcp_packages": profile.get("mcp", {}).get("packages", []),
            "tools_allowlist": profile.get("mcp", {}).get("tools_allowlist", []),
        },
    }


def capabilities_preview(profile_row_capabilities: dict[str, Any]) -> dict[str, bool]:
    raw = profile_row_capabilities.get("capabilities", [])
    if isinstance(raw, list):
        return {
            "s4b": "procurement.s4b" in raw,
            "specs_kp": "procurement.kp" in raw,
        }
    return {"s4b": bool(profile_row_capabilities.get("s4b")), "specs_kp": False}
