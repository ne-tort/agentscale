"""SQLAlchemy adapters binding the canonical Resource API ports to durable rows.

Layering note: the HTTP surface and ``application/resources/api`` stay free of
SQLAlchemy. This subpackage is the process-side adapter set — the same shape the
repository already uses for other migration zones (for example
``application/dynamic_apps_definition/sqlalchemy_adapter.py``).

Tables used, all created by revision ``2026110306_resources_runtime``:

* ``resources`` / ``resource_versions`` / ``resource_bindings`` — the canonical
  root, its append-only versions and its bindings;
* ``runtime_revision_heads`` — the durable source of a project's resource
  generation (read through ``application.resource_materialization.durable``,
  never re-derived from a plan);
* ``platform_events`` — its unique ``idempotency_key`` is the race-proof identity
  for an apply receipt (the same mechanism the dynamic-app audit port uses).

No adapter creates DDL, runs migrations or writes materialization state. The
generation fence itself remains owned by ``AtomicMaterializationApplyPort``.
"""

from __future__ import annotations

from .sqlalchemy_receipts import SqlAlchemyApplyReceiptStore
from .sqlalchemy_scope import SqlAlchemyResourceScopeProof
from .sqlalchemy_store import SqlAlchemyResourceStore
from .factory import (
    build_sqlalchemy_resource_ports,
    default_resource_api_ports,
    install_default_resource_api_ports,
)

__all__ = [
    "SqlAlchemyApplyReceiptStore",
    "SqlAlchemyResourceScopeProof",
    "SqlAlchemyResourceStore",
    "build_sqlalchemy_resource_ports",
    "default_resource_api_ports",
    "install_default_resource_api_ports",
]
