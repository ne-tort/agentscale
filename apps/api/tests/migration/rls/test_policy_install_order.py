"""Structural gate: every registered table has a policy installer at or after it.

The registry (`prodavan.infrastructure.persistence.rls.policies`) is the single
source of truth for *which* tables must be policied, but membership proves
nothing about *when* the policy is installed.  A revision that creates a
tenant-scoped table after the last installing revision leaves that table without
policies, because the installer's ``to_regclass`` guard correctly skips a
relation that does not exist yet.

That defect appeared twice in this chain (``agent_run_leases`` and
``agent_run_provider_attempts``), and both times the registry still looked
complete.  This module turns the review rule into a gate.

How the check works (static only, no database):

* walk ``alembic/versions`` and build the revision graph from the module-level
  ``revision`` / ``down_revision`` assignments;
* a revision *installs policies* when its source calls
  ``upgrade_guard_sql`` — the only supported way to emit a policy;
* order the installing revisions by their position on the head chain;
* every registry spec's ``owner_revision`` must be at or before the newest
  installer on that chain.

If a new table is added without a following re-apply, the newest installer falls
behind the new owner revision and this test fails with the offending table names.
"""

from __future__ import annotations

import ast
from pathlib import Path

from prodavan.infrastructure.persistence.rls import policies

API_ROOT = Path(__file__).resolve().parents[3]
VERSIONS = API_ROOT / "alembic" / "versions"

INSTALL_CALL = "upgrade_guard_sql"
#: Applied by the installer itself, not a table: the registry pass of 2026110307.
BASE_INSTALLER = "2026110307"


def _revision_graph() -> dict[str, str | None]:
    graph: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision: str | None = None
        down: str | None = None
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if target.id == "revision":
                revision = ast.literal_eval(node.value)
            elif target.id == "down_revision":
                down = ast.literal_eval(node.value)
        if revision is not None:
            graph[revision] = down
    return graph


def _policy_installers() -> set[str]:
    installers: set[str] = set()
    for path in VERSIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if INSTALL_CALL not in text:
            continue
        tree = ast.parse(text, filename=str(path))
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "revision"
            ):
                installers.add(ast.literal_eval(node.value))
    return installers


def _chain_from_head(graph: dict[str, str | None]) -> list[str]:
    children = {down for down in graph.values() if down}
    heads = [revision for revision in graph if revision not in children]
    assert len(heads) == 1, f"expected exactly one head, found {sorted(heads)}"
    chain: list[str] = []
    current: str | None = heads[0]
    while current is not None:
        chain.append(current)
        current = graph.get(current)
    return chain


def test_graph_has_a_single_head_and_a_known_base_installer() -> None:
    graph = _revision_graph()
    chain = _chain_from_head(graph)
    assert BASE_INSTALLER in chain
    assert BASE_INSTALLER in _policy_installers()


def test_every_registered_table_is_policied_after_its_creating_revision() -> None:
    graph = _revision_graph()
    chain = _chain_from_head(graph)
    position = {revision: index for index, revision in enumerate(reversed(chain))}

    installers = _policy_installers() & set(position)
    assert installers, "no revision installs policies"
    newest_installer = max(installers, key=lambda revision: position[revision])

    late = sorted(
        spec.table
        for spec in policies.CANONICAL_TABLE_POLICIES
        if position.get(spec.owner_revision, -1) > position[newest_installer]
    )
    assert late == [], (
        "these tables are registered but created after the last policy installer "
        f"({newest_installer}); add a registry re-apply revision: {late}"
    )


def test_every_registered_owner_revision_exists_in_the_chain() -> None:
    graph = _revision_graph()
    chain = set(_chain_from_head(graph))
    unknown = sorted(
        {
            spec.owner_revision
            for spec in policies.CANONICAL_TABLE_POLICIES
            if spec.owner_revision not in chain
        }
    )
    assert unknown == [], f"registry names revisions that are not in the chain: {unknown}"


def test_registry_tables_all_exist_in_metadata() -> None:
    from prodavan.infrastructure.persistence.models.base import Base

    missing = sorted(
        spec.table
        for spec in policies.CANONICAL_TABLE_POLICIES
        if spec.table not in Base.metadata.tables
    )
    assert missing == [], f"registry references tables that no model declares: {missing}"
