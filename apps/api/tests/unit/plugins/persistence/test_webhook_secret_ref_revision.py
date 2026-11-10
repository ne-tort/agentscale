"""Static checks for additive grant webhook_ref revision (not run in freeze)."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = (
        Path(__file__).parents[4]
        / "alembic/versions/2026110904_plugin_grant_webhook_secret_ref.py"
    )
    spec = importlib.util.spec_from_file_location("revision_2026110904_webhook_ref", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_is_additive_and_leaves_legacy_refs_null(monkeypatch) -> None:
    module = _module()
    calls = []
    monkeypatch.setattr(module.op, "add_column", lambda *args: calls.append(args))

    module.upgrade()

    assert module.revision == "2026110904"
    assert module.down_revision == "2026110903"
    assert len(calls) == 1
    table, column = calls[0]
    assert table == "plugin_capability_grants"
    assert column.name == "webhook_secret_ref"
    assert column.nullable is True
    assert column.server_default is None
