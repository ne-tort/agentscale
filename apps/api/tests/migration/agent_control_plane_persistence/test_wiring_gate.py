"""Static gates for host registration and the migration-only wiring boundary."""

from __future__ import annotations

import ast
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[3]
PERSISTENCE_ZONE = API_ROOT / "src" / "prodavan" / "infrastructure" / "persistence" / "agent_control_plane"
PERSISTENCE_ROOT = API_ROOT / "src" / "prodavan" / "infrastructure" / "persistence"
MODELS_INIT = API_ROOT / "src" / "prodavan" / "infrastructure" / "persistence" / "models" / "__init__.py"
REVISION = API_ROOT / "alembic" / "versions" / "2026110202_agent_control_plane_persistence.py"
API_SOURCE = API_ROOT / "src" / "prodavan" / "api" / "migration_agent_runs" / "dependencies.py"
PRODAVAN_SOURCE = API_ROOT / "src" / "prodavan"

EXPECTED_MODELS = {
    "AgentControlPlaneAggregateRow",
    "AgentControlPlaneCommandRow",
    "AgentControlPlaneEnqueueIntentRow",
    "AgentControlPlaneEventRow",
    "AgentControlPlaneOutboxEventRow",
    "AgentControlPlaneRunScopeRow",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_persistence_models_are_registered_once_and_remain_zone_owned() -> None:
    tree = _tree(MODELS_INIT)
    registration_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "prodavan.infrastructure.persistence.agent_control_plane.models"
    ]
    zone_tree = _tree(PERSISTENCE_ZONE / "models.py")
    declared = {node.name for node in zone_tree.body if isinstance(node, ast.ClassDef)}
    assert declared == EXPECTED_MODELS
    assert len(registration_imports) == 1
    imported_names = [alias.name for alias in registration_imports[0].names]
    assert len(imported_names) == len(EXPECTED_MODELS)
    assert set(imported_names) == EXPECTED_MODELS
    assert all(alias.asname is None for alias in registration_imports[0].names)

    exports = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    )
    exported_control_plane_models = {
        name for name in exports if name.startswith("AgentControlPlane") and name.endswith("Row")
    }
    assert exported_control_plane_models == EXPECTED_MODELS

    import prodavan.infrastructure.persistence.models as models
    from prodavan.infrastructure.persistence.models.base import Base

    for class_name in EXPECTED_MODELS:
        model = getattr(models, class_name)
        assert Base.metadata.tables[model.__tablename__] is model.__table__
        mapped_owners = [
            mapper.class_ for mapper in Base.registry.mappers if mapper.local_table is model.__table__
        ]
        assert mapped_owners == [model]

    declarations = {"AgentRunRow": [], "ApprovalRequestRow": []}
    for path in PERSISTENCE_ROOT.rglob("*.py"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ClassDef) and node.name in declarations:
                declarations[node.name].append(path)
    legacy_owner = PERSISTENCE_ROOT / "models" / "agent_control_plane.py"
    assert declarations == {
        "AgentRunRow": [legacy_owner],
        "ApprovalRequestRow": [legacy_owner],
    }
    assert all((PERSISTENCE_ZONE / item).is_file() for item in ("models.py", "repository.py", "WIRING.md"))


def test_migration_is_authored_against_current_head_but_not_executed_here() -> None:
    tree = _tree(REVISION)
    assignments = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in {"revision", "down_revision"}:
                assignments[node.targets[0].id] = ast.literal_eval(node.value)
    assert assignments == {"revision": "2026110202", "down_revision": "2026110201"}
    assert "agent_runs" in REVISION.read_text(encoding="utf-8")
    assert "agent_control_plane_enqueue_intents" in REVISION.read_text(encoding="utf-8")
    wiring = (PERSISTENCE_ZONE / "WIRING.md").read_text(encoding="utf-8")
    assert "revision `2026110202` is authored only and not executed in this worktree" in wiring


def test_wiring_document_states_registration_and_dispatch_blockers() -> None:
    wiring = (PERSISTENCE_ZONE / "WIRING.md").read_text(encoding="utf-8")
    assert "Metadata registration is complete" in wiring
    assert "revision `2026110202` is authored only and not executed in this worktree" in wiring
    assert "dispatcher/repair consumer" in wiring
    assert "RLS policy verification" in wiring
    assert "explicit production approval" in wiring
    assert "writes_enabled=False" in wiring
    assert "No DDL, Alembic, live database action, or test execution was performed" in wiring


def test_feature_gated_binding_keeps_writes_disabled_after_metadata_registration() -> None:
    models = _tree(MODELS_INIT)
    imports = {
        node.module: {alias.name for alias in node.names}
        for node in models.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert imports["prodavan.infrastructure.persistence.agent_control_plane.models"] == EXPECTED_MODELS

    dependency = API_SOURCE.read_text(encoding="utf-8")
    assert dependency.index("if not _feature_enabled") < dependency.index("missing_model_tables(set(Base.metadata.tables))")
    assert dependency.index("missing_model_tables(set(Base.metadata.tables))") < dependency.index(
        "SqlAlchemyAgentControlPlaneRepository(session_factory=get_session_factory())"
    )
    assert "writes_enabled: bool = False" in dependency
    assert "return AgentRunBackend(transaction=transaction, session=session, writes_enabled=False)" in dependency
    assert 'blocker="agent_control_plane_dispatcher_unwired"' in dependency
    assert "if not self.writes_enabled:" in dependency


def test_enqueue_intent_has_no_dispatch_consumer_yet() -> None:
    consumers = []
    for path in PRODAVAN_SOURCE.rglob("*.py"):
        if path in {PERSISTENCE_ZONE / "models.py", PERSISTENCE_ZONE / "repository.py"}:
            continue
        if path == MODELS_INIT:
            continue
        if "AgentControlPlaneEnqueueIntentRow" in path.read_text(encoding="utf-8"):
            consumers.append(path)
    assert consumers == []

    wiring = (PERSISTENCE_ZONE / "WIRING.md").read_text(encoding="utf-8")
    assert "No dispatcher/repair consumer owns the enqueue intents" in wiring
