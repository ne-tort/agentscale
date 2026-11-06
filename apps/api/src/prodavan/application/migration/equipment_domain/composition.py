"""Composition root for the durable equipment domain.

This is the only supported way to obtain an equipment service whose selection and
export transactions are backed by ``equipment_v2_*`` rows.  Everything it returns
is scoped to one server-resolved principal and one explicit
:class:`~.durable.EquipmentDurableConfig`; there is no default, no fallback and no
in-memory variant.

Typical use inside a request handler that already opened a transaction and
applied the trusted tenant context:

.. code-block:: python

    async with session.begin():
        async with trusted_tenant_context(session, principal=principal):
            service = build_durable_equipment_service(
                session=session, principal=principal, config=config,
            )
            await service.commit_selection(scope=scope, actor_id=principal.subject_id, ...)

The returned service's transaction port *is* the SQLAlchemy repository, so
``commit_selection``/``commit_export`` either write real rows plus append-only
audit and idempotency, or return ``blocked``/``conflict``.  ``WIRING_BLOCKER`` is
therefore unreachable for a service built here; it remains the documented
outcome only for hosts that construct :class:`~.service.EquipmentDomainService`
directly without a transaction port.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from .durable import EquipmentDurableBlocked, EquipmentDurableConfig, SqlAlchemyEquipmentRepository
from .service import EquipmentDomainService


def build_durable_equipment_service(
    *,
    session: AsyncSession,
    principal: object,
    config: EquipmentDurableConfig,
    clock: Callable[[], datetime] | None = None,
) -> EquipmentDomainService:
    """Build the fully durable service; refuses anything less explicit."""
    if not isinstance(config, EquipmentDurableConfig):
        raise EquipmentDurableBlocked("equipment_durable_config_required",
                                      "a durable config with owner and source map is required")
    repository = SqlAlchemyEquipmentRepository(
        session, principal=principal, config=config, clock=clock,  # type: ignore[arg-type]
    )
    return EquipmentDomainService(
        source=repository,
        actors=repository,
        decisions=repository,
        budgets=repository,
        transaction=repository,
    )


__all__ = ["build_durable_equipment_service"]
