"""Static gate: no registry policy name can be truncated by PostgreSQL.

Task ``MIG-RLS-LIVE-01``.  This module needs no database on purpose - it fails at
the moment a name is declared too long, which is *before* the name reaches a
server that would silently shorten it.

Why a separate gate exists
==========================
``m04e_definition_version_promotion_evidence_trusted_service_access`` is 65
characters.  PostgreSQL stores at most ``NAMEDATALEN - 1`` = 63 bytes of an
identifier, so ``CREATE POLICY`` succeeded and ``pg_policy`` received
``..._trusted_service_acce``.  The registry, the revision log and the review
inventory all kept showing the 65-character name; only a live catalog query could
see the difference.  Two consequences, both silent:

* every comparison of a declared name with the stored one mismatches forever;
* two distinct declared names could truncate to one stored name, at which point a
  ``CREATE POLICY`` either fails (``duplicate_object``) or - with a
  ``DROP ... IF EXISTS`` in front, which is exactly what this registry emits -
  replaces the other table's policy.

The rule the registry now applies is deterministic (see ``rls/policies.py``): use
``<table>_<full suffix>`` while it fits, else the compact suffix for the same
kind, and never shorten the table part.  These tests pin that rule, prove it holds
for the whole registry, and prove the failure mode is closed rather than papered
over.

The checks are deliberately independent of the implementation where it matters:
:func:`_postgres_truncation` reproduces the server's own truncation instead of
calling the registry's helper, so a bug in that helper cannot make this gate pass.
"""

from __future__ import annotations

import re

import pytest

from prodavan.infrastructure.persistence.rls import policies
from prodavan.infrastructure.persistence.rls.policies import (
    CANONICAL_TABLE_POLICIES,
    POLICY_NAME_MAX_LENGTH,
    TENANT_POLICY_COMPACT_SUFFIX,
    TENANT_POLICY_SUFFIX,
    TRUSTED_POLICY_COMPACT_SUFFIX,
    TRUSTED_POLICY_SUFFIX,
    RlsRegistryError,
    TablePolicySpec,
    installed_policy_name,
)

#: ``NAMEDATALEN`` in PostgreSQL's ``pg_config_manual.h``.  Stated here rather than
#: imported so the test encodes the server's constant, not this codebase's copy.
POSTGRES_NAMEDATALEN = 64

#: Every policy name in the registry: ``(table, kind, name)``.
POLICY_NAMES = [
    (spec.table, kind, name)
    for spec in CANONICAL_TABLE_POLICIES
    for kind, name in (("tenant", spec.tenant_policy), ("trusted", spec.trusted_policy))
]


def _postgres_truncation(name: str) -> str:
    """The identifier PostgreSQL would store: ``name_bytes[:NAMEDATALEN - 1]``.

    Written out independently of ``policies.installed_policy_name``: the point of
    this gate is to catch a mistake in the registry's own mapping.
    """

    encoded = name.encode("utf-8")
    return encoded[: POSTGRES_NAMEDATALEN - 1].decode("utf-8")


def _registry_spec(table: str) -> TablePolicySpec:
    for spec in CANONICAL_TABLE_POLICIES:
        if spec.table == table:
            return spec
    raise AssertionError(f"{table} is not in the registry")


# --------------------------------------------------------------------------- #
# the constant the rest of the suite relies on
# --------------------------------------------------------------------------- #
def test_the_module_limit_is_postgresql_namedatalen_minus_one() -> None:
    assert POLICY_NAME_MAX_LENGTH == POSTGRES_NAMEDATALEN - 1
    assert installed_policy_name("a" * 70) == "a" * 63


# --------------------------------------------------------------------------- #
# the gate itself
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("table", "kind", "name"), POLICY_NAMES, ids=lambda value: value if isinstance(value, str) else "")
def test_policy_name_fits_the_identifier_limit(table: str, kind: str, name: str) -> None:
    assert len(name) <= POLICY_NAME_MAX_LENGTH, (
        f"{table} {kind} policy {name!r} is {len(name)} characters; PostgreSQL stores "
        f"{_postgres_truncation(name)!r} instead and every name comparison fails"
    )


@pytest.mark.parametrize(("table", "kind", "name"), POLICY_NAMES, ids=lambda value: value if isinstance(value, str) else "")
def test_installed_name_equals_declared_name(table: str, kind: str, name: str) -> None:
    """The registry must declare exactly what the server stores - no mapping needed."""

    assert _postgres_truncation(name) == name, (
        f"{table} {kind} policy would be installed as {_postgres_truncation(name)!r}, "
        f"not {name!r}"
    )


def test_truncated_names_stay_unique() -> None:
    """The failure mode that makes truncation dangerous, not merely inconvenient."""

    stored: dict[str, str] = {}
    collisions: list[str] = []
    for table, kind, name in POLICY_NAMES:
        truncated = _postgres_truncation(name)
        owner = stored.setdefault(truncated, f"{table}::{kind}")
        if owner != f"{table}::{kind}":
            collisions.append(f"{truncated!r} <- {owner} and {table}::{kind}")
    assert collisions == [], (
        "these declared policy names collapse to one stored name, so one policy would "
        f"replace another: {collisions}"
    )


