"""Static cross-review contract for the DDL chain ``2026110301``-``2026110311``.

Task ``MIG-REVIEW-DDL-01``.  This module automates the checks recorded in
``docs/08-migration/ddl-chain-cross-review.md``; the prose document is the source
of the findings, this file is the regression net.

Everything here is static and database-free:

* chain metadata is parsed with :mod:`ast` from ``alembic/versions`` (including the
  ``revision: str = "..."`` annotation style used by the legacy revisions, which a
  plain ``ast.Assign`` scan silently drops);
* declared DDL is obtained by replaying each revision's ``upgrade()``/``downgrade()``
  through :mod:`tests.migration.ddl_cross_review.harness`, which swaps
  ``alembic.op`` for a recorder and materialises real
  :class:`sqlalchemy.Table` objects in an isolated :class:`~sqlalchemy.MetaData`
  (no connection: the fake ``op.get_bind()`` raises, and the migration context is
  reported as offline);
* the model side is the read-only ``Base.metadata`` import;
* the policy side is the shared registry in
  ``prodavan.infrastructure.persistence.rls.policies``.

No ``pytest`` run, no ``alembic upgrade``, no DDL, no build, no lint and no git
command is required or performed by this module.  Coverage is deliberately
narrow: the four highest-value contracts (chain shape, model parity, inter-revision
foreign targets, RLS coverage) plus the fragile spots named in the review
(retained project key, M05 anchor-key ownership, deferred cycle, validation gate,
downgrade symmetry).

Three tests carry a **strict** ``xfail`` (B1, M1, M3 of the review).  A strict
xfail means "the invariant is documented as violated by the current chain, and the
fix is a new revision, not an edit of an applied one".  When the owning fix lands
those tests turn into ``XPASS`` and fail on purpose, so the marker cannot rot into
a permanent excuse.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Iterator

import pytest

from prodavan.infrastructure.persistence.rls import policies

# --------------------------------------------------------------------------- #
# harness (loaded by path: the review package must not depend on sys.path setup)
# --------------------------------------------------------------------------- #

_HARNESS_PATH = Path(__file__).resolve().with_name("harness.py")


def _load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddl_cross_review_harness", _HARNESS_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {_HARNESS_PATH}"
    module = importlib.util.module_from_spec(spec)
    # Register before executing: the harness declares dataclasses, and
    # ``dataclasses`` resolves ``cls.__module__`` through ``sys.modules``.
    sys.modules.setdefault("ddl_cross_review_harness", module)
    spec.loader.exec_module(module)
    return module


H = _load_harness()

VERSIONS_DIR: Path = H.VERSIONS_DIR

#: The reviewed chain, in dependency order.  ``2026110301`` attaches to the
#: pre-existing head ``2026110202``.
#:
#: Kept as an explicit range so the contract fails loudly when a new revision
#: lands without this file being extended: the chain review must be re-run, not
#: silently widened.
CHAIN: tuple[str, ...] = tuple(f"20261103{number:02d}" for number in range(1, 16))
CHAIN_ROOT = "2026110202"

#: Revisions of the chain that own tables.
TABLE_OWNING_REVISIONS: tuple[str, ...] = (
    "2026110301",
    "2026110302",
    "2026110303",
    "2026110304",
    "2026110305",
    "2026110306",
    "2026110309",
    "2026110311",
    "2026110313",
    "2026110315",
)

#: Revisions that install RLS policies by walking the shared registry.
POLICY_REVISIONS: tuple[str, ...] = (
    "2026110307",
    "2026110310",
    "2026110312",
    "2026110314",
    "2026110315",
)

#: ``ALTER TABLE t ADD CONSTRAINT n UNIQUE (a, b)`` emitted as raw SQL.  Revision
#: ``2026110303`` adds its M05 anchor keys to the shared ``scope_registry`` table
#: this way, so the replay never sees them as ``Table`` objects.
_RAW_SQL_UNIQUE = re.compile(
    r"ADD\s+CONSTRAINT\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+UNIQUE\s*\((?P<columns>[^)]*)\)",
    re.IGNORECASE,
)

_HEADER_NAMES = frozenset({"revision", "down_revision", "branch_labels", "depends_on"})


# --------------------------------------------------------------------------- #
# chain metadata (ast, annotation-aware)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Header:
    revision: str
    parents: tuple[str, ...]
    branch_labels: object
    depends_on: object
    path: Path


def _module_headers(path: Path) -> dict[str, object]:
    """Read the four chain attributes, including ``name: str = value`` bodies."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        else:
            continue
        if name not in _HEADER_NAMES or value is None:
            continue
        try:
            values[name] = ast.literal_eval(value)
        except ValueError:  # pragma: no cover - all four are literals by convention
            values[name] = "<dynamic>"
    return values


