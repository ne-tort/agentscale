"""Typed, persistence-agnostic contracts for owner inventory analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Protocol


class OwnerRowPort(Protocol):
    """Read-only source of declarative ownership rows."""

    def iter_owner_rows(self) -> Iterable["OwnerRow"]: ...


class ReferencePort(Protocol):
    """Read-only source of owner/cabinet/project reference records."""

    def iter_owners(self) -> Iterable["OwnerReference"]: ...

    def iter_cabinets(self) -> Iterable["CabinetReference"]: ...

    def iter_projects(self) -> Iterable["ProjectReference"]: ...


@dataclass(frozen=True, slots=True)
class OwnerRow:
    """A source row whose owner identifier is being assessed."""

    row_key: str
    tenant_id: str
    owner_id: object
    cabinet_id: object | None = None
    project_id: object | None = None
    source_type: str = "String(80)"


@dataclass(frozen=True, slots=True)
class OwnerReference:
    owner_id: object
    tenant_id: str
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class CabinetReference:
    cabinet_id: object
    tenant_id: str
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class ProjectReference:
    project_id: object
    tenant_id: str
    cabinet_id: object | None = None
    deleted: bool = False


class FindingCode(str, Enum):
    SAFE_CANDIDATE = "safe_candidate"
    OVERLENGTH_ID = "overlength_id"
    MALFORMED_ID = "malformed_id"
    ORPHAN_OWNER = "orphan_owner"
    DELETED_OWNER = "deleted_owner"
    CROSS_TENANT_OWNER = "cross_tenant_owner"
    ORPHAN_CABINET = "orphan_cabinet"
    DELETED_CABINET = "deleted_cabinet"
    CROSS_TENANT_CABINET = "cross_tenant_cabinet"
    ORPHAN_PROJECT = "orphan_project"
    DELETED_PROJECT = "deleted_project"
    CROSS_TENANT_PROJECT = "cross_tenant_project"
    CABINET_PROJECT_MISMATCH = "cabinet_project_mismatch"
    AMBIGUOUS_OWNER = "ambiguous_owner"
    AMBIGUOUS_CABINET = "ambiguous_cabinet"
    AMBIGUOUS_PROJECT = "ambiguous_project"
    QUARANTINE_REQUIRED = "quarantine_required"


@dataclass(frozen=True, slots=True)
class Evidence:
    code: FindingCode
    message: str
    field: str | None = None
    related_id: str | None = None


@dataclass(frozen=True, slots=True)
class OwnerFinding:
    row_key: str
    tenant_id: str
    owner_id: object
    candidate: str | None
    evidence: tuple[Evidence, ...]
    quarantine: bool


@dataclass(frozen=True, slots=True)
class InventorySummary:
    rows: int
    candidates: int
    quarantined: int
    by_code: Mapping[FindingCode, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InventoryReport:
    findings: tuple[OwnerFinding, ...]
    summary: InventorySummary


class EmptyOwnerRows:
    """Convenient typed port for callers with no rows."""

    def __init__(self, rows: Iterable[OwnerRow] = ()) -> None:
        self._rows = rows

    def iter_owner_rows(self) -> Iterable[OwnerRow]:
        return self._rows


class EmptyReferences:
    """Convenient typed reference port for tests and declarative adapters."""

    def __init__(
        self,
        owners: Iterable[OwnerReference] = (),
        cabinets: Iterable[CabinetReference] = (),
        projects: Iterable[ProjectReference] = (),
    ) -> None:
        self._owners = owners
        self._cabinets = cabinets
        self._projects = projects

    def iter_owners(self) -> Iterable[OwnerReference]:
        return self._owners

    def iter_cabinets(self) -> Iterable[CabinetReference]:
        return self._cabinets

    def iter_projects(self) -> Iterable[ProjectReference]:
        return self._projects
