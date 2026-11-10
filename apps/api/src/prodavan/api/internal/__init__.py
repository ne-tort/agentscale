"""Internal API routes (pod agent, service-to-service).

Mounting remains a composition-root decision. This package only exposes the
new CI publication router; it does not include it in main or api/v1.
"""

from .plugin_artifacts import router as plugin_artifacts_router

__all__ = ["plugin_artifacts_router"]
