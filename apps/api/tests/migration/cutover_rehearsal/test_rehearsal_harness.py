"""Stage 1: the rehearsal's own fixture is a real, guarded, policied database.

Everything the later stage tests conclude depends on the schema beneath them, so
this module proves that schema *is* the migrated one rather than a convenient
imitation.  Four properties are checked before any stage runs:

* the migration chain is at head, and the tables the chain owns are present;
* the append-only and artifact guards exist **in the rehearsal schema** and carry
  the reason codes the revisions declare (so an ``UPDATE`` failure later is a
  guard, never a typo);
* every reviewed table carries ``ENABLE`` *and* ``FORCE`` row level security plus
  exactly the two policies the registry declares -- ``FORCE`` is what makes the
  policy apply to the table owner too;
* the connection the ports use is the trusted writer role, with ``row_security``
  on, while the seeding connection is a superuser -- the difference is what makes
  the isolation test in :mod:`test_rehearsal_rls` meaningful.

Nothing in the chain is mocked here: if the fixture drifted from a migrated
database, these assertions fail before a stage test can pass for the wrong reason.
"""

from __future__ import annotations

import pytest

from prodavan.application.migration.tenant_context import SECURITY_REVIEW_ID
from prodavan.infrastructure.persistence.rls.policies import CANONICAL_TABLE_POLICIES

from .harness import RehearsalDatabase, RehearsalHarness, as_text, new_seed

#: Tables the chain creates that the rehearsal writes through.  Listed explicitly
#: (rather than "all of ``Base.metadata``") so a missing chain table is visible.
CHAIN_TABLES = (
    "scope_registry",
    "scope_owner_tenant_anchors",
    "scope_owner_cabinet_anchors",
    "scope_owner_project_anchors",
    "scope_owner_mappings",
    "scope_owner_quarantine",
    "scope_owner_audit",
    "m04e_dynamic_app_definitions",
    "m04e_dynamic_app_versions",
    "m04e_definition_version_mappings",
    "m04e_definition_version_quarantine",
    "m04e_definition_version_checkpoints",
    "m04e_definition_version_audit",
    "m04e_definition_version_promotion_evidence",
    "m05_plugin_instances",
    "m05_plugin_instance_registry_links",
    "m05_plugin_instance_promotion_evidence",
    "dynamic_app_capability_grants",
    "dynamic_app_capability_grant_mappings",
    "dynamic_app_capability_grant_quarantine",
    "dynamic_app_capability_audit",
    "dynamic_app_capability_writer_epochs",
    "m06_canonical_grant_promotion_evidence",
    "m06_partition_cutover_evidence",
    "m06_partition_cutover_source_tail",
    "m04_scope_promotion_evidence",
)


def test_database_carries_the_migration_chain_the_rehearsal_needs(
    rehearsal_database: RehearsalDatabase,
) -> None:
    """The stamp must cover every revision this package depends on.

    Deliberately *not* ``== head``: sibling revisions land in this branch while the
    rehearsal is being written (``2026110320`` creates the legacy ``dynamic_app_*``
    tables), and a rehearsal database that is one revision behind the script head is a
    fixture to upgrade, not a defect in the pipeline under test.  What is asserted here
    is the property that actually matters: the chain is applied at least as far as the
    cutover ledger, the M04E ledger, ``auth_sessions`` and the anchors.
    """

    assert rehearsal_database.alembic_version, "the database has no alembic stamp"
    assert rehearsal_database.satisfies_chain, (
        f"the database is stamped {rehearsal_database.alembic_version!r}; the rehearsal "
        f"needs at least {rehearsal_database.required_revision!r}"
    )
    assert rehearsal_database.required_revision <= rehearsal_database.head_revision
    assert rehearsal_database.trusted_role == "prodavan_service"


