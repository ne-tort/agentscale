"""Process-side composition: the default durable ports for the Resource API.

Everything the Resource API needs from persistence is built here from the request
session, so the HTTP layer never sees an ORM row:

* ``SqlAlchemyResourceScopeProof`` — tenant/cabinet/project/module proofs;
* ``SqlAlchemyResourceStore`` — roots, immutable versions and bindings;
* ``SqlAlchemyApplyReceiptStore`` — durable apply receipts over
  ``platform_events``.

The materialization CAS boundary is **not** created here. It is owned by
``application.resource_materialization`` (durable attempt ledger + revision head,
MIG-RES-ATTEMPT-02) and is consumed through ``AtomicMaterializationApplyPort``.
That module publishes no process-level provider yet, so this factory returns
``apply_port=None`` and the seam is
:func:`install_materialization_apply_port` — the single place where the durable
executor is attached once it exists. Until then ``apply`` refuses with
``503 RESOURCE_APPLY_UNAVAILABLE`` while planning and every resource CRUD path
work against real tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.resource_materialization import AtomicMaterializationApplyPort
from prodavan.application.resources.api.composition import (
    ResourceApiPorts,
    install_resource_api_ports,
)

from .sqlalchemy_receipts import SqlAlchemyApplyReceiptStore
from .sqlalchemy_scope import SqlAlchemyResourceScopeProof
from .sqlalchemy_store import SqlAlchemyResourceStore

ApplyPortProvider = Callable[[AsyncSession], AtomicMaterializationApplyPort | None]

_apply_port_provider: ApplyPortProvider | None = None


def install_materialization_apply_port(provider: ApplyPortProvider | None) -> None:
    """Attach (or clear) the durable CAS executor owned by the runtime zone."""
    global _apply_port_provider
    _apply_port_provider = provider


def materialization_apply_port(session: AsyncSession) -> AtomicMaterializationApplyPort | None:
    provider = _apply_port_provider
    return provider(session) if provider is not None else None


@dataclass(frozen=True, slots=True)
class BuiltPorts:
    """Concrete durable ports plus the CAS boundary attached to them."""

    store: SqlAlchemyResourceStore
    proofs: SqlAlchemyResourceScopeProof
    apply_port: AtomicMaterializationApplyPort | None
    apply_receipts: SqlAlchemyApplyReceiptStore


def build_sqlalchemy_resource_ports(session: AsyncSession) -> ResourceApiPorts:
    """Build every port from one session; nothing is cached between requests."""
    proofs = SqlAlchemyResourceScopeProof(session)
    return ResourceApiPorts(
        store=SqlAlchemyResourceStore(session, scopes=proofs),
        proofs=proofs,
        apply_port=materialization_apply_port(session),
        apply_receipts=SqlAlchemyApplyReceiptStore(session),
    )


def default_resource_api_ports(session: Any) -> ResourceApiPorts | None:
    """``PortFactory`` used when no other adapter was installed."""
    if not isinstance(session, AsyncSession):
        # A non-ORM session cannot be bound to durable rows; fail closed rather
        # than silently serving another store.
        return None
    return build_sqlalchemy_resource_ports(session)


def install_default_resource_api_ports() -> None:
    """Register the durable factory for this process (idempotent)."""
    install_resource_api_ports(default_resource_api_ports)


__all__ = [
    "ApplyPortProvider",
    "build_sqlalchemy_resource_ports",
    "default_resource_api_ports",
    "install_default_resource_api_ports",
    "install_materialization_apply_port",
    "materialization_apply_port",
]
