"""Content domain enums."""

from __future__ import annotations

from enum import StrEnum


class ContentVisibility(StrEnum):
    PRIVATE = "private"
    COMPANY = "company"


class AliasStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ContentResourceKind(StrEnum):
    ASSET = "asset"
    ALIAS = "alias"


class AclPrincipalKind(StrEnum):
    EMPLOYEE = "employee"
    COMPANY = "company"
    PLATFORM = "platform"


class AclPermission(StrEnum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class AssetLinkKind(StrEnum):
    PROJECT_ATTACHMENT = "project_attachment"
    PROJECT = "project"
    MODULE = "module"
