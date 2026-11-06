"""Fixtures for the M04E export-reader acceptance package.

Two distinct layers, deliberately kept apart:

* **The SQLite mirror** (``sqlite_export_ledger``) -- an ephemeral in-process database built
  from the *registered* models, mirroring the four ledger tables plus the promotion table the
  handoff marker lives in (see :class:`~tests.migration.m04e_export.ledger_fixtures
  .ExportLedgerMirror`).  Rows are always written by the production store
  (``SqlM04EEvidenceStore.classify_and_commit_page``), never by hand, except in the cases
  that model a ledger *no page commit produces* -- those say so in their own docstring.
  This layer needs no external service, so it never skips.

* **The live PostgreSQL** (``m04e_export_postgres``) -- session-scoped, skips with a printed
  reason unless ``PRODAVAN_TEST_DATABASE_URL`` names a reachable, migrated database, exactly
  as :mod:`tests.migration.cutover_rehearsal` does.  Nothing connects until a test asks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from .ledger_fixtures import ExportLedgerMirror, store_for


@pytest_asyncio.fixture()
async def export_ledger() -> AsyncIterator[ExportLedgerMirror]:
    """One empty SQLite mirror of the M04E ledger **and** the handoff-marker table."""

    mirror = ExportLedgerMirror()
    await mirror.create()
    try:
        yield mirror
    finally:
        await mirror.close()


@pytest.fixture()
def m04e_export_postgres() -> Any:
    """The migrated PostgreSQL this package may read, or a skip that says why.

    Imported lazily so collecting the SQLite layer never imports the rehearsal harness --
    a harness import failure must not take the unit tests down with it.
    """

    from tests.migration.cutover_rehearsal.harness import (
        _tcp_reachable as is_reachable,
    )
    from tests.migration.cutover_rehearsal.harness import database_url_from_env

    url = database_url_from_env()
    if url is None:
        pytest.skip(
            "PRODAVAN_TEST_DATABASE_URL is not set: the live M04E export-reader checks run "
            "only against a PostgreSQL with the migration chain applied "
            "(see tests/migration/cutover_rehearsal/__init__.py)."
        )
    if not is_reachable(url):
        pytest.skip(
            "PRODAVAN_TEST_DATABASE_URL is set but nothing listens on that endpoint; "
            "the live M04E export-reader checks need a migrated PostgreSQL."
        )
    return url


__all__ = ["export_ledger", "m04e_export_postgres", "store_for"]