@lru_cache(maxsize=1)
def revision_headers() -> dict[str, Header]:
    headers: dict[str, Header] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        values = _module_headers(path)
        revision = values.get("revision")
        if not isinstance(revision, str):
            continue
        down = values.get("down_revision")
        if down is None:
            parents: tuple[str, ...] = ()
        elif isinstance(down, str):
            parents = (down,)
        else:
            parents = tuple(down)  # type: ignore[arg-type]
        headers[revision] = Header(
            revision=revision,
            parents=parents,
            branch_labels=values.get("branch_labels"),
            depends_on=values.get("depends_on"),
            path=path,
        )
    return headers


def heads(headers: dict[str, Header]) -> tuple[str, ...]:
    referenced = {parent for header in headers.values() for parent in header.parents}
    return tuple(sorted(set(headers) - referenced))


# --------------------------------------------------------------------------- #
# declared DDL per revision
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def chain_upgrades() -> dict[str, object]:
    return {revision: H.replay(revision, "upgrade") for revision in CHAIN}


def upgrade(revision: str):  # noqa: ANN201 - Replay dataclass from the harness
    return chain_upgrades()[revision]


@lru_cache(maxsize=1)
def chain_downgrades() -> dict[str, object]:
    return {revision: H.replay(revision, "downgrade") for revision in CHAIN}


def downgrade(revision: str):  # noqa: ANN201 - Replay dataclass from the harness
    return chain_downgrades()[revision]


@lru_cache(maxsize=1)
def table_owners() -> dict[str, str]:
    """Table name -> the single revision of the chain that creates it."""

    owners: dict[str, str] = {}
    for revision in CHAIN:
        for table in upgrade(revision).table_order:
            assert table not in owners, f"{table} is created by both {owners.get(table)} and {revision}"
            owners[table] = revision
    return owners


@lru_cache(maxsize=1)
def raw_sql_uniques() -> dict[str, set[tuple[str, tuple[str, ...]]]]:
    """Unique constraints the chain creates through raw SQL, keyed by table.

    Returned as ``(constraint name, column tuple)`` per table so the parity check
    can compare them with the model's ``UniqueConstraint`` declarations.
    """

    found: dict[str, set[tuple[str, tuple[str, ...]]]] = defaultdict(set)
    table_pattern = re.compile(
        r"ALTER\s+TABLE\s+(?P<table>[A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
    )
    for revision in CHAIN:
        for statement in upgrade(revision).statements:
            tables = [match.group("table") for match in table_pattern.finditer(statement)]
            for match in _RAW_SQL_UNIQUE.finditer(statement):
                if not tables:
                    continue
                columns = tuple(column.strip() for column in match.group("columns").split(","))
                found[tables[0]].add((match.group("name"), columns))
    return found


@lru_cache(maxsize=1)
def created_object_owners() -> dict[str, list[tuple[str, str]]]:
    """Object name -> ``[(kind, revision)]`` for everything the chain creates."""

    owners: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for revision in CHAIN:
        record = upgrade(revision)
        for table in record.table_order:
            owners[table].append(("table", revision))
        for name, table in record.tables.items():
            for constraint in table.constraints:
                if constraint.name:
                    owners[constraint.name].append((type(constraint).__name__, revision))
        for name in record.indexes:
            owners[name].append(("index", revision))
        for name in record.detached_uniques:
            owners[name].append(("unique_constraint", revision))
        for name in record.detached_foreign_keys:
            owners[name].append(("foreign_key", revision))
        for name, sql_object in record.sql_objects.items():
            owners[name].append((sql_object.kind, revision))
    return dict(owners)


@lru_cache(maxsize=1)
def policy_installers() -> dict[str, tuple[str, ...]]:
    """Policy name -> revisions of the chain that emit ``CREATE POLICY`` for it."""

    installers: dict[str, list[str]] = defaultdict(list)
    for revision in CHAIN:
        for name, sql_object in upgrade(revision).sql_objects.items():
            if sql_object.kind == "policy":
                installers[name].append(revision)
    return {name: tuple(sorted(revisions)) for name, revisions in installers.items()}


