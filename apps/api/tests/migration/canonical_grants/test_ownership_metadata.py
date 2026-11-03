"""Metadata ownership acceptance for MIG-DDL-M06-01 / MIG-OWNERSHIP-CLEANUP-01.

Authored under the migration verification freeze: these checks are reviewed as
static contracts and are **not executed** here. They encode the decision in
``docs/08-migration/canonical-grants-ownership-decision.md``:

* exactly one owning model per physical table;
* ``canonical_grants`` is the only description of ``dynamic_app_capability_grants``;
* the retired legacy duplicate module is deleted and its name fails closed;
* ``dynamic_app_grants`` stays a separate read-only compatibility projection.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

import prodavan.infrastructure.persistence.models as models
from prodavan.infrastructure.persistence.models import canonical_grants
from prodavan.infrastructure.persistence.models.base import Base


MODELS_DIR = Path(models.__file__).resolve().parent
CANONICAL_MODULE = "canonical_grants.py"
RETIRED_MODULE = "dynamic_app_grants.py"
RETIRED_DOTTED_MODULE = f"{models.__name__}.{RETIRED_MODULE.removesuffix('.py')}"


def _mapped_classes(table) -> list[type]:
    return [mapper.class_ for mapper in Base.registry.mappers if mapper.local_table is table]


def _declared_tablenames(path: Path) -> list[str]:
    """Return every ``__tablename__`` literal declared in one model module."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target] if isinstance(node.target, ast.Name) else []
            value = node.value
        else:
            continue
        if not any(target.id == "__tablename__" for target in targets):
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            names.append(value.value)
    return names


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


@pytest.mark.parametrize(
    ("table_name", "class_name"),
    sorted(canonical_grants.M06_TABLE_OWNERS.items()),
)
def test_each_canonical_table_has_exactly_one_owning_model(table_name: str, class_name: str) -> None:
    model = getattr(models, class_name)
    table = Base.metadata.tables[table_name]

    assert model.__tablename__ == table_name
    assert table is model.__table__
    assert _mapped_classes(table) == [model]
    assert class_name in models.__all__


def test_canonical_module_declares_only_the_owned_tables() -> None:
    declared = set(_declared_tablenames(MODELS_DIR / CANONICAL_MODULE))

    assert declared == set(canonical_grants.M06_TABLE_OWNERS)
    for class_name in canonical_grants.M06_TABLE_OWNERS.values():
        assert class_name in models.__all__


def test_metadata_has_no_duplicate_tablename_claims() -> None:
    owners: dict[str, list[type]] = {}
    for mapper in Base.registry.mappers:
        owners.setdefault(mapper.local_table.name, []).append(mapper.class_)

    duplicated = {name: classes for name, classes in owners.items() if len(classes) > 1}
    assert duplicated == {}

    # Every canonical table must also be present in metadata exactly once.
    for table_name in canonical_grants.M06_TABLE_OWNERS:
        assert list(Base.metadata.tables).count(table_name) == 1


def test_no_module_declares_a_canonical_table_twice() -> None:
    declared: dict[str, set[str]] = {}
    for path in sorted(MODELS_DIR.glob("*.py")):
        for table_name in _declared_tablenames(path):
            declared.setdefault(table_name, set()).add(path.name)

    canonical = set(canonical_grants.M06_TABLE_OWNERS)
    offenders = {
        table_name: sorted(modules - {CANONICAL_MODULE})
        for table_name, modules in declared.items()
        if table_name in canonical and modules - {CANONICAL_MODULE}
    }

    # MIG-OWNERSHIP-CLEANUP-01 removed the last residue: the retired duplicate
    # module no longer exists, so nothing outside ``canonical_grants.py`` may
    # claim a canonical table. ``canonical_grants.M06_RETIRED_DUPLICATES`` stays
    # a historical record of the retired entry until that module is next touched;
    # it is intentionally not used as an allowance here.
    assert offenders == {}
    assert not (MODELS_DIR / RETIRED_MODULE).exists()


def test_models_package_does_not_import_the_retired_duplicate_module() -> None:
    imported = _imported_modules(MODELS_DIR / "__init__.py")

    assert RETIRED_DOTTED_MODULE not in imported
    assert "DynamicAppCapabilityGrantRow" not in models.__all__
    assert not (MODELS_DIR / RETIRED_MODULE).exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(RETIRED_DOTTED_MODULE)
    with pytest.raises(AttributeError, match="retired"):
        models.DynamicAppCapabilityGrantRow  # noqa: B018 — attribute access is the assertion


def test_legacy_dynamic_app_grants_projection_is_a_separate_read_only_table() -> None:
    projection = models.AppGrantRow

    assert projection.__tablename__ == canonical_grants.M06_LEGACY_PROJECTION["table"]
    assert projection.__tablename__ != "dynamic_app_capability_grants"
    assert projection.__table__ is not models.CanonicalGrantRow.__table__
    # The projection keeps the legacy JSONB decision set and never gains the
    # canonical capability/instance/generation columns.
    assert "grants" in projection.__table__.c
    for canonical_only in (
        "capability_key",
        "subject_kind",
        "plugin_instance_id",
        "grant_generation",
        "writer_generation",
        "payload_digest",
    ):
        assert canonical_only not in projection.__table__.c
    # It must not reference the canonical authorization table either way.
    assert not any(
        foreign_key.column.table.name == "dynamic_app_capability_grants"
        for column in projection.__table__.c
        for foreign_key in column.foreign_keys
    )
    assert "read-only" in canonical_grants.M06_LEGACY_PROJECTION["state"]
