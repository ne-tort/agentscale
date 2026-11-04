"""Application contracts for automation ingress, task runs, and deliveries.

The package re-exports the same names as before, but resolves them lazily.

Why: ``automation_persistence.contracts`` imports
``automation_service.contracts``, and importing that submodule executes this
package ``__init__`` first. Eagerly re-exporting ``outbox``/``service`` from here
therefore pulled ``automation_persistence.contracts`` back in while it was still
half-built (``ImportError: cannot import name 'StaleCasOutcome' from partially
initialized module``), so *any* first import of the persistence package failed —
including the module-level imports of ``DurableError`` and ``LeaseToken`` through
the ``__getattr__`` hook :mod:`prodavan.application.automation_service.contracts`
already declares for exactly this reason.

Deferring resolution to first attribute access keeps the public surface
identical (``from prodavan.application.automation_service import X`` still works
for every name in ``__all__``) while making the import graph acyclic: nothing is
imported until the cycle has been unwound and the owning module is complete.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "OutboxDispatcher",
    "OutboxRepository",
    "OutboxWorker",
    "AutomationService",
    "DeliveryEgressPolicyPort",
    "DeliveryEgressPolicyResult",
    "DurableError",
    "LeaseToken",
    "durable_error",
    "DeliverySchedule",
    "DeliveryStatus",
    "IngressResult",
    "TaskRun",
    "TaskRunStatus",
    "WebhookIngress",
]

#: Public name -> the submodule that owns it. ``contracts`` itself is a leaf (it
#: imports the persistence value objects only through ``__getattr__``), so it may
#: be imported eagerly; ``outbox`` and ``service`` import the persistence package
#: at module scope and must stay lazy.
_ORIGINS: dict[str, str] = {
    "OutboxDispatcher": "outbox",
    "OutboxRepository": "outbox",
    "OutboxWorker": "outbox",
    "AutomationService": "service",
    "DeliverySchedule": "service",
    "DeliveryStatus": "service",
    "IngressResult": "service",
    "TaskRun": "service",
    "TaskRunStatus": "service",
    "WebhookIngress": "service",
    "DeliveryEgressPolicyPort": "contracts",
    "DeliveryEgressPolicyResult": "contracts",
    "DurableError": "contracts",
    "LeaseToken": "contracts",
    "durable_error": "contracts",
}


def __getattr__(name: str) -> Any:
    """Resolve a re-exported contract on first use."""

    origin = _ORIGINS.get(name)
    if origin is None:
        raise AttributeError(name)
    module = import_module(f"{__name__}.{origin}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
