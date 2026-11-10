"""Source-control metadata для trusted CI publication (не runtime registry)."""

from .ports import ModuleSourceRepositoryPort
from .repository import (
    ModuleSourceConflictError,
    ModuleSourceRepositoryError,
    SqlModuleSourceRepository,
)
from .types import ModuleSource, ModuleSourceStatus

__all__ = [
    "ModuleSource",
    "ModuleSourceConflictError",
    "ModuleSourceRepositoryError",
    "ModuleSourceRepositoryPort",
    "ModuleSourceStatus",
    "SqlModuleSourceRepository",
]
