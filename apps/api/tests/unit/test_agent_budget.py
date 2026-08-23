"""Unit tests — agent budget enforcement helpers."""

from prodavan.domain.admin import CompanyAgentRuntimePolicy


def test_agent_policy_budget_validation() -> None:
    policy = CompanyAgentRuntimePolicy(max_agent_tokens_month=1000, max_tokens_per_run=500)
    policy.validate()
    bad = CompanyAgentRuntimePolicy(max_agent_tokens_month=0)
    try:
        bad.validate()
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
