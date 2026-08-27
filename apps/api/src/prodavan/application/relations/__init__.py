"""Relations BC — centralized link query/command facade."""

from prodavan.application.relations.commands import RelationsCommand, handle_relation_event_envelope
from prodavan.application.relations.query import RelationsQuery

__all__ = [
    "RelationsCommand",
    "RelationsQuery",
    "handle_relation_event_envelope",
]
