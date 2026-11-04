"""SQL adapters behind the *canonical* ports of the M06 shadow gateway.

Task ``MIG-M06-PORTS-01``.  See
:mod:`prodavan.infrastructure.persistence.canonical_grants_read.sql_ports` for
what is implemented, what a stored row that cannot be decoded does (a named
refusal, never a skipped row) and why nothing here writes.
"""

from __future__ import annotations

from .sql_ports import (
    ACTIVE_COMPANY_STATUS,
    CanonicalGrantReadRefused,
    SqlAuthenticatedCompanyPort,
    SqlCanonicalGrantReadPort,
)

__all__ = [
    "ACTIVE_COMPANY_STATUS",
    "CanonicalGrantReadRefused",
    "SqlAuthenticatedCompanyPort",
    "SqlCanonicalGrantReadPort",
]
