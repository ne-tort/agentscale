"""Unit tests — company subscription metrics (L04)."""

from datetime import UTC, datetime, timedelta

from prodavan.domain.admin.types import subscription_read_model


def test_subscription_lifetime_never_expires() -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    out = subscription_read_model(ends_at=None, lifetime=True, now=now)
    assert out["subscription_lifetime"] is True
    assert out["subscription_expiring_soon"] is False
    assert out["subscription_expired"] is False


def test_subscription_expiring_soon_within_window() -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    ends = now + timedelta(days=10)
    out = subscription_read_model(ends_at=ends, lifetime=False, now=now, expiring_days=30)
    assert out["subscription_expiring_soon"] is True
    assert out["subscription_expired"] is False


def test_subscription_expired_past_end() -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    ends = now - timedelta(days=1)
    out = subscription_read_model(ends_at=ends, lifetime=False, now=now)
    assert out["subscription_expired"] is True
    assert out["subscription_expiring_soon"] is False