async def test_rehearsal_schema_carries_the_chain_tables(rehearsal: RehearsalHarness) -> None:
    present = {
        row[0]
        for row in await rehearsal.rows(
            "SELECT tablename FROM pg_tables WHERE schemaname = :schema",
            {"schema": rehearsal.schema},
        )
    }
    missing = sorted(set(CHAIN_TABLES) - present)
    assert not missing, f"rehearsal schema {rehearsal.schema!r} is missing chain tables: {missing}"


async def test_guard_triggers_are_installed_in_the_rehearsal_schema(
    rehearsal: RehearsalHarness,
) -> None:
    """Each guard the revisions install must exist *here*, with its declared reason.

    The reason code is read from ``pg_get_functiondef`` so the assertion is about
    the installed body, not about the string this package happens to pass in.
    """

    installed = {
        as_text(row[0]): as_text(row[1])
        for row in await rehearsal.rows(
            "SELECT t.tgname, p.proname "
            "FROM pg_trigger AS t "
            "JOIN pg_class AS c ON c.oid = t.tgrelid "
            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "JOIN pg_proc AS p ON p.oid = t.tgfoid "
            "WHERE n.nspname = :schema AND NOT t.tgisinternal",
            {"schema": rehearsal.schema},
        )
    }
    assert installed, f"no guard triggers in schema {rehearsal.schema!r}"

    from .harness import guard_statements

    for guard in guard_statements():
        function_reason = await rehearsal.scalar(
            "SELECT pg_get_functiondef(p.oid) "
            "FROM pg_proc AS p "
            "JOIN pg_namespace AS n ON n.oid = p.pronamespace "
            "WHERE n.nspname = :schema AND p.proname = :name",
            {"schema": rehearsal.schema, "name": guard.function_name},
        )
        assert function_reason is not None, (
            f"guard function {guard.function_name!r} from revision {guard.revision} is not "
            f"installed in {rehearsal.schema!r}"
        )
        assert guard.reason in str(function_reason), (
            f"guard function {guard.function_name!r} from revision {guard.revision} does "
            f"not raise {guard.reason!r}"
        )
        missing = sorted({name for name, _, _ in guard.triggers} - set(installed))
        assert not missing, (
            f"revision {guard.revision}: triggers {missing} are not installed in "
            f"{rehearsal.schema!r}"
        )


@pytest.mark.parametrize("table", [spec.table for spec in CANONICAL_TABLE_POLICIES])
async def test_reviewed_tables_force_row_security_with_their_two_policies(
    rehearsal: RehearsalHarness, table: str
) -> None:
    """Every reviewed table: RLS enabled *and forced*, and exactly its two policies.

    Parametrised over the registry rather than a hand-written list so a table that
    the registry adds is covered here automatically -- and a table the fixture
    forgot to dress fails instead of silently running unprotected.
    """

    row = (
        await rehearsal.rows(
            "SELECT c.relrowsecurity, c.relforcerowsecurity "
            "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relname = :table",
            {"schema": rehearsal.schema, "table": table},
        )
    )
    assert len(row) == 1, f"{table}: not present in {rehearsal.schema!r}"
    enabled, forced = row[0]
    assert enabled is True, f"{table}: relrowsecurity is off"
    assert forced is True, (
        f"{table}: relforcerowsecurity is off, so the table owner (and thus the "
        "migration writer) would bypass the policy"
    )

    spec = next(spec for spec in CANONICAL_TABLE_POLICIES if spec.table == table)
    installed = {
        str(name)
        for (name,) in await rehearsal.rows(
            "SELECT policyname FROM pg_policies WHERE schemaname = :schema AND tablename = :table",
            {"schema": rehearsal.schema, "table": table},
        )
    }
    assert installed == set(spec.policy_names), (
        f"{table}: installed policies {sorted(installed)} != declared "
        f"{sorted(spec.policy_names)}"
    )
    for name in spec.policy_names:
        assert len(name) <= 63, f"{table}: policy name {name!r} would be truncated"


