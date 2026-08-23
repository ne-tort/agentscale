"""Backend core — lifespan wiring + infrastructure managers (P0 / docs/target/13)."""

from prodavan.core.lifespan.manager import LifespanManager
from prodavan.core.lifespan.resource import LifespanResource

__all__ = ["LifespanManager", "LifespanResource"]
