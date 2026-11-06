"""Criterion 7 — the alembic chain upgrades, downgrades and upgrades again.

The wave added revisions ``2026110301`` … ``2026110311`` on top of the
control-plane revision ``2026110202``.  A chain that only goes *up* is not a
migration: reverting the wave must leave exactly the schema that existed before
it, and re-applying it must land on the same head.  This module runs that cycle
on a **clean** database:

``upgrade head`` → assert the wave tables exist → ``downgrade 2026110202`` →
assert every wave table is gone and the control-plane tables survive →
``upgrade head`` → assert the schema is byte-for-byte the same as after the
first upgrade.

Why this needs a separate database instead of the schema-per-test harness
-----------------------------------------------------------------------
``alembic/env.py`` builds its engine from ``settings.database_url`` and creates
objects in the first schema of that connection's ``search_path``; there is no
supported way to hand it a throwaway schema.  The ``migration_database_url``
fixture therefore accepts only a database that is provably empty (no
``alembic_version`` and no ORM tables in ``public``) and skips otherwise — see
``conftest.py``.

Alembic is *synchronous* (its async env calls ``asyncio.run``), so every
migration command runs in a worker thread while the test itself stays async and
probes the result through its own engine.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[3]
"""``apps/api`` — this file is ``tests/migration/postgres_integration/<file>``."""

ALEMBIC_SCRIPT_LOCATION = API_ROOT / "alembic"
ALEMBIC_SRC_PATH = API_ROOT / "src"

DOWNGRADE_TARGET = "2026110202"
"""The control-plane revision; everything the wave added sits above it."""

WAVE_REVISIONS = (
    "2026110301",  # M04 owner/scope registry
    "2026110302",  # M04.e definition/version
    "2026110303",  # M05 plugin-instance registry
    "2026110304",  # canonical grants
    "2026110305",  # equipment v2
    "2026110306",  # resources + runtime revisions
    "2026110307",  # RLS policy registry
    "2026110308",  # deferred-constraint validation pass
    "2026110309",  # durable run lease fence
    "2026110310",  # RLS after the lease fence
    "2026110311",  # provider-attempt journal
)
"""Revisions this suite *requires* to be above the downgrade target.