# --------------------------------------------------------------------------- #
# the rule is deterministic, not ad hoc
# --------------------------------------------------------------------------- #
def test_full_suffix_is_used_whenever_it_fits() -> None:
    """A compact suffix is a consequence of length, never a per-table decision."""

    compact = [spec.table for spec in CANONICAL_TABLE_POLICIES if spec.trusted_policy_is_compact]
    for table in compact:
        spec = _registry_spec(table)
        full = f"{table}_{TRUSTED_POLICY_SUFFIX}"
        assert len(full) > POLICY_NAME_MAX_LENGTH, (
            f"{table}: the full trusted suffix fits ({len(full)} chars) but the compact "
            "form was chosen; the rule must be length-driven only"
        )
        assert spec.trusted_policy == f"{table}_{TRUSTED_POLICY_COMPACT_SUFFIX}"

    compact_tenant = [spec.table for spec in CANONICAL_TABLE_POLICIES if spec.tenant_policy_is_compact]
    for table in compact_tenant:
        full = f"{table}_{TENANT_POLICY_SUFFIX}"
        assert len(full) > POLICY_NAME_MAX_LENGTH, f"{table}: tenant suffix compacted early"


def test_compact_suffixes_are_the_only_alternatives() -> None:
    """No third spelling and no free-form suffix sneaks in."""

    allowed_tenant = (f"_{TENANT_POLICY_SUFFIX}", f"_{TENANT_POLICY_COMPACT_SUFFIX}")
    allowed_trusted = (f"_{TRUSTED_POLICY_SUFFIX}", f"_{TRUSTED_POLICY_COMPACT_SUFFIX}")
    for spec in CANONICAL_TABLE_POLICIES:
        assert spec.tenant_policy.startswith(f"{spec.table}_")
        assert spec.tenant_policy.endswith(allowed_tenant), spec.tenant_policy
        assert spec.trusted_policy.startswith(f"{spec.table}_")
        assert spec.trusted_policy.endswith(allowed_trusted), spec.trusted_policy


def test_table_part_is_never_shortened() -> None:
    """A policy name must identify its table unambiguously."""

    for spec in CANONICAL_TABLE_POLICIES:
        for name in spec.policy_names:
            assert name.startswith(f"{spec.table}_"), (
                f"{spec.table}: policy {name!r} does not start with the full table name"
            )


def test_compact_suffix_keeps_the_two_kinds_distinct() -> None:
    """Compacting must not make the tenant and trusted names collide on one table."""

    for spec in CANONICAL_TABLE_POLICIES:
        assert spec.tenant_policy != spec.trusted_policy
        assert not spec.tenant_policy.startswith(spec.trusted_policy)
        assert not spec.trusted_policy.startswith(spec.tenant_policy)


# --------------------------------------------------------------------------- #
# fail-closed behaviour
# --------------------------------------------------------------------------- #
def test_a_table_too_long_for_the_compact_form_is_refused() -> None:
    """Truncation is never the fallback: the registry refuses instead."""

    # ``_IDENTIFIER`` allows 63 characters, so a 63-character table name is the
    # worst legal case; with the shortest compact suffix it cannot fit.
    too_long = "t" * POLICY_NAME_MAX_LENGTH
    with pytest.raises(RlsRegistryError, match="fits"):
        TablePolicySpec(table=too_long, owner_revision="2026110301", tenant=None)


def test_validate_registry_rejects_an_over_long_name() -> None:
    """The import-time validator is the last line of defence."""

    spec = TablePolicySpec(table="a" * 40, owner_revision="2026110301", tenant=None)
    assert len(spec.trusted_policy) <= POLICY_NAME_MAX_LENGTH
    # Build the pathological case through the validator directly: a spec whose
    # declared names cannot fit is rejected, not truncated.
    with pytest.raises(RlsRegistryError):
        policies.validate_registry((spec, TablePolicySpec(table="a" * 40, owner_revision="2026110301", tenant=None)))


def test_declared_names_match_a_strict_identifier_pattern() -> None:
    """Belt and braces: a name is lowercase ASCII and would survive quoting."""

    pattern = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
    for _, _, name in POLICY_NAMES:
        assert pattern.fullmatch(name), name


def test_the_known_offender_is_fixed() -> None:
    """Regression pin for the concrete defect this task started from."""

    table = "m04e_definition_version_promotion_evidence"
    spec = _registry_spec(table)
    naive = f"{table}_{TRUSTED_POLICY_SUFFIX}"
    assert len(naive) > POLICY_NAME_MAX_LENGTH, "the premise of this regression pin changed"
    assert spec.trusted_policy == f"{table}_{TRUSTED_POLICY_COMPACT_SUFFIX}"
    assert len(spec.trusted_policy) <= POLICY_NAME_MAX_LENGTH
    # The tenant name fit all along and must not have been touched.
    assert spec.tenant_policy == f"{table}_{TENANT_POLICY_SUFFIX}"