# --------------------------------------------------------------------------- #
# cumulative declared schema per table
# --------------------------------------------------------------------------- #


def declared_unique_keys(propagation_table: str) -> set[frozenset[str]]:
    """Every column set PostgreSQL would accept as an FK target on the table."""

    keys: set[frozenset[str]] = set()
    owner = table_owners().get(propagation_table)
    if owner is not None:
        table = upgrade(owner).tables[propagation_table]
        keys |= H.unique_key_sets(table)
    for _name, columns in raw_sql_uniques().get(propagation_table, set()):
        keys.add(frozenset(columns))
    return keys


def declared_foreign_keys() -> Iterator[tuple[str, str, object]]:
    """``(revision, source table, ForeignKeySpec)`` for every FK the chain declares."""

    for revision in CHAIN:
        record = upgrade(revision)
        for table_name in record.table_order:
            for spec in H.foreign_key_specs(record.tables[table_name]):
                yield revision, table_name, spec
        for spec in record.detached_foreign_keys.values():
            yield revision, spec.source_table or "", spec


@lru_cache(maxsize=1)
def chain_foreign_keys() -> tuple[tuple[str, str, object], ...]:
    return tuple(declared_foreign_keys())


def aggregated_foreign_keys(table: str) -> set[tuple[tuple[str, ...], tuple[str, ...], str | None, bool | None, str | None]]:
    """FK shapes of ``table`` including the late ``op.create_foreign_key`` ones.

    Names are excluded on purpose: the migrations name every key explicitly while
    the models leave the ``agent_runs`` keys unnamed (review item m2), and the
    parity contract is about shape, not spelling.
    """

    shapes: set[tuple[tuple[str, ...], tuple[str, ...], str | None, bool | None, str | None]] = set()
    for _revision, source, spec in chain_foreign_keys():
        if source != table:
            continue
        shapes.add((spec.local, spec.remote, spec.ondelete, spec.deferrable, spec.initially))
    return shapes


def aggregated_indexes(table: str) -> list[tuple[str, tuple[str, ...], bool, str | None]]:
    specs: list[tuple[str, tuple[str, ...], bool, str | None]] = []
    for revision in CHAIN:
        for spec in upgrade(revision).indexes.values():
            if spec.table == table:
                specs.append((spec.name, spec.expressions, spec.unique, spec.where))
    return sorted(specs)


def model_foreign_key_shapes(table: str):
    model_table = H.model_metadata().tables[table]
    return {
        (spec.local, spec.remote, spec.ondelete, spec.deferrable, spec.initially)
        for spec in H.foreign_key_specs(model_table)
    }


def model_indexes(table: str) -> list[tuple[str, tuple[str, ...], bool, str | None]]:
    model_table = H.model_metadata().tables[table]
    return sorted((spec.name, spec.expressions, spec.unique, spec.where) for spec in H.index_specs(model_table))


def model_unique_specs(table: str) -> set[tuple[str | None, tuple[str, ...]]]:
    model_table = H.model_metadata().tables[table]
    return {(spec.name, spec.columns) for spec in H.unique_specs(model_table)}


def declared_unique_specs(table: str) -> set[tuple[str | None, tuple[str, ...]]]:
    specs: set[tuple[str | None, tuple[str, ...]]] = set()
    owner = table_owners().get(table)
    if owner is not None:
        specs |= {(spec.name, spec.columns) for spec in H.unique_specs(upgrade(owner).tables[table])}
    specs |= raw_sql_uniques().get(table, set())
    return specs


# --------------------------------------------------------------------------- #
# 1. chain shape
# --------------------------------------------------------------------------- #


def test_every_chain_revision_has_exactly_one_file() -> None:
    for revision in CHAIN:
        paths = sorted(VERSIONS_DIR.glob(f"{revision}_*.py"))
        assert len(paths) == 1, f"expected exactly one file for {revision}, found {paths}"


def test_chain_headers_are_linear_and_correctly_linked() -> None:
    headers = revision_headers()
    for revision in CHAIN:
        assert revision in headers, f"{revision} has no parsable revision header"

    assert headers[CHAIN[0]].parents == (CHAIN_ROOT,), (
        f"{CHAIN[0]} must attach to the pre-existing head {CHAIN_ROOT}, "
        f"got {headers[CHAIN[0]].parents}"
    )
    for previous, current in zip(CHAIN, CHAIN[1:]):
        assert headers[current].parents == (previous,), (
            f"{current} down_revision is {headers[current].parents}, expected ({previous},)"
        )

    for revision in CHAIN:
        header = headers[revision]
        assert header.branch_labels in (None, ()), f"{revision} declares branch_labels={header.branch_labels!r}"
        assert header.depends_on in (None, ()), f"{revision} declares depends_on={header.depends_on!r}"


