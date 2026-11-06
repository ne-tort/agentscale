"""Fixtures and markers for the cutover rehearsal package.

The heavy machinery lives in :mod:`harness` (so it can be imported, type-checked and
read on its own); this module only re-exports the fixtures pytest has to see and
declares the one marker the package uses.

``rehearsal_dsn`` / ``rehearsal_database`` are session-scoped and open no connection
until a test asks for them, so collecting this package never touches a database.
"""

from __future__ import annotations

import pytest

from .harness import (
    PURGE_ORDER,
    RehearsalDatabase,
    RehearsalHarness,
    rehearsal,
    rehearsal_database,
    rehearsal_dsn,
)


def pytest_configure(config: pytest.Config) -> None:
    """Register the package's marker (a typo then fails instead of silently defaulting)."""

    config.addinivalue_line(
        "markers",
        "rows_in_public: run this rehearsal against the migrated `public` schema, where "
        "the guards are the ones `alembic upgrade head` installed; the fixture deletes "
        "exactly the rows it seeded.",
    )


__all__ = [
    "PURGE_ORDER",
    "RehearsalDatabase",
    "RehearsalHarness",
    "rehearsal",
    "rehearsal_database",
    "rehearsal_dsn",
]
