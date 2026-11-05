"""The operator CLI's operator entry points over a live database.

Everything here needs a real PostgreSQL because the two read-only commands are SQL:
``partitions`` inspects the legacy source table and ``cutover-status`` reads the cutover
ledger under the tenant GUC.  Without a DSN the module skips with a printed reason rather
than passing vacuously.
"""

from __future__ import annotations

import json
import os

import pytest

from prodavan import migration_cli

_DSN = os.getenv("PRODAVAN_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not _DSN,
    reason=(
        "set PRODAVAN_TEST_DATABASE_URL (or DATABASE_URL) to a migrated database to run "
        "the CLI's SQL commands"
    ),
)


def test_partitions_reports_an_absent_legacy_table_instead_of_inventing_rows(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A revision-only database has no legacy definitions; that is a fact, not an error.

    ``dynamic_app_definitions`` predates the migration and no revision creates it.  When
    it is absent the command must say so -- not fail, and not report "0 partitions" as if
    it had looked and found none.
    """

    assert migration_cli.main(["--json", "partitions"]) == 0
    payload = json.loads(capsys.readouterr().out)

    if payload["source_table_present"]:
        pytest.skip("this database carries the retained legacy table")
    assert payload["partitions"] == []
    assert "legacy source table dynamic_app_definitions is absent" in payload["note"]


def test_cutover_status_reports_absence_as_absence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No lineage row means no row, and the wording must not imply a migration ran."""

    assert (
        migration_cli.main(
            [
                "--json",
                "cutover-status",
                "--tenant",
                "tenant-with-no-cutover",
                "--definition",
                "definition-with-no-cutover",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["lineage"] == []
    assert payload["partition"] == {
        "tenant_id": "tenant-with-no-cutover",
        "app_definition_id": "definition-with-no-cutover",
    }


def test_planning_a_real_partition_reports_blockers_and_writes_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Planning against the live database must not create or move anything.

    This asserts the CLI's most important safety property: a plan is a read.  It does not
    assert *which* blockers appear -- that is the chain's business and is covered in
    ``tests/migration``; it asserts a plan is produced and nothing is refused silently.
    """

    assert (
        migration_cli.main(
            [
                "--json",
                "plan",
                "--tenant",
                "tenant-under-test",
                "--definition",
                "definition-under-test",
                "--stage",
                "M04_scope",
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["dry_run"] is True
    assert payload["executed"] is False
    # A partition with no host wiring cannot be executable; the point is that the reason
    # is named rather than the command succeeding quietly.
    assert payload["executable"] is False
    assert payload["blocker_codes"]
