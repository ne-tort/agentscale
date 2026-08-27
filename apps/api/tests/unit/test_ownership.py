"""Unit tests for shared ownership flags."""

from prodavan.domain.ownership import (
    EntitySource,
    OwnerScope,
    company_source,
    company_view_flags,
    company_writable,
    is_company_registry,
    registry_source,
)


def test_company_writable_only_for_owner() -> None:
    assert company_writable(owner_scope=OwnerScope.COMPANY, owner_company_id="co_a", company_id="co_a")
    assert not company_writable(owner_scope=OwnerScope.COMPANY, owner_company_id="co_a", company_id="co_b")
    assert not company_writable(owner_scope=OwnerScope.PLATFORM, owner_company_id=None, company_id="co_a")


def test_company_source_derivation() -> None:
    assert (
        company_source(owner_scope=OwnerScope.COMPANY, owner_company_id="co_a", company_id="co_a")
        == EntitySource.COMPANY_LOCAL
    )
    assert (
        company_source(owner_scope=OwnerScope.PLATFORM, owner_company_id=None, company_id="co_a")
        == EntitySource.PLATFORM_ASSIGNED
    )


def test_registry_source() -> None:
    assert registry_source(owner_scope=OwnerScope.PLATFORM) == EntitySource.PLATFORM_ASSIGNED
    assert registry_source(owner_scope=OwnerScope.COMPANY) == EntitySource.COMPANY_LOCAL
    assert is_company_registry(OwnerScope.COMPANY)
    assert not is_company_registry(OwnerScope.PLATFORM)


def test_company_view_flags_bundle() -> None:
    flags = company_view_flags(
        owner_scope=OwnerScope.PLATFORM,
        owner_company_id=None,
        company_id="co_a",
    )
    assert flags["writable"] is False
    assert flags["source"] == EntitySource.PLATFORM_ASSIGNED

    local = company_view_flags(
        owner_scope=OwnerScope.COMPANY,
        owner_company_id="co_a",
        company_id="co_a",
    )
    assert local["writable"] is True
    assert local["source"] == EntitySource.COMPANY_LOCAL
