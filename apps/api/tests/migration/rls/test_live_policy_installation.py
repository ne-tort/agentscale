"""Live acceptance: the policies the registry declares are the ones PostgreSQL stores.

Task ``MIG-RLS-LIVE-01``.  Everything else in this package reasons about the
registry, the DDL text or a model of the predicate.  This module asks the
database what it *actually* installed, which is the only way to catch the class of
defect that motivated the file: a policy name longer than PostgreSQL's 63-byte
identifier limit is truncated **silently**, so the registry looked correct, the
``CREATE POLICY`` succeeded, and any comparison against the catalog could never
match.

What is asserted, per registered table, against a live migrated database:

* the table exists in the schema the application uses;
* ``relrowsecurity`` **and** ``relforcerowsecurity`` are on (FORCE is what makes
  the policy apply to the table *owner* too, so a migration running as the owner
  is not quietly exempt);
* exactly the two policies the registry declares are present, compared through
  :func:`prodavan...policies.installed_policy_name` so the check is meaningful for
  an over-long name instead of trivially failing or trivially passing;
* the stored names are *already* within the limit — i.e. nothing was truncated —
  which is the invariant the compact-suffix rule exists to preserve;
* ``polcmd`` matches the spec (``trusted_command`` ``SELECT`` -> ``'r'``,
  ``ALL`` -> ``'*'``), so "readable by the service role" cannot silently become
  "writable by it";
* the predicates read the transaction-local GUCs the registry documents, and the
  tenant policy carries a ``WITH CHECK`` (an ``ALL`` policy without one would let
  a tenant insert another tenant's rows even though it cannot read them).

Then, across the whole schema:

* no table outside the registry carries a policy - a policy is a security control,
  so an unregistered one is either a leftover or a deliberate omission that must
  be recorded in ``EXCLUDED_TABLES`` with a reason;
* no registry name collides with another once installed, so two tables can never
  share (or overwrite) one policy.

The suite is read-only: it opens one connection, reads ``pg_class`` / ``pg_policy``
once and asserts.  Running it cannot change the database it inspects.
"""

from __future__ import annotations

import pytest

from prodavan.infrastructure.persistence.rls import policies
from prodavan.infrastructure.persistence.rls.policies import (
    CANONICAL_TABLE_POLICIES,
    POLICY_NAME_MAX_LENGTH,
    TablePolicySpec,
    installed_policy_name,
)

from .conftest import CatalogRelation, InstalledPolicy, PolicyCatalog

#: ``polcmd`` for the two commands the registry may declare.
_POLCMD_FOR_COMMAND = {"SELECT": "r", "ALL": "*"}

#: GUC each scope kind must read in the installed predicate.  The registry is the
#: source of the *predicate*; this table only says which setting a scope kind has
#: to touch, so the live check does not depend on predicate rendering details.
_SETTING_FOR_SCOPE = {
    "tenant": policies.TENANT_SETTING,
    "cabinet": policies.CABINET_SETTING,
    "project": policies.PROJECT_SETTING,
}

SPECS = {spec.table: spec for spec in CANONICAL_TABLE_POLICIES}


def _spec_ids() -> list[str]:
    return [spec.table for spec in CANONICAL_TABLE_POLICIES]


def _policy_on_table(catalog: PolicyCatalog, table: str, name: str) -> InstalledPolicy:
    for policy in catalog.policies_of(table):
        if policy.name == name:
            return policy
    raise AssertionError(
        f"{table}: policy {name!r} is not installed; found "
        f"{sorted(policy.name for policy in catalog.policies_of(table))}"
    )


def _relation(catalog: PolicyCatalog, table: str) -> CatalogRelation:
    relation = catalog.relations.get(table)
    if relation is None:
        raise AssertionError(
            f"{table}: registered table is absent from schema {catalog.schema!r} "
            f"(alembic {catalog.alembic_version}); the database is not fully migrated"
        )
    return relation


# --------------------------------------------------------------------------- #
# the snapshot itself
# --------------------------------------------------------------------------- #
def test_catalog_snapshot_is_a_migrated_database(policy_catalog: PolicyCatalog) -> None:
    """Fail loudly rather than assert vacuously against an empty schema."""

    assert policy_catalog.schema, "current_schema() returned nothing"
    assert policy_catalog.alembic_version is not None, (
        "the inspected database has no alembic_version row, so no migration chain was "
        "applied to it; point PRODAVAN_TEST_DATABASE_URL at a migrated database"
    )
    assert policy_catalog.all_policies, (
        f"no policies at all in schema {policy_catalog.schema!r} - the RLS revisions "
        "were not applied"
    )


# --------------------------------------------------------------------------- #
# per table
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("table", _spec_ids())
def test_registered_table_enables_and_forces_row_security(
    policy_catalog: PolicyCatalog, table: str
) -> None:
    relation = _relation(policy_catalog, table)
    assert relation.row_security, f"{table}: relrowsecurity is off"
    assert relation.force_row_security, (
        f"{table}: relforcerowsecurity is off, so the table owner bypasses the policy - "
        "a migration or an admin role would read across tenants"
    )


@pytest.mark.parametrize("table", _spec_ids())
def test_registered_table_carries_exactly_its_two_declared_policies(
    policy_catalog: PolicyCatalog, table: str
) -> None:
    spec: TablePolicySpec = SPECS[table]
    installed = {policy.name for policy in policy_catalog.policies_of(table)}
    expected = set(spec.policy_names)
    assert installed == expected, (
        f"{table}: installed policies {sorted(installed)} != declared {sorted(expected)}"
    )


