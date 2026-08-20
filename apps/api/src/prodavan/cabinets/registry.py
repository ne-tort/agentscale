"""Cabinet module registry (profile_id → CabinetModule)."""

from __future__ import annotations

from prodavan.cabinets.electronics_procurement.module import ElectronicsProcurementModule
from prodavan.cabinets.generic_assistant.module import GenericAssistantModule
from prodavan.cabinets.spi import CabinetModule

_MODULES: dict[str, CabinetModule] = {
    "electronics-procurement": ElectronicsProcurementModule(),
    "generic-assistant": GenericAssistantModule(),
}


def get_module_for_profile(profile_id: str | None) -> CabinetModule:
    if not profile_id:
        return _MODULES["generic-assistant"]
    # Pack dir alias
    if profile_id == "_template":
        profile_id = "generic-assistant"
    return _MODULES.get(profile_id, _MODULES["generic-assistant"])


def registered_pack_ids() -> list[str]:
    return sorted(_MODULES.keys())