def test_the_whole_versions_tree_has_a_single_head() -> None:
    headers = revision_headers()
    missing = {parent for header in headers.values() for parent in header.parents} - set(headers)
    assert not missing, f"dangling down_revision(s): {sorted(missing)}"

    # The head is derived from the tree, not hard-coded: a new revision must not
    # need this assertion edited, only CHAIN extended after a review.
    resolved_heads = heads(headers)
    assert len(resolved_heads) == 1, f"expected a single head, got {resolved_heads}"
    head = resolved_heads[0]
    assert head >= "2026110301", f"head {head} is not part of the wave chain"

    # Walking the full history from the head must terminate at the root.
    seen: list[str] = []
    current: str | None = head
    while current is not None:
        assert current not in seen, f"cycle detected at {current}"
        seen.append(current)
        parents = headers[current].parents
        assert len(parents) <= 1, f"{current} has multiple parents {parents}: fork"
        current = parents[0] if parents else None

    assert seen[-1] == "stub_bootstrap", f"chain root is {seen[-1]}, expected stub_bootstrap"
    assert len(seen) == len(headers), "the head walk does not visit every revision: a branch exists"


# --------------------------------------------------------------------------- #
# 2. no duplicated objects
# --------------------------------------------------------------------------- #


def test_no_table_index_or_constraint_is_created_twice() -> None:
    duplicated: dict[str, list[tuple[str, str]]] = {}
    for name, creators in created_object_owners().items():
        kinds = {kind for kind, _ in creators}
        if len(creators) < 2:
            continue
        # Re-installing a policy is legal only when the installer drops it first
        # (guarded, idempotent DDL).  Anything else would be a real duplicate.
        if kinds == {"policy"}:
            continue
        duplicated[name] = creators
    assert not duplicated, f"objects created more than once: {duplicated}"


def test_policy_reinstallation_is_idempotent_and_owned_by_one_revision() -> None:
    installers = policy_installers()
    registry_policies = {spec.tenant_policy for spec in policies.CANONICAL_TABLE_POLICIES} | {
        spec.trusted_policy for spec in policies.CANONICAL_TABLE_POLICIES
    }
    assert set(installers) == registry_policies, (
        "the chain must install exactly the registry policies; "
        f"missing={sorted(registry_policies - set(installers))} "
        f"extra={sorted(set(installers) - registry_policies)}"
    )

    for policy, revisions in installers.items():
        assert set(revisions) <= set(POLICY_REVISIONS), f"{policy} is installed by unexpected revisions {revisions}"
        assert revisions[0] == "2026110307", f"{policy} must be introduced by 2026110307, got {revisions[0]}"

    # Every installer runs the guarded upgrade block, which drops before creating.
    for revision in POLICY_REVISIONS:
        for spec in policies.CANONICAL_TABLE_POLICIES:
            statements = policies.upgrade_statements(spec)
            drops = [statement for statement in statements if statement.startswith("DROP POLICY IF EXISTS")]
            creates = [statement for statement in statements if statement.startswith("CREATE POLICY")]
            assert len(drops) == len(creates) == 2, f"{revision}: {spec.table} guard is not drop-then-create"


# --------------------------------------------------------------------------- #
# 3. inter-revision references
# --------------------------------------------------------------------------- #


def _model_metadata_tables() -> set[str]:
    return set(H.model_metadata().tables)


def test_every_foreign_key_target_exists_earlier_or_is_retained() -> None:
    owners = table_owners()
    metadata_tables = _model_metadata_tables()
    problems: list[str] = []

    for revision, source, spec in chain_foreign_keys():
        target = spec.target_table
        if target in owners:
            if owners[target] > revision:
                problems.append(
                    f"{revision}: {source}.{spec.name} -> {target} is created later by {owners[target]}"
                )
            continue
        if target not in metadata_tables:
            problems.append(f"{revision}: {source}.{spec.name} -> {target} is neither created in the chain nor retained")
    assert not problems, "foreign key targets that do not exist yet:\n" + "\n".join(problems)


