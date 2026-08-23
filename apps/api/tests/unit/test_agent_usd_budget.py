"""Unit tests — agent policy USD budget fields (L08)."""

from decimal import Decimal

import pytest

from prodavan.domain.admin import CompanyAgentRuntimePolicy


def test_policy_accepts_max_cost_usd_month() -> None:
    policy = CompanyAgentRuntimePolicy(max_cost_usd_month=Decimal("10.50"))
    policy.validate()
    assert policy.max_cost_usd_month == Decimal("10.50")


def test_policy_rejects_non_positive_usd_cap() -> None:
    with pytest.raises(ValueError, match="max_cost_usd_month"):
        CompanyAgentRuntimePolicy(max_cost_usd_month=Decimal("0")).validate()
