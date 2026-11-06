"""SQLAlchemy persistence boundary for M04 scope migration."""

from .adapter import (
    ReviewedScopeRlsManifest,
    ScopePersistenceGateBlocked,
    SqlAlchemyTrustedScopeTransactionPort,
)

__all__ = [
    "ReviewedScopeRlsManifest",
    "ScopePersistenceGateBlocked",
    "SqlAlchemyTrustedScopeTransactionPort",
]
