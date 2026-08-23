"""Unit tests — L04 admin domain and quota logic."""

from prodavan.domain.admin import DEFAULT_CABINET_QUOTA, CompanyAgentRuntimePolicy, CompanyCabinetQuota


def test_default_cabinet_quota_values() -> None:
    assert DEFAULT_CABINET_QUOTA.max_cabinets == 10
    assert DEFAULT_CABINET_QUOTA.max_packages_per_cabinet == 20


def test_cabinet_quota_validation() -> None:
    ok = CompanyCabinetQuota(max_cabinets=1, max_packages_per_cabinet=0, max_bundle_import_mb=1)
    ok.validate()
    bad = CompanyCabinetQuota(max_cabinets=0)
    try:
        bad.validate()
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_agent_policy_preset_validation() -> None:
    policy = CompanyAgentRuntimePolicy(tool_preset="chat_readonly")
    policy.validate()
    bad = CompanyAgentRuntimePolicy(tool_preset="unknown")
    try:
        bad.validate()
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