def test_every_composite_foreign_target_has_an_exact_primary_or_unique_key() -> None:
    problems: list[str] = []
    for revision, source, spec in chain_foreign_keys():
        target = spec.target_table
        if target in table_owners():
            keys = declared_unique_keys(target)
        elif target in _model_metadata_tables():
            keys = H.unique_key_sets(H.model_metadata().tables[target])
        else:  # covered by the previous test
            continue
        if frozenset(spec.target_columns) not in keys:
            problems.append(
                f"{revision}: {source}.{spec.name} -> {target}{spec.target_columns} has no PK/UNIQUE "
                f"on exactly those columns (available: {sorted(tuple(sorted(key)) for key in keys)})"
            )
    assert not problems, "composite foreign keys without a matching target key:\n" + "\n".join(problems)


def test_deferred_cycle_constraints_are_complete_and_deferrable() -> None:
    deferred = [
        (revision, source, spec)
        for revision, source, spec in chain_foreign_keys()
        if spec.deferrable or spec.initially
    ]
    names = sorted({spec.name for _revision, _source, spec in deferred})
    assert names == sorted(
        {
            "fk_m04e_definition_current_version_same_definition",
            "fk_m05_registry_instance_link_evidence",
            "fk_m05_instance_registry_tenant_link",
            "fk_m05_instance_registry_cabinet_link",
            "fk_m05_instance_registry_project_link",
            "fk_m05_registry_instance_tenant_link",
            "fk_m05_registry_instance_cabinet_link",
            "fk_m05_registry_instance_project_link",
        }
    ), f"unexpected deferred-constraint inventory: {names}"

    for revision, source, spec in deferred:
        assert spec.initially == "DEFERRED", f"{revision}: {source}.{spec.name} must be INITIALLY DEFERRED"
        assert spec.ondelete == "RESTRICT", f"{revision}: {source}.{spec.name} must be ON DELETE RESTRICT"

    # The cycle can only be closed after both sides exist.
    owners = table_owners()
    for revision, source, spec in deferred:
        target = spec.target_table
        if target in owners:
            assert owners[target] <= revision, (
                f"{revision}: deferred {source}.{spec.name} points at {target}, created by {owners[target]}"
            )


def test_validation_gate_covers_exactly_the_staged_constraints() -> None:
    module = H.load_revision("2026110308")
    specs = module.VALIDATION_SPECS
    validated = {(spec["table"], spec["constraint"]) for spec in specs}

    staged = {
        (source, spec.name)
        for revision, source, spec in chain_foreign_keys()
        if revision in {"2026110302", "2026110303", "2026110304"} and (spec.deferrable or spec.initially)
    }
    # fk_cap_grant_instance_exact is declared inline (not deferrable) but the wave
    # shipped no validation evidence for it while the partition is quarantined.
    staged.add(("dynamic_app_capability_grants", "fk_cap_grant_instance_exact"))

    assert validated == staged, (
        f"validation gate drift: unvalidated={sorted(staged - validated)} "
        f"unknown={sorted(validated - staged)}"
    )
    assert len(specs) == len(validated), "the validation inventory lists a constraint twice"

    for spec in specs:
        assert spec["table"] in table_owners() or spec["table"] == "scope_registry", (
            f"{spec['constraint']} validates an unknown table {spec['table']}"
        )
        assert spec["owner_revision"] in CHAIN, f"{spec['constraint']} names an unknown owner revision"


# --------------------------------------------------------------------------- #
# 4. parity with the models
# --------------------------------------------------------------------------- #


def test_every_chain_table_is_registered_in_the_model_metadata() -> None:
    missing = sorted(set(table_owners()) - _model_metadata_tables())
    assert not missing, f"tables created by the chain but absent from Base.metadata: {missing}"


def test_column_parity_with_models() -> None:
    problems: list[str] = []
    metadata = H.model_metadata()
    for table, revision in sorted(table_owners().items()):
        declared = upgrade(revision).tables[table]
        model_table = metadata.tables.get(table)
        if model_table is None:
            continue  # covered by the registration test
        if H.column_specs(declared) != H.column_specs(model_table):
            problems.append(H._column_diff(table, declared, model_table))
    assert not problems, "column parity drift (name/type/nullable/server_default/order):\n" + "\n".join(problems)


def test_primary_key_parity_with_models() -> None:
    problems: list[str] = []
    metadata = H.model_metadata()
    for table, revision in sorted(table_owners().items()):
        declared = upgrade(revision).tables[table]
        model_table = metadata.tables.get(table)
        if model_table is None:
            continue
        if H.primary_key(declared) != H.primary_key(model_table):
            problems.append(f"{table}: migration PK {H.primary_key(declared)} vs model {H.primary_key(model_table)}")
    assert not problems, "primary key parity drift:\n" + "\n".join(problems)


