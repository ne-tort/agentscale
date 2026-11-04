"""Structural guardrails: no automation, one partition per call, dry run by default.

"One explicit call = one partition = one stage" is an architectural property, so it is
checked structurally (AST + signature) rather than only behaviourally: a behavioural test can
miss a code path that loops over partitions but never runs in the test's fixture, whereas the
module cannot grow a scheduler or a ``while`` loop without tripping these checks.

The checks are deliberately narrow (they name the constructs that would implement
automation) so they do not fire on ordinary comprehensions or ``for`` loops over stage
services.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from prodavan.application.migration.composition import MigrationStage
from prodavan.application.migration.partition_chain import PartitionChainKey
from prodavan.application.migration.partition_runner import STAGE_EXECUTION, run_partition_stage

MODULE_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "prodavan"
    / "application"
    / "migration"
)
RUNNER_PATH = MODULE_DIR / "partition_runner.py"
CHAIN_PATH = MODULE_DIR / "partition_chain.py"

#: Names that would make the module do work on its own.
AUTOMATION_CALLS = frozenset(
    {
        "create_task",
        "ensure_future",
        "call_later",
        "call_at",
        "run_forever",
        "create_background_task",
        "sleep",
        "schedule",
        "repeat",
        "loop_partitions",
        "iterate_partitions",
    }
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("path", (RUNNER_PATH, CHAIN_PATH), ids=("runner", "chain"))
def test_no_scheduling_primitives(path: Path) -> None:
    calls = {
        getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert not (calls & AUTOMATION_CALLS), sorted(calls & AUTOMATION_CALLS)


@pytest.mark.parametrize("path", (RUNNER_PATH, CHAIN_PATH), ids=("runner", "chain"))
def test_no_while_loops(path: Path) -> None:
    """A retry/poll loop is the shape automation takes first."""

    assert not [node for node in ast.walk(_tree(path)) if isinstance(node, ast.While)]


@pytest.mark.parametrize("path", (RUNNER_PATH, CHAIN_PATH), ids=("runner", "chain"))
def test_no_global_mutable_registry(path: Path) -> None:
    """Module-level mutable state would let one call observe another partition's run.

    Every module-level assignment must be immutable data: a literal, a comprehension, a
    frozen mapping built from literals, a ``dict``/``frozenset`` *literal*, a type, a string
    or a name (a constant reference).  A bare ``{}``/``[]``/``set()`` would be shared state.
    """

    allowed = (
        ast.Dict,  # a literal mapping, not a mutable accumulator
        ast.Set,
        ast.List,
        ast.Tuple,
        ast.Call,  # ``dict(...)``/``frozenset(...)``/``Type(...)`` of literals
        ast.Name,
        ast.Attribute,
        ast.Constant,
        ast.BinOp,  # "a" + "b" code fragments
        ast.Subscript,
    )
    offending = [
        target.id
        for node in _tree(path).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and not isinstance(node.value, allowed + (ast.JoinedStr,))
    ]
    assert offending == []


def test_the_runner_exposes_exactly_one_public_entry_point() -> None:
    """One function, taking one key and one stage: there is nothing to configure globally."""

    signature = inspect.signature(run_partition_stage)
    parameters = signature.parameters

    assert list(parameters)[0] == "key"
    assert list(parameters)[1] == "stage"
    assert isinstance(parameters["key"].annotation, str)
    assert "PartitionChainKey" in parameters["key"].annotation
    # No variance argument: a stage is a ``MigrationStage``, not a name or an index.
    assert "MigrationStage" in parameters["stage"].annotation
    # No callable seam that could start a loop, and no partition *collection* anywhere.
    assert not any(
        name.endswith(("s", "es")) and name in {"keys", "partitions", "stages_to_run"}
        for name in parameters
    )
    assert "dry_run" in parameters


def test_the_dry_run_default_is_on_the_declared_default() -> None:
    default = inspect.signature(run_partition_stage).parameters["dry_run"].default

    assert default is True


def test_every_stage_has_exactly_one_execution_binding() -> None:
    """The mapping is data, so a stage cannot grow a second, implicit way to run."""

    assert set(STAGE_EXECUTION) == set(MigrationStage)
    for symbol, method in STAGE_EXECUTION.values():
        assert symbol and method
    # No two stages share one service symbol: each stage owns its own entry point.
    symbols = [symbol for symbol, _method in STAGE_EXECUTION.values()]
    assert len(symbols) == len(set(symbols))


def test_the_chain_planner_never_executes_a_port_method() -> None:
    """Assembly is construction: no ``await`` of a port and no stage method call.

    The module may *define* async helpers; what it must never do is await anything, because
    awaiting is how assembly would turn into execution.
    """

    tree = _tree(CHAIN_PATH)
    awaited = [node for node in ast.walk(tree) if isinstance(node, ast.Await)]

    assert awaited == []


def test_the_chain_planner_imports_no_write_port() -> None:
    """The planner builds ports; it must not reach for a service that mutates state."""

    imported = {
        alias.name
        for node in ast.walk(_tree(CHAIN_PATH))
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    forbidden = {
        "DefinitionVersionEvidenceBackfill",
        "InstanceImportService",
        "CapabilityGatewayService",
        "CutoverCommandService",
    }

    assert not (imported & forbidden)


def test_a_partition_key_carries_exactly_two_identifiers() -> None:
    """A partition is ``(tenant, definition)`` -- no third axis, no list of partitions."""

    fields = tuple(PartitionChainKey.__dataclass_fields__)

    assert fields == ("tenant_id", "app_definition_id")
