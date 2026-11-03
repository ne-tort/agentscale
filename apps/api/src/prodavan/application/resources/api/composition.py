"""Runtime composition for the canonical Resource API (MIG-RESOURCE-API-02).

The HTTP surface is built from three application services that only know ports.
The ports themselves are persistence concerns and are owned outside this package
(``infrastructure/persistence`` or a dedicated adapter module), so this module is
the single seam between them:

* an adapter registers a factory bound to the request ``AsyncSession`` with
  :func:`install_resource_api_ports` once, at process start;
* :func:`build_resource_api` assembles the command and materialization services
  for one request;
* when no factory is registered, the durable adapter set shipped with
  ``application.resources.adapters`` is used (real tables from revision
  ``2026110306``); when even that is unavailable the request fails closed with
  ``503 RESOURCE_STORE_UNAVAILABLE``. There is deliberately no in-memory or
  legacy fallback: serving resources from process memory would bypass tenant
  scoping, immutable versions and the materialization CAS boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from prodavan.application.resource_materialization import AtomicMaterializationApplyPort

from .commands import ResourceCommandService
from .errors import ResourceApiErrorCode, fail
from .materialization import ResourceMaterializationService
from .ports import (
    ResourceApplyReceiptPort,
    ResourceScopeProofPort,
    ResourceStorePort,
)
from .scope import ResourceScopeService


@dataclass(frozen=True, slots=True)
class ResourceApiPorts:
    """Every port the resource HTTP surface needs, resolved for one request."""

    store: ResourceStorePort
    proofs: ResourceScopeProofPort
    #: The trusted CAS boundary owned by the runtime materialization zone.
    apply_port: AtomicMaterializationApplyPort | None = None
    #: Durable apply receipts; without them apply is unavailable, not degraded.
    apply_receipts: ResourceApplyReceiptPort | None = None


@dataclass(frozen=True, slots=True)
class ResourceApiBackend:
    """Services wired to one request's ports."""

    ports: ResourceApiPorts
    commands: ResourceCommandService
    materialization: ResourceMaterializationService


#: ``session -> ports``. Returning ``None`` means "the adapter is not ready for
#: this request" and is answered exactly like an unwired factory.
PortFactory = Callable[[Any], ResourceApiPorts | None]

_factory: PortFactory | None = None


def install_resource_api_ports(factory: PortFactory | None) -> None:
    """Register (or clear) the persistence adapter factory for this process."""
    global _factory
    _factory = factory


def resource_api_port_factory() -> PortFactory | None:
    """The currently registered factory, if any (introspection and tests)."""
    return _factory


def assemble_resource_api(ports: ResourceApiPorts) -> ResourceApiBackend:
    """Bind the application services to already-resolved ports."""
    scopes = ResourceScopeService(ports.proofs)
    return ResourceApiBackend(
        ports=ports,
        commands=ResourceCommandService(
            store=ports.store,
            scopes=scopes,
            proofs=ports.proofs,
        ),
        materialization=ResourceMaterializationService(
            store=ports.store,
            scopes=scopes,
            apply_port=ports.apply_port,
            receipts=ports.apply_receipts,
        ),
    )


def _default_factory() -> PortFactory | None:
    """The durable adapter set installed with the package, when importable."""
    try:
        from prodavan.application.resources.adapters.factory import (
            default_resource_api_ports,
        )
    except Exception:  # noqa: BLE001 - a missing adapter is a closed gate
        return None
    return default_resource_api_ports


def build_resource_api(session: Any) -> ResourceApiBackend:
    """Assemble the request-scoped backend or fail closed."""
    factory = _factory
    if factory is None:
        factory = _default_factory()
    if factory is None:
        fail(
            ResourceApiErrorCode.RESOURCE_STORE_UNAVAILABLE,
            detail="resource persistence adapters are not registered in this process",
            reason="resource_persistence_adapter_unwired",
            extra={"blocker": "resource_persistence_adapter_unwired"},
        )
    ports = factory(session)
    if ports is None:
        fail(
            ResourceApiErrorCode.RESOURCE_STORE_UNAVAILABLE,
            detail="resource persistence adapters are not ready for this request",
            reason="resource_persistence_adapter_unavailable",
            extra={"blocker": "resource_persistence_adapter_unavailable"},
        )
    return assemble_resource_api(ports)


__all__ = [
    "PortFactory",
    "ResourceApiBackend",
    "ResourceApiPorts",
    "assemble_resource_api",
    "build_resource_api",
    "install_resource_api_ports",
    "resource_api_port_factory",
]