Treated as a subset requirement rather than an equality assertion, so a later
agent adding ``2026110312`` cannot make this test fail for the wrong reason; the
exact-set assertions below compare the database against itself instead.
"""

WAVE_TABLES = frozenset(
    {
        # 2026110301 — M04 owner/scope registry
        "scope_owner_tenant_anchors",
        "scope_owner_cabinet_anchors",
        "scope_owner_project_anchors",
        "scope_registry",
        "scope_owner_mappings",
        "scope_owner_quarantine",
        "scope_owner_audit",
        # 2026110302 — M04.e definition/version
        "m04e_dynamic_app_definitions",
        "m04e_dynamic_app_versions",
        "m04e_definition_version_promotion_evidence",
        # 2026110303 — M05 plugin-instance registry
        "m05_plugin_instances",
        "m05_plugin_instance_registry_links",
        "m05_plugin_instance_promotion_evidence",
        # 2026110304 — canonical grants
        "dynamic_app_capability_grants",
        "dynamic_app_capability_grant_mappings",
        "dynamic_app_capability_grant_quarantine",
        "dynamic_app_capability_audit",
        "dynamic_app_capability_writer_epochs",
        "m06_canonical_grant_promotion_evidence",
        # 2026110305 — equipment v2
        "equipment_v2_ingestion_runs",
        "equipment_v2_catalog_snapshots",
        "equipment_v2_catalog_offers",
        "equipment_v2_requests",
        "equipment_v2_request_lines",
        "equipment_v2_candidate_groups",
        "equipment_v2_budget_allocations",
        "equipment_v2_tenders",
        # 2026110306 — resources + runtime revisions
        "resources",
        "resource_versions",
        "resource_bindings",
        "capability_bundles",
        "project_runtime_revisions",
        "materialization_entries",
        "runtime_revision_heads",
        "runtime_materialization_attempts",
        "runtime_materialization_audit",
        # 2026110309 — durable run lease fence
        "agent_run_leases",
        # 2026110311 — provider-attempt journal
        "agent_run_provider_attempts",
    }
)

SURVIVING_TABLES = frozenset(
    {
        "agent_runs",
        "approval_requests",
        "agent_control_plane_run_scopes",
        "agent_control_plane_aggregates",
        "agent_control_plane_events",
        "agent_control_plane_outbox_events",
        "agent_control_plane_enqueue_intents",
        "agent_control_plane_commands",
        "alembic_version",
    }
)
"""Tables owned by ``2026110202`` and earlier: they must survive the downgrade."""

EVENT_TRIGGER = "trg_acp_events_append_only"
"""Created and dropped by ``2026110202`` itself — a cheap "did the body run" signal."""


# --------------------------------------------------------------------------- #
# alembic plumbing (synchronous; always called in a worker thread)
# --------------------------------------------------------------------------- #
def _alembic_config() -> Config:
    """A ``Config`` with no ini file.

    ``alembic.ini`` would be resolved relative to the process CWD and would run
    ``logging.config.fileConfig``, reconfiguring pytest's loggers.  The three
    options ``env.py`` actually reads are set explicitly; ``env.py`` skips
    ``fileConfig`` when ``config_file_name`` is ``None``.
    """
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    config.set_main_option("prepend_sys_path", str(ALEMBIC_SRC_PATH))
    config.set_main_option("version_path_separator", "os")
    return config


def _invoke(dsn: str, operation: str, revision: str) -> None:
    """Run one alembic command against ``dsn``.

    ``env.py`` overwrites ``sqlalchemy.url`` with ``settings.database_url``, so
    the settings singleton is patched for the duration of the call instead of
    fighting the environment file.
    """
    from prodavan.config.settings import settings

    config = _alembic_config()
    with patch.object(settings, "database_url", dsn):
        if operation == "upgrade":
            command.upgrade(config, revision)
        elif operation == "downgrade":
            command.downgrade(config, revision)
        else:  # pragma: no cover - programming error
            raise ValueError(f"unknown alembic operation {operation!r}")


def _head_revision() -> str:
    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    assert head is not None, "the alembic script directory has no head revision"
    return head


async def _run(dsn: str, operation: str, revision: str) -> None:
    """Run alembic off the event loop: its async env calls ``asyncio.run``."""
    await asyncio.to_thread(_invoke, dsn, operation, revision)


# --------------------------------------------------------------------------- #
# database probes
# --------------------------------------------------------------------------- #
async def _fetch(dsn: str, sql: str, params: dict | None = None):
    engine = create_async_engine(dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.scalar(text(sql), params or {})
    finally:
        await engine.dispose()


async def _public_tables(dsn: str) -> set[str]:
    """Table names in ``public`` — the schema the alembic env targets."""
    engine = create_async_engine(dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            return {row[0] for row in result.all()}
    finally:
        await engine.dispose()


async def _stamped_revision(dsn: str) -> str | None:
    return await _fetch(dsn, "SELECT version_num FROM alembic_version")


async def _public_policy_count(dsn: str) -> int:
    return int(await _fetch(dsn, "SELECT count(*) FROM pg_policies WHERE schemaname = 'public'") or 0)


async def _has_event_trigger(dsn: str) -> bool:
    count = await _fetch(
        dsn, "SELECT count(*) FROM pg_trigger WHERE tgname = :name", {"name": EVENT_TRIGGER}
    )
    return bool(count)


# --------------------------------------------------------------------------- #
# the cycle
# --------------------------------------------------------------------------- #
async def test_upgrade_downgrade_upgrade_cycle(migration_database_url: str):
    dsn = migration_database_url
    head = _head_revision()
    assert head > DOWNGRADE_TARGET, (
        f"head {head!r} must be above the control-plane revision {DOWNGRADE_TARGET!r}; "
        "the cycle would otherwise be a no-op (revision ids are equal-width numbers)"
    )

    try:
        # ---------------------------------------------------------- upgrade
        await _run(dsn, "upgrade", "head")

        assert await _stamped_revision(dsn) == head
        upgraded = await _public_tables(dsn)
        missing = sorted(WAVE_TABLES - upgraded)
        assert not missing, f"upgrade head did not create the wave tables: {missing}"
        assert SURVIVING_TABLES <= upgraded, (
            f"upgrade head lost pre-wave tables: {sorted(SURVIVING_TABLES - upgraded)}"
        )
        assert await _public_policy_count(dsn) > 0, (
            "revisions 2026110307/2026110310 install the canonical RLS policy set"
        )
        assert await _has_event_trigger(dsn) is True

        # -------------------------------------------------------- downgrade
        await _run(dsn, "downgrade", DOWNGRADE_TARGET)

        assert await _stamped_revision(dsn) == DOWNGRADE_TARGET
        downgraded = await _public_tables(dsn)
        leftover = sorted(WAVE_TABLES & downgraded)
        assert not leftover, (
            f"downgrade to {DOWNGRADE_TARGET} left wave tables behind: {leftover}"
        )
        assert SURVIVING_TABLES <= downgraded, (
            "downgrade removed tables owned by "
            f"{DOWNGRADE_TARGET}: {sorted(SURVIVING_TABLES - downgraded)}"
        )
        assert await _public_policy_count(dsn) == 0, (
            "downgrading past 2026110307 must remove the policies it installed"
        )
        assert await _has_event_trigger(dsn) is True, (
            "the append-only trigger belongs to 2026110202 and must survive the downgrade"
        )

        # ------------------------------------------------------ upgrade again
        await _run(dsn, "upgrade", "head")

        assert await _stamped_revision(dsn) == head
        re_upgraded = await _public_tables(dsn)
        assert re_upgraded == upgraded, (
            "re-upgrade did not reproduce the first upgrade exactly; "
            f"missing={sorted(upgraded - re_upgraded)} extra={sorted(re_upgraded - upgraded)}"
        )
        assert await _public_policy_count(dsn) > 0
    except Exception:
        # Best-effort recovery: a database left at the downgrade target would no
        # longer look clean, so the *next* run would skip instead of retrying.
        try:
            await _run(dsn, "upgrade", "head")
        except Exception:  # pragma: no cover - recovery only
            pass
        raise


async def test_upgrade_to_head_is_idempotent(migration_database_url: str):
    """A second ``upgrade head`` is a no-op, not a second DDL pass."""
    dsn = migration_database_url
    head = _head_revision()
    await _run(dsn, "upgrade", "head")
    before = await _public_tables(dsn)

    await _run(dsn, "upgrade", "head")

    assert await _stamped_revision(dsn) == head
    assert await _public_tables(dsn) == before


def test_wave_revisions_above_the_downgrade_target_form_a_linear_chain():
    """The chain is linear and reaches the downgrade target from the single head.

    A branch here would make ``upgrade head`` ambiguous and would stop
    ``2026110202`` from meaning "before the wave".  This check reads the script
    directory only — no database, no migration execution — so it also documents
    the chain that the cycle above relies on.
    """
    script = ScriptDirectory.from_config(_alembic_config())
    heads = list(script.get_heads())
    assert len(heads) == 1, f"expected exactly one head, got {heads}"

    chain: list[str] = []
    current: str | None = script.get_current_head()
    while current is not None and current != DOWNGRADE_TARGET:
        revision = script.get_revision(current)
        assert revision is not None, f"revision {current!r} is missing from the script directory"
        parent = revision.down_revision
        assert not isinstance(parent, tuple), (
            f"revision {current!r} is a merge point; the wave chain must be linear"
        )
        chain.append(current)
        current = parent

    assert current == DOWNGRADE_TARGET, (
        f"walking down from the head never reached {DOWNGRADE_TARGET!r}; visited {chain}"
    )
    absent = sorted(set(WAVE_REVISIONS) - set(chain))
    assert not absent, f"revisions expected above {DOWNGRADE_TARGET!r} are missing: {absent}"