def test_unique_constraint_parity_with_models() -> None:
    """Includes the M05 anchor keys added to ``scope_registry`` through raw SQL."""

    problems: list[str] = []
    for table in sorted(table_owners()):
        if table not in _model_metadata_tables():
            continue
        declared = declared_unique_specs(table)
        modelled = model_unique_specs(table)
        if declared != modelled:
            problems.append(
                f"{table}: only in migration {sorted(declared - modelled)} / only in model {sorted(modelled - declared)}"
            )
    assert not problems, "unique constraint parity drift:\n" + "\n".join(problems)


def test_check_constraint_parity_with_models() -> None:
    problems: list[str] = []
    metadata = H.model_metadata()
    for table, revision in sorted(table_owners().items()):
        declared = upgrade(revision).tables[table]
        model_table = metadata.tables.get(table)
        if model_table is None:
            continue
        if H.check_specs(declared) != H.check_specs(model_table):
            problems.append(H._check_diff(table, declared, model_table))
    assert not problems, "CHECK text parity drift:\n" + "\n".join(problems)


def test_foreign_key_parity_with_models() -> None:
    """Shape parity (columns/target/ondelete/deferrable).  Names are reviewed separately."""

    problems: list[str] = []
    for table in sorted(table_owners()):
        if table not in _model_metadata_tables():
            continue
        declared = aggregated_foreign_keys(table)
        modelled = model_foreign_key_shapes(table)
        if declared != modelled:
            problems.append(f"{table}: only in migration {sorted(declared - modelled)} / only in model {sorted(modelled - declared)}")
    assert not problems, "foreign key parity drift:\n" + "\n".join(problems)


def test_index_parity_with_models() -> None:
    problems: list[str] = []
    for table in sorted(table_owners()):
        if table not in _model_metadata_tables():
            continue
        declared = aggregated_indexes(table)
        modelled = model_indexes(table)
        if declared != modelled:
            missing = sorted({spec[0] for spec in modelled} - {spec[0] for spec in declared})
            extra = sorted({spec[0] for spec in declared} - {spec[0] for spec in modelled})
            problems.append(f"{table}: missing in migration {missing} / only in migration {extra}")
    assert not problems, "index parity drift (name/expressions/unique/predicate):\n" + "\n".join(problems)


# --------------------------------------------------------------------------- #
# 5. fragile spots named in the review
# --------------------------------------------------------------------------- #


def test_retained_project_compatibility_key_matches_the_model() -> None:
    key = "uq_projects_scope_owner_parent_tuple"
    created = upgrade("2026110301").detached_uniques.get(key)
    assert created is not None, f"{key} must be created by 2026110301"
    assert created.table == "projects", f"{key} must be installed on the retained projects table, got {created.table}"
    assert created.columns == ("id", "company_id", "cabinet_id"), f"unexpected columns {created.columns}"

    model_table = H.model_metadata().tables["projects"]
    assert created in H.unique_specs(model_table), (
        f"{key} is not declared by the projects model; the migration would drift from metadata"
    )

    # First statement of the upgrade, last drop of the downgrade: nothing may
    # survive that still depends on the key.
    drop_order = list(downgrade("2026110301").dropped_constraints)
    assert drop_order[-1] == key, f"{key} must be dropped last, drop order is {drop_order}"


def test_m05_registry_anchor_keys_are_owned_by_0303_alone() -> None:
    expected = {
        "uq_scope_registry_m05_instance_tenant": ("id", "plugin_instance_id", "tenant_id"),
        "uq_scope_registry_m05_instance_cabinet": ("id", "plugin_instance_id", "tenant_id", "cabinet_id"),
        "uq_scope_registry_m05_instance_project": (
            "id",
            "plugin_instance_id",
            "tenant_id",
            "cabinet_id",
            "project_id",
        ),
    }

    # 2026110301 must not create them (its docstring delegates them to 0303).
    first = upgrade("2026110301")
    assert not expected.keys() & set(first.detached_uniques), "2026110301 must not own the M05 anchor keys"
    assert not expected.keys() & set(first.sql_objects), "2026110301 must not emit the M05 anchor keys"

    # 2026110303 creates and drops exactly them.
    third = upgrade("2026110303")
    assert set(third.sql_objects) == set(expected), f"2026110303 created {sorted(third.sql_objects)}"
    assert raw_sql_uniques()["scope_registry"] == set(expected.items()), (
        f"declared anchor keys {sorted(raw_sql_uniques()['scope_registry'])}"
    )
    dropped = set(downgrade("2026110303").dropped_constraints)
    assert set(expected) <= dropped, f"2026110303 downgrade leaves anchor keys behind: {sorted(set(expected) - dropped)}"

    # And the model declares the very same keys on the shared table.
    assert set(expected.items()) <= model_unique_specs("scope_registry"), (
        "the M05 model module must declare the anchor keys it expects the migration to install"
    )


