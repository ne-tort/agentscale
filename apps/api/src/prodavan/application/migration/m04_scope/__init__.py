"""Fail-closed M04 typed scope migration boundary."""

from prodavan.application.migration.m04_scope.scope_migration import (
    BackfillCheckpoint,
    BackfillOutcome,
    MigrationEvidenceContext,
    ScopeCandidate,
    ScopeMigrationCoordinator,
    ScopeMigrationResult,
    ScopeResolution,
    ScopeResolver,
)

__all__ = [
    "BackfillCheckpoint",
    "BackfillOutcome",
    "MigrationEvidenceContext",
    "ScopeCandidate",
    "ScopeMigrationCoordinator",
    "ScopeMigrationResult",
    "ScopeResolution",
    "ScopeResolver",
]
