"""Exclusive typed integration zone for the canonical web facade."""

from .contracts import AdapterScope, ReplayCursor, legacy_event_to_run_event, parse_last_event_id
from .service import WebFacadeAdapter

__all__ = [
    "AdapterScope",
    "ReplayCursor",
    "WebFacadeAdapter",
    "legacy_event_to_run_event",
    "parse_last_event_id",
]
