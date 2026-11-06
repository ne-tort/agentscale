"""MIG-WEBHOOK-01 API/UI delivery contract; deliberately not executed."""

from .acceptance_fixtures import CASES


def test_webhook_delivery_acceptance_scope_is_explicit() -> None:
    assert len(CASES) == 5
    assert any("subscription_enabled" in case for case in CASES)
    assert any("paused" in case for case in CASES)
    assert any("removed" in case for case in CASES)
    assert any("exhausted" in case for case in CASES)
    assert any("manual retry" in case for case in CASES)
