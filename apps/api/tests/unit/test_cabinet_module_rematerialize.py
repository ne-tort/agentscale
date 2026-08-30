"""Unit tests — rematerialize metadata on cabinet module row CRUD responses."""

from prodavan.application.cabinets.cabinet_module_service import _attach_rematerialize


def test_attach_rematerialize_when_scheduled() -> None:
    row = {"row_id": "r1", "body": {"name": "x"}}
    remat = {"scheduled": 2, "enqueued": ["p1", "p2"], "sync": []}
    out = _attach_rematerialize(row, remat)
    assert out["rematerialize"] == remat
    assert out["row_id"] == "r1"


def test_attach_rematerialize_skips_zero_scheduled() -> None:
    row = {"row_id": "r1", "body": {}}
    remat = {"scheduled": 0, "skipped": True, "reason": "no_materialize_rules"}
    out = _attach_rematerialize(row, remat)
    assert "rematerialize" not in out