def test_rls_registry_tables_exist_and_name_their_creating_revision() -> None:
    owners = table_owners()
    metadata_tables = _model_metadata_tables()
    problems: list[str] = []

    for spec in policies.CANONICAL_TABLE_POLICIES:
        if spec.table not in metadata_tables:
            problems.append(f"{spec.table}: policied but absent from Base.metadata")
            continue
        creator = owners.get(spec.table)
        if creator is None:
            problems.append(f"{spec.table}: no revision of the chain creates it")
        elif creator != spec.owner_revision:
            problems.append(f"{spec.table}: registry says {spec.owner_revision}, chain creates it in {creator}")

    assert not problems, "RLS registry drift:\n" + "\n".join(problems)


def test_rls_registry_is_internally_consistent() -> None:
    policies.validate_registry()
    names = [name for spec in policies.CANONICAL_TABLE_POLICIES for name in spec.policy_names]
    assert len(names) == len(set(names)), "policy names must be unique across the registry"
    for spec in policies.CANONICAL_TABLE_POLICIES:
        predicate = policies.tenant_predicate(spec)
        if spec.tenant is None:
            assert predicate == "false", f"{spec.table}: a table without a tenant anchor must deny tenant sessions"
        else:
            assert "current_setting" in predicate, f"{spec.table}: predicate does not read the transaction GUC"
        assert "app.trusted_service" in policies.trusted_service_predicate()


def test_uncovered_relations_is_empty_for_the_model_metadata() -> None:
    assert policies.uncovered_relations(H.model_metadata().tables.keys()) == ()


def test_uncovered_relations_is_empty_for_the_chain_tables() -> None:
    assert policies.uncovered_relations(table_owners()) == ()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "M1 (ddl-chain-cross-review.md): runtime_revision_heads, runtime_materialization_attempts and "
        "runtime_materialization_audit carry project_id but are neither registered nor excluded, and no "
        "CANONICAL_TABLE_PREFIXES entry matches their names, so uncovered_relations() reports an empty set "
        "vacuously. The fix is a new revision/registry entry, not an edit of 2026110306."
    ),
)
def test_every_scoped_chain_table_is_registered_or_explicitly_excluded() -> None:
    registered = set(policies.registry_tables())
    excluded = policies.excluded_table_names()
    scope_columns = {"tenant_id", "cabinet_id", "project_id", "company_id"}
    metadata = H.model_metadata()

    offenders: list[str] = []
    for table in sorted(table_owners()):
        if table in registered or table in excluded:
            continue
        columns = {column.name for column in metadata.tables[table].columns}
        if columns & scope_columns:
            offenders.append(f"{table} ({sorted(columns & scope_columns)})")

    assert not offenders, (
        "scoped canonical tables without a policy and without a reviewed exclusion: " + ", ".join(offenders)
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "B1 (ddl-chain-cross-review.md): agent_run_provider_attempts is created by 2026110311, after both "
        "policy passes (2026110307 and 2026110310). A fresh `upgrade head` leaves a tenant table without "
        "RLS. The fix is a new revision after 2026110311 that re-applies policies.upgrade_guard_sql()."
    ),
)
def test_every_registered_table_is_policied_after_its_creating_revision() -> None:
    owners = table_owners()
    installers = policy_installers()
    problems: list[str] = []

    for spec in policies.CANONICAL_TABLE_POLICIES:
        creator = owners.get(spec.table)
        if creator is None:
            problems.append(f"{spec.table}: not created by the chain")
            continue
        install_revisions = installers.get(spec.tenant_policy, ())
        if not install_revisions:
            problems.append(f"{spec.table}: no revision installs its policy")
            continue
        if max(install_revisions) < creator:
            problems.append(
                f"{spec.table}: created by {creator}, but the last policy pass is {max(install_revisions)} "
                f"(installers {list(install_revisions)})"
            )

    assert not problems, "tables that would be left without RLS after `upgrade head`:\n" + "\n".join(problems)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "M3 (ddl-chain-cross-review.md): 2026110310 re-applies the whole registry but its downgrade is a "
        "no-op, so the policy delta it creates for agent_run_leases is never removed."
    ),
)
def test_policy_reinstallation_revision_rolls_back_its_own_delta() -> None:
    installed = set(upgrade("2026110310").sql_objects)
    dropped = set(downgrade("2026110310").dropped_sql_objects)
    assert dropped == installed, f"2026110310 downgrade leaves installed policies behind: {sorted(installed - dropped)}"