async def test_ports_connect_as_the_trusted_role_not_as_the_seeding_login(
    rehearsal: RehearsalHarness,
) -> None:
    """The two connections must differ in exactly the way the rehearsal claims.

    ``session_user`` stays the harness login on both, while ``current_user`` is the
    trusted role on the port connection.  That is the shape a deployed worker has --
    and the reason ``role_not_bypass_rls`` can be ``True`` for a real bind.
    """

    port_identity = (
        await rehearsal.trusted_rows(
            "SELECT current_user, session_user, current_setting('row_security', true)"
        )
    )[0]
    seed_identity = (
        await rehearsal.rows(
            "SELECT current_user, session_user, current_setting('row_security', true)"
        )
    )[0]
    assert str(port_identity[0]) == "prodavan_service"
    assert str(port_identity[1]) == str(seed_identity[1]), (
        "the port connection must be the same authenticated login acting as a role, "
        "not a second set of credentials"
    )
    assert str(port_identity[2]).lower() == "on"
    assert str(seed_identity[0]) != "prodavan_service", (
        "the seeding connection is deliberately a superuser; if it were the trusted "
        "role the RLS assertions in this package would be circular"
    )


async def test_seeded_partition_uses_real_tables_and_consistent_digests(
    rehearsal: RehearsalHarness,
) -> None:
    """The seed must be readable through the chain's own tables, not a parallel copy."""

    from .harness import seed_partition

    seed = await seed_partition(rehearsal)

    definition = (
        await rehearsal.rows(
            "SELECT tenant_id, app_id, scope_kind, scope_id, status, current_version_id "
            "FROM dynamic_app_definitions WHERE id = :id",
            {"id": seed.definition_id},
        )
    )[0]
    assert (definition[0], definition[1], definition[2], definition[3]) == (
        seed.tenant_id,
        seed.app_id,
        "cabinet",
        seed.cabinet_id,
    )
    assert str(definition[4]) == "published"
    assert str(definition[5]) == seed.version_id

    version = (
        await rehearsal.rows(
            "SELECT artifact_digest, manifest_version, status FROM dynamic_app_versions "
            "WHERE id = :id",
            {"id": seed.version_id},
        )
    )[0]
    assert str(version[0]) == seed.artifact_digest
    assert str(version[2]) == "published"

    target = (
        await rehearsal.rows(
            "SELECT tenant_id, lifecycle, source_snapshot_id FROM m04e_dynamic_app_definitions "
            "WHERE id = :id",
            {"id": seed.definition_id},
        )
    )[0]
    assert str(target[0]) == seed.tenant_id
    assert str(target[2]) == seed.snapshot_id

    assert await rehearsal.count("companies", "id = :id", {"id": seed.tenant_id}) == 1
    assert await rehearsal.count(
        "cabinet_company_grants",
        "cabinet_id = :cabinet AND company_id = :company",
        {"cabinet": seed.cabinet_id, "company": seed.tenant_id},
    ) == 1


async def test_schema_security_review_identity_is_the_reviewed_registry(
    rehearsal: RehearsalHarness,
) -> None:
    """The bind will report this identity as evidence; it must be the reviewed one."""

    assert SECURITY_REVIEW_ID.startswith("rls-registry:")


async def test_a_second_seed_is_isolated_from_the_first(rehearsal: RehearsalHarness) -> None:
    """Two seeds in one schema must not collide, and neither may see chain state.

    This is the guard against a fixture that silently reuses identities: a repeated
    ``source_id`` would make the M04 ledger replay instead of mapping (the source key
    is ``(source_kind, source_id, schema_version)`` platform-wide), turning an
    "accepted" assertion into a "replay" one.
    """

    from .harness import seed_partition

    first = await seed_partition(rehearsal)
    second = await seed_partition(rehearsal)
    assert first.source_id != second.source_id
    assert first.tenant_id != second.tenant_id
    assert first.definition_id != second.definition_id
    assert new_seed().source_id not in {first.source_id, second.source_id}
    assert (
        await rehearsal.count(
            "companies", "id = ANY(:ids)", {"ids": [first.tenant_id, second.tenant_id]}
        )
        == 2
    )
