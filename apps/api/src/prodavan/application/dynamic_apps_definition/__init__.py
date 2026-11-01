"""M04.e definition/version resolution boundary."""

from .contracts import (
    DefinitionResolution,
    DefinitionResolutionOutcome,
    DefinitionResolutionReason,
    DefinitionVersionQuery,
    DefinitionVersionRef,
)
from .ports import DefinitionVersionRepository
from .service import DynamicAppDefinitionService

__all__ = [
    "DefinitionResolution",
    "DefinitionResolutionOutcome",
    "DefinitionResolutionReason",
    "DefinitionVersionQuery",
    "DefinitionVersionRef",
    "DefinitionVersionRepository",
    "DynamicAppDefinitionService",
]