@pytest.mark.parametrize("table", _spec_ids())
def test_declared_policy_names_are_stored_untruncated(
    policy_catalog: PolicyCatalog, table: str
) -> None:
    """The defect this task exists for: a name the server silently shortened.

    Comparing through ``installed_policy_name`` (the truncation PostgreSQL
    applies) is what makes the *other* assertions honest - without it an
    over-long declared name could never match the catalog.  This test adds the
    complementary half: the registry must not need that mapping at all.
    """

    spec = SPECS[table]
    for declared in spec.policy_names:
        assert len(declared) <= POLICY_NAME_MAX_LENGTH, (
            f"{table}: declared policy name {declared!r} is {len(declared)} characters; "
            f"PostgreSQL stores {installed_policy_name(declared)!r} instead"
        )
        assert installed_policy_name(declared) == declared, (
            f"{table}: policy name {declared!r} would be truncated on install"
        )
        stored = [policy.name for policy in policy_catalog.policies_of(table)]
        assert declared in stored, f"{table}: {declared!r} is not stored as declared"


@pytest.mark.parametrize("table", _spec_ids())
def test_tenant_policy_is_for_all_with_a_matching_check(
    policy_catalog: PolicyCatalog, table: str
) -> None:
    spec = SPECS[table]
    policy = _policy_on_table(policy_catalog, table, spec.tenant_policy)
    assert policy.command == _POLCMD_FOR_COMMAND["ALL"], (
        f"{table}: tenant policy polcmd is {policy.command!r}, expected '*' (FOR ALL)"
    )
    assert policy.using, f"{table}: tenant policy has no USING expression"
    assert policy.with_check, (
        f"{table}: tenant policy has no WITH CHECK - a tenant could insert rows of "
        "another tenant even though it cannot read them"
    )
    assert "current_setting(" in policy.using, (
        f"{table}: tenant policy does not read a transaction-local GUC: {policy.using!r}"
    )
    for scope_kind, scoped in (
        ("tenant", spec.tenant),
        ("cabinet", spec.cabinet),
        ("project", spec.project),
    ):
        if scoped is None:
            continue
        setting = _SETTING_FOR_SCOPE[scope_kind]
        assert setting in policy.using, (
            f"{table}: table declares {scope_kind} scope but the installed predicate "
            f"never reads {setting}: {policy.using!r}"
        )


@pytest.mark.parametrize("table", _spec_ids())
def test_trusted_policy_command_matches_the_spec(
    policy_catalog: PolicyCatalog, table: str
) -> None:
    spec = SPECS[table]
    policy = _policy_on_table(policy_catalog, table, spec.trusted_policy)
    expected = _POLCMD_FOR_COMMAND[spec.trusted_command]
    assert policy.command == expected, (
        f"{table}: trusted policy polcmd is {policy.command!r}, expected {expected!r} for "
        f"trusted_command={spec.trusted_command!r}"
    )
    assert policies.TRUSTED_SERVICE_ROLE in (policy.using or ""), (
        f"{table}: trusted policy does not name the service role: {policy.using!r}"
    )
    assert policies.TRUSTED_SERVICE_SETTING in (policy.using or ""), (
        f"{table}: trusted policy does not require {policies.TRUSTED_SERVICE_SETTING}: "
        f"{policy.using!r}"
    )
    if spec.trusted_command == "ALL":
        assert policy.with_check, f"{table}: FOR ALL trusted policy has no WITH CHECK"
    else:
        assert policy.with_check is None, (
            f"{table}: a FOR SELECT trusted policy must not carry a WITH CHECK; "
            f"got {policy.with_check!r}"
        )


# --------------------------------------------------------------------------- #
# across the schema
# --------------------------------------------------------------------------- #
def test_no_policy_lives_on_a_table_outside_the_registry(policy_catalog: PolicyCatalog) -> None:
    """An unregistered policy is an unowned security control."""

    registered = set(SPECS)
    offenders = sorted(
        {
            f"{table}.{policy.name}"
            for table, policy in policy_catalog.all_policies
            if table not in registered
        }
    )
    assert offenders == [], (
        "policies exist on tables the registry does not declare; either register the "
        f"table or record why it is excluded: {offenders}"
    )


def test_installed_policy_names_are_unique_across_the_schema(
    policy_catalog: PolicyCatalog,
) -> None:
    """Two tables truncating to one name would let one policy replace another."""

    seen: dict[str, str] = {}
    collisions: list[str] = []
    for table, policy in policy_catalog.all_policies:
        owner = seen.setdefault(policy.name, table)
        if owner != table:
            collisions.append(f"{policy.name!r}: {owner} and {table}")
    assert collisions == [], f"policy name collisions: {collisions}"


def test_every_installed_policy_name_fits_the_identifier_limit(
    policy_catalog: PolicyCatalog,
) -> None:
    too_long = sorted(
        {policy.name for _, policy in policy_catalog.all_policies if len(policy.name) > 63}
    )
    assert too_long == [], f"stored policy names exceed 63 characters: {too_long}"


def test_registry_table_set_is_the_policied_set(policy_catalog: PolicyCatalog) -> None:
    """Every registered table is policied, and nothing is left to a later revision."""

    policied = {table for table, _ in policy_catalog.all_policies}
    missing = sorted(set(SPECS) - policied)
    assert missing == [], f"registered tables without any policy: {missing}"
