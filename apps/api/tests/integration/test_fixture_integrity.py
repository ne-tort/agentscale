"""Fixture integrity — the wipe must hand back a database that still enforces.

``conftest._wipe_public_tables`` truncates every ``public`` table between tests.
Several tables carry *append-only* guards installed by migrations
(``runtime_materialization_audit``, the M04E ledger, ``m06_partition_cutover_evidence``,
``agent_control_plane_events``), and those guards refuse ``UPDATE``/``DELETE``/
``TRUNCATE`` by design. The fixture therefore disables USER triggers for the
duration of the ``TRUNCATE`` and switches them back on.

That bypass is only acceptable if it is *provably* undone. If a run ever returns
with the guards disabled, the whole append-only test surface becomes vacuously
green — it would pass against a database that enforces nothing. These tests pin
that contract:

* every app-owned trigger is back to ``tgenabled = 'O'`` after the wipe;
* the known append-only triggers are still *present* (so the check above cannot
  pass by simply having no triggers left to inspect);
* the guard actually fires, i.e. the flag matches reality rather than being
  cosmetic;
* a wipe repairs a database that an earlier crash left with triggers disabled.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import (
    DATABASE_URL,
    _wipe_public_tables,
    public_user_trigger_states,
    requires_postgres,
)

APPEND_ONLY_TRIGGERS = {
    ("agent_control_plane_events", "trg_acp_events_append_only"),
    ("m04e_definition_version_audit", "trg_m04e_ledger_append_only_m04e_definition_version_audit"),
    (
        "m04e_definition_version_mappings",
        "trg_m04e_ledger_append_only_m04e_definition_version_mappings",
    ),
    (
        "m04e_definition_version_quarantine",
        "trg_m04e_ledger_append_only_m04e_definition_version_quarantine",
    ),
    ("m06_partition_cutover_evidence", "trg_m06_cutover_ledger_append_only"),
    ("runtime_materialization_audit", "trg_runtime_materialization_audit_append_only"),
    ("runtime_materialization_audit", "trg_runtime_materialization_audit_no_truncate"),
}
"""``(table, trigger)`` pairs whose only job is to reject mutation.

Migration-owned facts, mirrored here on purpose: if a migration drops one of these
the suite must notice, because ``test_..._enabled_after_wipe`` would otherwise
iterate over an empty (or smaller) set and pass for the wrong reason.
"""


def _run_db(coro_factory, *, timeout: float = 60.0) -> None:
    """Run async DB work on a fresh loop in a worker thread (Windows Proactor safe)."""

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro_factory())
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_runner).result(timeout=timeout)


@requires_postgres
def test_all_public_triggers_enabled_after_wipe() -> None:
    """The autouse wipe ran before this test; every app trigger must be back on."""
    states = public_user_trigger_states()
    assert states, "no app-owned triggers found in public — the catalog query is broken"

    disabled = [(table, trigger) for table, trigger, enabled in states if enabled != "O"]
    assert not disabled, (
        "the test wipe left USER triggers disabled, so append-only guards are not "
        f"enforced: {disabled}"
    )


@requires_postgres
def test_known_append_only_triggers_still_exist() -> None:
    """Guards the previous test against passing on an empty trigger set."""
    present = {(table, trigger) for table, trigger, _enabled in public_user_trigger_states()}
    missing = sorted(APPEND_ONLY_TRIGGERS - present)
    assert not missing, f"append-only triggers disappeared from the schema: {missing}"


@requires_postgres
def test_append_only_guard_rejects_truncate() -> None:
    """The flag is not cosmetic: a TRUNCATE of the audit table must still fail.

    Statement-level ``BEFORE TRUNCATE`` guard, so it fires without any rows and
    without touching tenant RLS.
    """

    async def _attempt_truncate() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("TRUNCATE runtime_materialization_audit"))
        finally:
            await engine.dispose()

    with pytest.raises(DBAPIError) as excinfo:
        _run_db(_attempt_truncate)
    assert "append-only" in str(excinfo.value), (
        "TRUNCATE of runtime_materialization_audit must fail with the append-only "
        f"guard, got: {excinfo.value}"
    )

    # The guard fired — that failure must not have disabled anything on the way out.
    assert [s for s in public_user_trigger_states() if s[2] != "O"] == []


@requires_postgres
def test_wipe_repairs_triggers_disabled_by_an_earlier_run() -> None:
    """A crashed run leaves guards off; the next wipe must restore them first.

    Without the entry repair, the follow-up wipe would truncate through a guard
    that is supposed to be live — the exact "falsely green" failure mode.
    """

    async def _disable_one() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "ALTER TABLE runtime_materialization_audit "
                        "DISABLE TRIGGER trg_runtime_materialization_audit_no_truncate"
                    )
                )
        finally:
            await engine.dispose()

    _run_db(_disable_one)
    disabled = [s for s in public_user_trigger_states() if s[2] != "O"]
    assert disabled, "the test could not disable a trigger, so it proves nothing"

    _wipe_public_tables()

    still_disabled = [(table, trigger) for table, trigger, enabled in public_user_trigger_states() if enabled != "O"]
    assert not still_disabled, f"wipe did not restore triggers disabled by a previous run: {still_disabled}"