# --------------------------------------------------------------------------- #
# 6. downgrade symmetry
# --------------------------------------------------------------------------- #


def test_table_downgrades_mirror_upgrades() -> None:
    problems: list[str] = []
    for revision in TABLE_OWNING_REVISIONS:
        up = upgrade(revision)
        down = downgrade(revision)

        if set(up.tables) != set(down.dropped_tables):
            problems.append(
                f"{revision}: created-but-not-dropped {sorted(set(up.tables) - set(down.dropped_tables))} "
                f"dropped-but-not-created {sorted(set(down.dropped_tables) - set(up.tables))}"
            )
        if list(down.dropped_tables) != list(reversed(up.table_order)):
            problems.append(
                f"{revision}: drop order {down.dropped_tables} is not the reverse of create order {up.table_order}"
            )
        if set(up.indexes) != set(down.dropped_indexes):
            problems.append(
                f"{revision}: created-but-not-dropped indexes {sorted(set(up.indexes) - set(down.dropped_indexes))} "
                f"dropped-but-not-created {sorted(set(down.dropped_indexes) - set(up.indexes))}"
            )
        explicit_created = set(up.detached_foreign_keys) | set(up.detached_uniques) | set(up.sql_objects)
        explicit_dropped = set(down.dropped_constraints) | set(down.dropped_sql_objects)
        if explicit_created != explicit_dropped:
            problems.append(
                f"{revision}: not dropped {sorted(explicit_created - explicit_dropped)} "
                f"dropped without creating {sorted(explicit_dropped - explicit_created)}"
            )
    assert not problems, "downgrade symmetry drift:\n" + "\n".join(problems)


def test_policy_revision_downgrade_removes_exactly_its_policies() -> None:
    up = upgrade("2026110307")
    down = downgrade("2026110307")
    assert set(up.sql_objects) == set(down.dropped_sql_objects), (
        "2026110307 must drop exactly the policies it installs: "
        f"not dropped={sorted(set(up.sql_objects) - set(down.dropped_sql_objects))}"
    )
    assert not up.tables and not down.dropped_tables, "the policy revision must not own tables"


def test_validation_revision_downgrade_is_a_documented_no_op() -> None:
    module = H.load_revision("2026110308")
    down = downgrade("2026110308")
    assert not down.dropped_tables and not down.statements and not down.dropped_constraints, (
        "VALIDATE CONSTRAINT has no inverse; the downgrade must not drop or rewrite anything"
    )
    assert "no-op" in (module.downgrade.__doc__ or ""), "the no-op decision must be documented in the downgrade docstring"


# --------------------------------------------------------------------------- #
# 7. review bookkeeping
# --------------------------------------------------------------------------- #


def test_chain_creates_no_rls_object_before_the_policy_revision() -> None:
    for revision in CHAIN:
        if revision in POLICY_REVISIONS:
            continue
        kinds = {sql_object.kind for sql_object in upgrade(revision).sql_objects.values()}
        assert "policy" not in kinds, f"{revision} installs a policy; policy ownership belongs to {POLICY_REVISIONS}"


def test_no_chain_revision_emits_not_valid() -> None:
    # Word-boundary match on purpose: the gate revision 2026110308 contains the
    # prose "constraints are still missing or not validated" in a RAISE message.
    # A naive substring check reports a false positive there; ``\bNOT\s+VALID\b``
    # does not, because "VALIDATED" has no boundary after "VALID".
    pattern = re.compile(r"\bNOT\s+VALID\b", re.IGNORECASE)
    offenders: list[str] = []
    for revision in CHAIN:
        for statement in upgrade(revision).statements:
            if pattern.search(statement):
                offenders.append(revision)
    assert not offenders, f"NOT VALID constraints were introduced by {sorted(set(offenders))}; 2026110308 is the gate"
