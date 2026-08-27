"""Platform bootstrap and seed configuration."""

from prodavan.application.platform.bootstrap_config import (
    CAB_BASIC_ID,
    DEFAULT_BASIC_MODULE_IDS,
)
from prodavan.application.platform.bootstrap_service import PlatformBootstrapService

__all__ = [
    "CAB_BASIC_ID",
    "DEFAULT_BASIC_MODULE_IDS",
    "PlatformBootstrapService",
]
