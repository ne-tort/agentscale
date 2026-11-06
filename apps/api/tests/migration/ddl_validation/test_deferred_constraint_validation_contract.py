"""Static acceptance contract for revision ``2026110308`` (MIG-DDL-VALIDATE-01).

Authored during the migration verification freeze and intentionally never
executed here: every check reads source through ``ast`` or calls the revision's
pure statement builders. No database, Alembic runtime, rendered DDL, build, lint
or git command is involved.

Covered contract:

* the revision is the only ``2026110308`` file and the single child of
  ``2026110307``;
* the validated inventory is derived *independently* from revisions
  ``2026110301``-``2026110307`` (late/cyclic/``use_alter`` markers) plus the
  explicitly staged closed-partition binding, and matches the revision one to
  one, once per constraint, with the owning revision recorded;
* every rendered statement validates exactly one named constraint, is guarded by
  ``to_regclass`` + ``pg_constraint`` + ``convalidated``, logs every skip with
  ``RAISE NOTICE`` and installs no ``EXCEPTION`` handler;
* a terminal gate re-reads ``pg_constraint`` and raises on any missing or
  unvalidated entry, so a skip can never be presented as evidence;
* the revision contains no DML and no schema operation other than
  ``ALTER TABLE ... VALIDATE CONSTRAINT``;
* ``downgrade()`` is a documented no-op and the module docstring states why the
  pass is separate and why it is mandatory before any zero-violation claim.

The derivation engine is exercised on a synthetic revision as well, so a future
``NOT VALID`` / ``DEFERRABLE`` constraint cannot slip past the coverage check.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[3]
VERSIONS_DIR = API_ROOT / "alembic" / "versions"
REVISION_PATH = VERSIONS_DIR / "2026110308_validate_deferred_constraints.py"

REVISION_ID = "2026110308"
PARENT_ID = "2026110307"
WAVE_REVISIONS = (
    "2026110301",
    "2026110302",
    "2026110303",
    "2026110304",
    "2026110305",
    "2026110306",
    "2026110307",
)

NOTICE_PREFIX = "MIG-DDL-VALIDATE-01"
BLOCKER_PREFIX = "MIG-DDL-VALIDATE-01 blocker"

#: Staging markers that make a foreign key part of the late-validation set.
STAGING_FLAG_KEYWORDS = frozenset(
    {"deferrable", "use_alter", "not_valid", "postgresql_not_valid"}
)

#: The exact-instance binding is not a late `use_alter` key: revision 2026110304
#: creates it inline, but the wave deliberately shipped no validation evidence
#: while the plugin-instance partition stays quarantined. It is listed here
#: explicitly, and the entry is cross-checked against the owning revision below.
CLOSED_PARTITION_BINDINGS = {
    ("dynamic_app_capability_grants", "fk_cap_grant_instance_exact"): {
        "owner_revision": "2026110304",
        "referent_table": "m05_plugin_instances",
    },
}

#: Human-readable contract: every constraint this revision must validate.
EXPECTED_INVENTORY = {
    ("m04e_dynamic_app_definitions", "fk_m04e_definition_current_version_same_definition"),
    ("m05_plugin_instance_registry_links", "fk_m05_registry_instance_link_evidence"),
    ("m05_plugin_instances", "fk_m05_instance_registry_tenant_link"),
    ("m05_plugin_instances", "fk_m05_instance_registry_cabinet_link"),
    ("m05_plugin_instances", "fk_m05_instance_registry_project_link"),
    ("scope_registry", "fk_m05_registry_instance_tenant_link"),
    ("scope_registry", "fk_m05_registry_instance_cabinet_link"),
    ("scope_registry", "fk_m05_registry_instance_project_link"),
    ("dynamic_app_capability_grants", "fk_cap_grant_instance_exact"),
}

DML_PATTERN = re.compile(r"\b(insert|update|delete|truncate|merge|copy)\b", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# source helpers
# --------------------------------------------------------------------------- #
def _tree_of_source(source: str) -> ast.Module:
    return ast.parse(source)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _revision_path(revision: str) -> Path:
    paths = sorted(VERSIONS_DIR.glob(f"{revision}_*.py"))
    assert len(paths) == 1, f"expected exactly one {revision} revision file, got {paths}"
    return paths[0]


def _load_revision() -> object:
    spec = importlib.util.spec_from_file_location("ddl_validation_revision_2026110308", REVISION_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {REVISION_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_revision()
TREE = _tree(REVISION_PATH)
SOURCE = _source(REVISION_PATH)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"module has no {name}()")


def _callee_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _literal(node: ast.AST | None):
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _module_constants(tree: ast.Module) -> dict[str, object]:
    """Literal module-level assignments, so ``create_table(DEFINITION_TABLE)`` resolves.

    Revisions 2026110302/2026110303 name their tables and late constraints through
    module constants; the derivation must see the same names the DDL will emit.
    """
    constants: dict[str, object] = {}
    for node in tree.body:
        target: str | None = None
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            if isinstance(node.targets[0], ast.Name):
                target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        if target is None or value is None:
            continue
        literal = _literal(value)
        if literal is not None:
            constants[target] = literal
    return constants


def _resolve(node: ast.AST | None, constants: dict[str, object]):
    """Resolve a literal or a module-level constant name to its value."""
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    return _literal(node)


def _declared_value(path: Path, name: str):
    """Read a literal module-level assignment without importing the revision."""
    for node in _tree(path).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == name:
                return _literal(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name and node.value is not None:
                return _literal(node.value)
    return None


# --------------------------------------------------------------------------- #
# staging derivation engine (independent of the revision under test)
# --------------------------------------------------------------------------- #
def _staged_markers(call: ast.Call) -> bool:
    """True when a constraint declaration carries an explicit staging marker."""
    for keyword in call.keywords:
        if keyword.arg in STAGING_FLAG_KEYWORDS:
            if isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                return True
        if keyword.arg == "initially":
            if isinstance(keyword.value, ast.Constant) and keyword.value.value == "DEFERRED":
                return True
    return False


def _spec_table_foreign_keys(tree: ast.Module) -> set[tuple[str, str]]:
    """Late keys declared as module-level specs and created in a loop.

    Revision ``2026110303`` builds seven ``op.create_foreign_key`` calls from
    ``DEFERRED_FOREIGN_KEYS``; the call arguments are subscripts, so the names
    are read from the spec table instead of from the call site.

    The spec table is resolved **per element and per value**, not as one
    ``ast.literal_eval`` blob: its ``source``/``referent`` values are module
    constants (``INSTANCE_TABLE``, ``LINK_TABLE``, ...), and a whole-table
    ``literal_eval`` raises on those ``Name`` nodes, yielding ``None``. The
    previous whole-table read therefore reported *no* staged key for 2026110303
    while the DDL created seven deferred ones — a silent under-count that made
    the coverage assertion fail (and, had ``EXPECTED_INVENTORY`` been trimmed to
    match, would have let unvalidated deferred keys pass). Resolving through
    ``_resolve`` reports exactly the names the loop will emit.
    """
    found: set[tuple[str, str]] = set()
    constants = _module_constants(tree)
    for node in tree.body:
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            if isinstance(node.targets[0], ast.Name):
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = node.value
        if not isinstance(value, (ast.Tuple, ast.List)):
            continue
        for element in value.elts:
            if not isinstance(element, ast.Dict):
                continue
            item: dict[object, object] = {}
            for key_node, value_node in zip(element.keys, element.values):
                key = _resolve(key_node, constants)
                if isinstance(key, str):
                    item[key] = _resolve(value_node, constants)
            name, source = item.get("name"), item.get("source")
            if not isinstance(name, str) or not isinstance(source, str):
                continue
            staged = (
                item.get("use_alter") is True
                or item.get("deferrable") is True
                or item.get("initially") == "DEFERRED"
                or item.get("not_valid") is True
                or item.get("postgresql_not_valid") is True
            )
            if staged:
                found.add((source, name))
    return found


def staged_foreign_keys(tree: ast.Module) -> set[tuple[str, str]]:
    """Every ``(table, constraint)`` the source stages instead of proving.

    Detects all three declaration shapes used by the wave: a direct
    ``op.create_foreign_key`` with literal arguments, an inline
    ``ForeignKeyConstraint`` inside ``op.create_table``, and the dict-spec loop.
    """
    found: set[tuple[str, str]] = _spec_table_foreign_keys(tree)
    constants = _module_constants(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _callee_name(node)
        if callee == "create_foreign_key" and len(node.args) >= 2 and _staged_markers(node):
            name, table = _resolve(node.args[0], constants), _resolve(node.args[1], constants)
            if isinstance(name, str) and isinstance(table, str):
                found.add((table, name))
        elif callee == "create_table" and node.args:
            table = _resolve(node.args[0], constants)
            if not isinstance(table, str):
                continue
            for argument in node.args[1:]:
                if not isinstance(argument, ast.Call):
                    continue
                if _callee_name(argument) != "ForeignKeyConstraint":
                    continue
                if not _staged_markers(argument):
                    continue
                name_node = next((kw.value for kw in argument.keywords if kw.arg == "name"), None)
                name = _resolve(name_node, constants)
                if isinstance(name, str):
                    found.add((table, name))
    return found


def _discovered_staged_foreign_keys() -> dict[tuple[str, str], str]:
    """``(table, constraint) -> owning revision`` for the whole DDL wave."""
    discovered: dict[tuple[str, str], str] = {}
    for revision in WAVE_REVISIONS:
        for pair in staged_foreign_keys(_tree(_revision_path(revision))):
            assert pair not in discovered, (
                f"{pair} is staged twice in the chain: {discovered[pair]} and {revision}"
            )
            discovered[pair] = revision
    return discovered


def _created_tables(tree: ast.Module) -> set[str]:
    """Tables created by one revision, resolving module-level name constants."""
    constants = _module_constants(tree)
    return {
        table
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _callee_name(node) == "create_table" and node.args
        for table in (_resolve(node.args[0], constants),)
        if isinstance(table, str)
    }


def _inline_foreign_keys(tree: ast.Module) -> dict[tuple[str, str], tuple[str, ...]]:
    """``(table, constraint) -> target column names`` for inline declarations."""
    found: dict[tuple[str, str], tuple[str, ...]] = {}
    constants = _module_constants(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _callee_name(node) != "create_table":
            continue
        if not node.args:
            continue
        table = _resolve(node.args[0], constants)
        if not isinstance(table, str):
            continue
        for argument in node.args[1:]:
            if not isinstance(argument, ast.Call) or _callee_name(argument) != "ForeignKeyConstraint":
                continue
            name = _resolve(next((kw.value for kw in argument.keywords if kw.arg == "name"), None), constants)
            if not isinstance(name, str) or len(argument.args) < 2:
                continue
            remote = argument.args[1]
            if not isinstance(remote, (ast.List, ast.Tuple)):
                continue
            targets = tuple(
                target for target in (_literal(element) for element in remote.elts)
                if isinstance(target, str)
            )
            found[(table, name)] = targets
    return found


def _statements() -> list[str]:
    return [MODULE.validation_statement(spec) for spec in MODULE.VALIDATION_SPECS]


# --------------------------------------------------------------------------- #
# 1. revision identity and chain
# --------------------------------------------------------------------------- #
def test_revision_identity_and_parent() -> None:
    assert MODULE.revision == REVISION_ID
    assert MODULE.down_revision == PARENT_ID
    assert MODULE.branch_labels is None
    assert MODULE.depends_on is None
    assert REVISION_PATH.name == "2026110308_validate_deferred_constraints.py"
    assert _revision_path(REVISION_ID) == REVISION_PATH


def test_2026110307_has_exactly_one_child() -> None:
    claimants = [
        _declared_value(path, "revision")
        for path in sorted(VERSIONS_DIR.glob("*.py"))
        if _declared_value(path, "down_revision") == PARENT_ID
    ]
    assert claimants == [REVISION_ID], (
        f"revision {PARENT_ID} must have exactly one child, got {claimants}"
    )


def test_the_wave_revisions_do_not_validate_anything_themselves() -> None:
    """The late pass owns every ``VALIDATE CONSTRAINT`` in the wave."""
    for revision in WAVE_REVISIONS:
        source = _source(_revision_path(revision)).upper()
        assert "VALIDATE CONSTRAINT" not in source, (
            f"revision {revision} must not pre-empt the 2026110308 validation gate"
        )


# --------------------------------------------------------------------------- #
# 2. inventory coverage
# --------------------------------------------------------------------------- #
def test_inventory_covers_every_staged_constraint_exactly_once() -> None:
    # ``_discovered_staged_foreign_keys`` maps each staged pair to its owning
    # revision, so the coverage comparison has to run on the key set. The checks
    # below are unchanged in strength: the derived pairs must still equal the
    # declared inventory exactly, with no overlap against the explicit binding.
    discovered = _discovered_staged_foreign_keys()
    explicit = set(CLOSED_PARTITION_BINDINGS)

    assert set(discovered).isdisjoint(explicit), (
        "the closed-partition binding must be declared once"
    )
    expected = set(discovered) | explicit
    assert expected == EXPECTED_INVENTORY

    actual = [(spec["table"], spec["constraint"]) for spec in MODULE.VALIDATION_SPECS]
    assert len(actual) == len(set(actual)), "a constraint must be validated exactly once"
    assert set(actual) == expected
    assert len(actual) == 9


def test_derivation_finds_the_deferred_and_cyclic_keys() -> None:
    discovered = _discovered_staged_foreign_keys()

    current_version_key = (
        "m04e_dynamic_app_definitions",
        "fk_m04e_definition_current_version_same_definition",
    )
    assert discovered[current_version_key] == "2026110302"
    m05 = {pair for pair, owner in discovered.items() if owner == "2026110303"}
    assert m05 == {
        ("m05_plugin_instance_registry_links", "fk_m05_registry_instance_link_evidence"),
        ("m05_plugin_instances", "fk_m05_instance_registry_tenant_link"),
        ("m05_plugin_instances", "fk_m05_instance_registry_cabinet_link"),
        ("m05_plugin_instances", "fk_m05_instance_registry_project_link"),
        ("scope_registry", "fk_m05_registry_instance_tenant_link"),
        ("scope_registry", "fk_m05_registry_instance_cabinet_link"),
        ("scope_registry", "fk_m05_registry_instance_project_link"),
    }
    assert len(m05) == 7
    # Revisions 2026110305-2026110307 stage no foreign key at all.
    assert {owner for owner in discovered.values()} == {"2026110302", "2026110303"}


def test_owner_revision_and_staging_reason_match_the_source() -> None:
    discovered = _discovered_staged_foreign_keys()
    for spec in MODULE.VALIDATION_SPECS:
        pair = (spec["table"], spec["constraint"])
        assert set(spec) == {"table", "constraint", "owner_revision", "staging"}
        if pair in discovered:
            assert spec["owner_revision"] == discovered[pair]
            assert spec["staging"] == "deferred_cycle"
        else:
            assert pair in CLOSED_PARTITION_BINDINGS
            assert spec["owner_revision"] == CLOSED_PARTITION_BINDINGS[pair]["owner_revision"]
            assert spec["staging"] == "closed_partition_binding"
        assert spec["owner_revision"] in WAVE_REVISIONS


def test_every_validated_table_is_created_by_the_wave() -> None:
    created: dict[str, str] = {}
    for revision in WAVE_REVISIONS:
        for table in _created_tables(_tree(_revision_path(revision))):
            created.setdefault(table, revision)

    for spec in MODULE.VALIDATION_SPECS:
        table = spec["table"]
        assert table in created, f"{table} is not created by revisions 2026110301-2026110307"
        assert WAVE_REVISIONS.index(created[table]) <= WAVE_REVISIONS.index(
            spec["owner_revision"]
        ), f"{table} is referenced before its owning revision creates it"


def test_closed_partition_binding_is_a_real_cross_revision_foreign_key() -> None:
    owner_path = _revision_path("2026110304")
    expected = CLOSED_PARTITION_BINDINGS[("dynamic_app_capability_grants", "fk_cap_grant_instance_exact")]

    keys = _inline_foreign_keys(_tree(owner_path))
    pair = ("dynamic_app_capability_grants", "fk_cap_grant_instance_exact")
    assert pair in keys, "revision 2026110304 must declare the exact-instance binding inline"

    targets = keys[pair]
    assert targets, "the exact-instance binding must pin concrete instance columns"
    assert all(target.startswith(f"{expected['referent_table']}.") for target in targets)
    assert ("id", "definition_id", "version_id", "tenant_id", "scope_kind", "scope_id") == tuple(
        target.split(".", 1)[1] for target in targets
    )
    assert expected["referent_table"] in _created_tables(_tree(_revision_path("2026110303")))
    # The binding is inline, not deferred, which is why it is listed explicitly.
    assert pair not in staged_foreign_keys(_tree(owner_path))


# --------------------------------------------------------------------------- #
# 3. per-constraint statements
# --------------------------------------------------------------------------- #
def test_each_statement_validates_exactly_one_named_constraint() -> None:
    statements = _statements()
    assert len(statements) == len(MODULE.VALIDATION_SPECS)

    validated: list[str] = []
    for spec, statement in zip(MODULE.VALIDATION_SPECS, statements):
        body = statement.strip()
        table, constraint = spec["table"], spec["constraint"]

        assert body.count("VALIDATE CONSTRAINT") == 1, f"{constraint}: must be validated once"
        assert body.count("ALTER TABLE") == 1
        assert f'ALTER TABLE "{table}" VALIDATE CONSTRAINT "{constraint}";' in body
        validated.append(constraint)

        assert body.count("to_regclass(") == 1
        assert "pg_constraint" in body
        assert body.count("convalidated") == 1
        assert "IF v_validated THEN" in body
        assert "contype = 'f'" not in body, "the type check compares the selected contype"
        assert "v_kind <> 'f'" in body

    assert sorted(validated) == sorted(constraint for _table, constraint in EXPECTED_INVENTORY)


def test_each_statement_is_a_single_do_block() -> None:
    for statement in _statements():
        body = statement.strip()
        assert body.startswith("DO $validate_constraint$")
        assert body.endswith("$validate_constraint$;")
        assert body.count("$validate_constraint$") == 2
        assert body.count("DO $") == 1


def test_skip_paths_are_logged_with_notice_and_never_swallowed() -> None:
    for spec, statement in zip(MODULE.VALIDATION_SPECS, _statements()):
        body = statement.strip()
        # table absent / constraint absent / not a foreign key / already valid / done
        assert body.count("RAISE NOTICE") == 5, f"{spec['constraint']}: every skip must be logged"
        assert body.count("RETURN;") == 4
        assert "EXCEPTION" not in body
        assert "is absent" in body
        assert "nothing to validate" in body
        assert "is not a foreign key" in body
        assert "is already validated" in body
        assert NOTICE_PREFIX in body


def test_statements_carry_no_client_side_placeholders() -> None:
    """No ``%`` anywhere: the SQL is safe for asyncpg and for ``--sql`` output."""
    for statement in _statements():
        assert "%" not in statement
    assert "%" not in MODULE.validation_gate_statement()


# --------------------------------------------------------------------------- #
# 4. terminal gate
# --------------------------------------------------------------------------- #
def test_terminal_gate_fails_closed_on_any_unvalidated_entry() -> None:
    gate = MODULE.validation_gate_statement()

    assert gate.strip().startswith("DO $validate_constraint_gate$")
    assert gate.strip().endswith("$validate_constraint_gate$;")
    assert "RAISE EXCEPTION" in gate
    assert "RAISE NOTICE" not in gate
    assert "convalidated" in gate
    assert "contype = 'f'" in gate
    assert BLOCKER_PREFIX in gate
    assert "NOT EXISTS" in gate

    assert gate.count("('") == len(MODULE.VALIDATION_SPECS)
    for spec in MODULE.VALIDATION_SPECS:
        assert f"('{spec['table']}', '{spec['constraint']}')" in gate, (
            "the gate must list every validated constraint"
        )


# --------------------------------------------------------------------------- #
# 5. scope: no data, no schema change
# --------------------------------------------------------------------------- #
def test_revision_issues_no_dml_and_no_schema_change() -> None:
    rendered = "\n".join([*_statements(), MODULE.validation_gate_statement()])
    assert DML_PATTERN.search(rendered) is None
    assert "DROP " not in rendered.upper()
    assert "CREATE " not in rendered.upper()
    assert "TRUNCATE" not in rendered.upper()
    assert "op.alter_column" not in SOURCE
    assert "op.add_column" not in SOURCE
    assert "op.bulk_insert" not in SOURCE
    assert "op.create_" not in SOURCE
    assert "op.drop_" not in SOURCE
    assert "from prodavan" not in SOURCE, "the revision must not import ORM models"
    assert "sa." not in SOURCE, "the revision needs no SQLAlchemy objects"


def test_upgrade_only_executes_the_inventory_then_the_gate() -> None:
    upgrade = _function(TREE, "upgrade")
    op_calls = [
        node.func.attr
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    ]
    assert op_calls == ["execute", "execute"]

    body = ast.unparse(upgrade)
    assert "for spec in VALIDATION_SPECS" in body
    assert body.count("op.execute(validation_statement(spec))") == 1
    assert body.count("validation_gate_statement()") == 1
    assert "_assert_unique_specs()" in body
    assert body.index("_assert_unique_specs()") < body.index("for spec in VALIDATION_SPECS")
    assert body.index("for spec in VALIDATION_SPECS") < body.index("validation_gate_statement()")


# --------------------------------------------------------------------------- #
# 6. downgrade
# --------------------------------------------------------------------------- #
def test_downgrade_is_a_documented_no_op() -> None:
    downgrade = _function(TREE, "downgrade")
    op_calls = [
        node for node in ast.walk(downgrade)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    ]
    assert op_calls == [], "downgrade must not run DDL: validation is unidirectional"

    # Only a docstring and an explicit ``return None``: nothing to execute.
    executable = [
        node for node in downgrade.body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        and not isinstance(node, ast.Return)
    ]
    assert executable == []
    assert "does not drop" in SOURCE
    assert "unidirectional" in SOURCE
    assert "separate revision" in SOURCE
    assert "tables are empty" in SOURCE


# --------------------------------------------------------------------------- #
# 7. documentation duties
# --------------------------------------------------------------------------- #
def test_docstring_states_why_validation_is_separate() -> None:
    docstring = MODULE.__doc__
    assert isinstance(docstring, str)
    for phrase in (
        "Why validation is its own revision",
        "must not be gated on a table scan",
        "DEFERRABLE INITIALLY DEFERRED",
        "0 violations",
        "convalidated",
    ):
        assert phrase in docstring, f"docstring must explain: {phrase}"


def test_docstring_documents_the_lock_profile() -> None:
    docstring = MODULE.__doc__
    assert "SHARE UPDATE EXCLUSIVE" in docstring
    assert "ACCESS EXCLUSIVE" in docstring
    assert "ROW SHARE" in docstring
    assert "no table is rewritten" in docstring


# --------------------------------------------------------------------------- #
# 8. guards and the derivation engine itself
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad",
    ["", "Upper", "has space", "1leading", 'quote"inside', "semicolon;drop", "dash-name"],
)
def test_identifier_rendering_fails_closed(bad: str) -> None:
    with pytest.raises(ValueError):
        MODULE._identifier(bad)
    with pytest.raises(ValueError):
        MODULE._literal(bad)


def test_identifier_rendering_quotes_valid_names() -> None:
    assert MODULE._identifier("scope_registry") == '"scope_registry"'
    assert MODULE._literal("fk_m05_instance_registry_tenant_link") == (
        "'fk_m05_instance_registry_tenant_link'"
    )


def test_duplicate_inventory_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "VALIDATION_SPECS", tuple(MODULE.VALIDATION_SPECS[:1] * 2))
    with pytest.raises(RuntimeError):
        MODULE._assert_unique_specs()


SYNTHETIC_STAGED_SOURCE = """
op.create_foreign_key(
    "fk_synth_literal", "child", "parent", ["a"], ["b"],
    deferrable=True, initially="DEFERRED",
)
op.create_foreign_key("fk_synth_immediate", "child", "parent", ["a"], ["b"])
op.create_foreign_key(
    "fk_synth_not_valid", "child", "parent", ["a"], ["b"], postgresql_not_valid=True,
)
op.create_table(
    "inline_table",
    sa.Column("x", sa.String()),
    sa.ForeignKeyConstraint(
        ["x"], ["parent.id"], name="fk_synth_inline", deferrable=True, initially="DEFERRED",
    ),
    sa.ForeignKeyConstraint(["x"], ["parent.id"], name="fk_synth_inline_plain"),
)
SPEC_KEYS = (
    {
        "name": "fk_synth_spec",
        "source": "spec_table",
        "use_alter": True,
        "deferrable": True,
        "initially": "DEFERRED",
    },
)
"""


def test_derivation_engine_detects_every_staging_shape() -> None:
    """A future staged constraint cannot pass the coverage check unnoticed."""
    found = staged_foreign_keys(_tree_of_source(SYNTHETIC_STAGED_SOURCE))
    assert found == {
        ("child", "fk_synth_literal"),
        ("child", "fk_synth_not_valid"),
        ("inline_table", "fk_synth_inline"),
        ("spec_table", "fk_synth_spec"),
    }
    assert ("child", "fk_synth_immediate") not in found
    assert ("inline_table", "fk_synth_inline_plain") not in found
