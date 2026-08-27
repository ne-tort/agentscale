"""Shared ownership flags for company-facing API rows."""

from __future__ import annotations

from enum import StrEnum
from typing import TypedDict


class OwnerScope(StrEnum):
    PLATFORM = "platform"
    COMPANY = "company"


class EntitySource(StrEnum):
    PLATFORM_ASSIGNED = "platform_assigned"
    COMPANY_LOCAL = "company_local"


class CompanyViewFlags(TypedDict):
    writable: bool
    source: str


def is_company_registry(owner_scope: str) -> bool:
    """Registry row is company-owned (admin/catalog semantics)."""
    return owner_scope == OwnerScope.COMPANY


def registry_source(*, owner_scope: str) -> str:
    """Catalog origin for admin/registry lists (not grant-relative)."""
    if is_company_registry(owner_scope):
        return EntitySource.COMPANY_LOCAL
    return EntitySource.PLATFORM_ASSIGNED


def company_writable(*, owner_scope: str, owner_company_id: str | None, company_id: str) -> bool:
    return owner_scope == OwnerScope.COMPANY and owner_company_id == company_id


def company_source(*, owner_scope: str, owner_company_id: str | None, company_id: str) -> str:
    if company_writable(owner_scope=owner_scope, owner_company_id=owner_company_id, company_id=company_id):
        return EntitySource.COMPANY_LOCAL
    return EntitySource.PLATFORM_ASSIGNED


def company_view_flags(*, owner_scope: str, owner_company_id: str | None, company_id: str) -> CompanyViewFlags:
    """Derived flags for a company actor viewing a grantable entity."""
    return CompanyViewFlags(
        writable=company_writable(
            owner_scope=owner_scope,
            owner_company_id=owner_company_id,
            company_id=company_id,
        ),
        source=company_source(
            owner_scope=owner_scope,
            owner_company_id=owner_company_id,
            company_id=company_id,
        ),
    )
