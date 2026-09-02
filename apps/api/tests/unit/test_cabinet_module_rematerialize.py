"""Unit tests — rematerialize metadata on cabinet module row CRUD responses."""

from prodavan.application.cabinets.cabinet_module_service import _attach_rematerialize


def test_attach_rematerialize_when_scheduled() -> None:
    row = {"row_id": "r1", "body": {"name": "x"}}
    remat = {"scheduled": 2, "enqueued": ["p1", "p2"], "sync": []}
    out = _attach_rematerialize(row, remat)
    assert out["rematerialize"]["scheduled"] == 2
    assert out["rematerialize"]["mode"] == "scheduled"
    assert out["workspace_sync"]["scheduled"] == 2
    assert out["row_id"] == "r1"


def test_attach_rematerialize_skips_zero_scheduled() -> None:
    row = {"row_id": "r1", "body": {}}
    remat = {"scheduled": 0, "skipped": True, "reason": "no_materialize_rules"}
    out = _attach_rematerialize(row, remat)
    assert out["workspace_sync"]["skipped"] is True
    assert out["rematerialize"]["reason"] == "no_materialize_rules"


def test_attach_rematerialize_when_marked_outdated() -> None:
    row = {"row_id": "r1", "body": {}}
    remat = {"scheduled": 0, "marked_outdated": 3, "cabinet_id": "cab_1"}
    out = _attach_rematerialize(row, remat)
    assert out["rematerialize"]["marked_outdated"] == 3
    assert out["rematerialize"]["mode"] == "deferred"
    assert out["workspace_sync"]["cabinet_id"] == "cab_1"
