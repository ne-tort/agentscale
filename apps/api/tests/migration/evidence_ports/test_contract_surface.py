"""Source- and signature-level acceptance for the four evidence ports.

These checks need no database and no fixtures: they pin the properties that make the
ports trustworthy at all — the declared port methods are actually implemented, nothing is
stored in process memory, no statement can mutate a recorded row, and every column the
mappers write exists on the registered ORM model.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from prodavan.application.migration.evidence_ports import (
    SqlM04EBackfillEvidenceStore,
    SqlM05HandoffEvidenceStore,
    SqlM05PromotionEvidenceStore,
    SqlPartitionHandoffReadStore,
)
from prodavan.application.migration.m04e_definition_version import backfill as m04e
from prodavan.application.migration.m05_instance_runtime import runtime_service as m05
from prodavan.application.migration.m06_grants_gateway import handoff as m06

from .acceptance_fixtures import MODULE_PATH

SOURCE = MODULE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _defined_class(name: str) -> ast.ClassDef:
    for node in TREE.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined in {MODULE_PATH.name}")


def test_the_four_reviewed_ports_are_implemented_in_one_module():
    assert inspect.iscoroutinefunction(SqlM05HandoffEvidenceStore.append_marker)
    assert inspect.iscoroutinefunction(SqlM04EBackfillEvidenceStore.classify_and_commit_page)
    assert inspect.iscoroutinefunction(SqlM05PromotionEvidenceStore.append_immutable)
    assert inspect.iscoroutinefunction(SqlPartitionHandoffReadStore.load_partition)


@pytest.mark.parametrize(
    "implementation,protocol,method",
    [
        (SqlM05HandoffEvidenceStore, m04e.M05HandoffPort, "append_marker"),
        (SqlM04EBackfillEvidenceStore, m04e.EvidenceStore, "find_existing"),
        (SqlM04EBackfillEvidenceStore, m04e.EvidenceStore, "find_observation"),
        (SqlM04EBackfillEvidenceStore, m04e.EvidenceStore, "record_blocked"),
        (SqlM04EBackfillEvidenceStore, m04e.EvidenceStore, "classify_and_commit_page"),
        (SqlM05PromotionEvidenceStore, m05.M05PromotionRecordPort, "append_immutable"),
        (SqlPartitionHandoffReadStore, m06.PartitionHandoffReadPort, "load_partition"),
    ],
)
def test_every_declared_port_method_has_a_matching_signature(implementation, protocol, method):
    declared = inspect.signature(getattr(protocol, method))
    implemented = inspect.signature(getattr(implementation, method))
    assert list(declared.parameters) == list(implemented.parameters)
    # ``inspect.iscoroutinefunction`` takes the callable, not its ``Signature``: passing the
    # signature always answered ``False``, which made this half of the check compare two
    # ``False`` values and never detect a synchronous implementation of an async port.
    assert inspect.iscoroutinefunction(getattr(implementation, method)) is (
        inspect.iscoroutinefunction(getattr(protocol, method))
    ), f"{implementation.__name__}.{method} is not async like {protocol.__name__}.{method}"


def test_port_return_annotations_match_the_contract():
    assert "M05HandoffMarker" in str(
        inspect.signature(SqlM05HandoffEvidenceStore.append_marker).return_annotation
    )
    assert inspect.signature(SqlM05PromotionEvidenceStore.append_immutable).return_annotation in {
        None, "None"
    }
    assert "AcceptedPartitionEvidence | None" in str(
        inspect.signature(SqlPartitionHandoffReadStore.load_partition).return_annotation
    )


def _docstring_constants(tree: ast.Module) -> set[int]:
    """ids of the ``Constant`` nodes that are docstrings, i.e. documentation, not behaviour."""

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", ())
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return docstrings


def _executable_strings(tree: ast.Module) -> tuple[str, ...]:
    """Every string literal the module can act on: docstrings excluded.

    The previous form of this test searched the raw source text, so it failed on the module
    docstring documenting *that there is no such marker* — a documentation line is not an
    executable path. What actually matters is whether the module can *produce* a marker.
    """

    docstrings = _docstring_constants(tree)
    return tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )


def test_module_declares_no_in_memory_or_test_double_path():
    class_names = {node.name for node in TREE.body if isinstance(node, ast.ClassDef)}
    assert not [name for name in class_names if "InMemory" in name or "Memory" in name]
    assert not [name for name in class_names if "Fixture" in name or "Fake" in name]

    # No executable string can carry the marker: an assignment such as
    # ``IS_TEST_DOUBLE = "..."``, a keyword argument, or a stored attribute value.
    markers = [
        value for value in _executable_strings(TREE) if "IS_TEST_DOUBLE" in value
    ]
    assert markers == [], f"an executable string literal declares a test double: {markers}"
    # ...and no identifier of that name is bound or read, anywhere in the module.
    for node in ast.walk(TREE):
        name = getattr(node, "attr", None) or getattr(node, "id", None)
        assert name != "IS_TEST_DOUBLE", (
            "the module must not define or read an IS_TEST_DOUBLE marker: the only evidence "
            "source is a committed row of a registered table"
        )
    # A test double also cannot arrive by import: nothing under ``tests`` may be imported.
    for node in ast.walk(TREE):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
        elif isinstance(node, ast.Import):
            module = ",".join(alias.name for alias in node.names)
        else:
            continue
        assert "tests" not in module.split(".") and "conftest" not in module, (
            f"the evidence ports must not import a test module: {module}"
        )

    for node in ast.walk(TREE):
        if isinstance(node, ast.Attribute) and node.attr in {"_rows", "_store_cache", "_markers"}:
            raise AssertionError("a process-local evidence cache is not allowed")


def test_module_issues_no_update_or_delete_statement():
    forbidden = {"update", "delete", "merge"}
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in forbidden:
                raise AssertionError(f"append-only ports must not call {name}()")
        if isinstance(node, ast.Name) and node.id in {"update", "delete"}:
            raise AssertionError(f"append-only ports must not reference {node.id}")


def test_module_never_commits_or_rolls_back_by_hand():
    """The transaction is owned by the session.begin() context, not by the port."""
    for node in ast.walk(TREE):
        if isinstance(node, ast.Attribute) and node.attr in {"commit", "rollback", "begin_nested"}:
            raise AssertionError("the port must not manage sub-transactions on its own")


def test_every_target_table_comes_from_a_registered_orm_model():
    """The ports may not name a table that no registered model owns."""
    from prodavan.infrastructure.persistence.models.base import Base

    registered = set(Base.metadata.tables)
    for table in (
        "m04e_definition_version_promotion_evidence",
        "m05_plugin_instance_promotion_evidence",
        "m05_plugin_instances",
        "m04_scope_promotion_evidence",
        "m06_canonical_grant_promotion_evidence",
        "dynamic_app_capability_grant_mappings",
        "dynamic_app_capability_grants",
    ):
        assert table in registered, f"{table} is not a registered model table"
    for row_class in (
        "M04EDefinitionVersionPromotionEvidenceRow",
        "M05PluginInstancePromotionEvidenceRow",
        "PluginInstanceRegistryRow",
        "M04ScopePromotionEvidenceRow",
        "M06CanonicalGrantPromotionEvidenceRow",
        "CanonicalGrantMappingRow",
        "CanonicalGrantRow",
    ):
        assert row_class in SOURCE

    # No raw table name is used in DML: every write goes through the ORM row class.
    assert "INSERT INTO" not in SOURCE.upper()


def test_schema_blockers_are_recorded_for_every_known_gap():
    from prodavan.application.migration.evidence_ports import (
        DECLARED_READ_BLOCKERS,
        SCHEMA_BLOCKERS,
    )

    joined = " ".join(SCHEMA_BLOCKERS)
    for expected in (
        "promotion_outcome_cannot_express_ready",
        "m04_scope_promotion_evidence_missing",
        "m05_promotion_record_carries_no_source_inventory",
        "m05_writer_generation_zero_has_no_m06_epoch",
        "m04e_backfill_has_no_checkpoint_or_source_mapping_table",
    ):
        assert expected in joined
    assert any("m04_scope_promotion_evidence_missing" in item for item in DECLARED_READ_BLOCKERS)


def test_no_port_defaults_a_required_dependency():
    for name in (
        "SqlM05HandoffEvidenceStore",
        "SqlM04EBackfillEvidenceStore",
        "SqlM05PromotionEvidenceStore",
        "SqlPartitionHandoffReadStore",
    ):
        node = _defined_class(name)
        init = next(
            child for child in node.body
            if isinstance(child, ast.FunctionDef) and child.name == "__init__"
        )
        arguments = [arg.arg for arg in init.args.kwonlyargs]
        assert arguments[:2] == ["session_factory", "scope"]
        assert all(arg.annotation is not None for arg in init.args.kwonlyargs)
        # Only the reviewed-RLS manifest may be omitted; omitting it refuses, it does
        # not degrade. Every other dependency is mandatory.
        defaulted = [
            arguments[index]
            for index, default in enumerate(init.args.kw_defaults)
            if default is not None
        ]
        assert defaulted in ([], ["reviewed_m04_rls"])
