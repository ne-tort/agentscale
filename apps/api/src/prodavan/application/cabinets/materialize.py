"""Project materialize hook — re-export for L06 compat (L07)."""

from prodavan.application.projects.materialize import MaterializeResult, get_materialize_service

__all__ = ["MaterializeResult", "get_materialize_port", "get_materialize_service"]


def get_materialize_port():
    """Backward-compatible name — returns materialize service."""
    return get_materialize_service()
