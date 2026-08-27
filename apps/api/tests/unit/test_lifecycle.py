"""Unit — domain lifecycle helpers."""

from __future__ import annotations

from types import SimpleNamespace

from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.identity import EmployeeStatus
from prodavan.domain.lifecycle import (
    cabinet_is_inert,
    cabinet_is_paused,
    cabinet_is_soft_deleted,
    company_is_inert,
    employee_is_inert,
    employee_is_paused,
    project_is_inert,
)
from prodavan.domain.projects import ProjectStatus


def test_project_inert() -> None:
    assert project_is_inert(SimpleNamespace(status=ProjectStatus.PAUSED))
    assert project_is_inert(SimpleNamespace(status=ProjectStatus.DELETED))
    assert not project_is_inert(SimpleNamespace(status=ProjectStatus.ACTIVE))


def test_cabinet_archive_is_pause_deleted_is_soft() -> None:
    assert cabinet_is_paused(SimpleNamespace(status=CabinetStatus.ARCHIVED))
    assert cabinet_is_soft_deleted(SimpleNamespace(status=CabinetStatus.DELETED))
    assert cabinet_is_inert(SimpleNamespace(status=CabinetStatus.ARCHIVED))
    assert cabinet_is_inert(SimpleNamespace(status=CabinetStatus.DELETED))


def test_employee_disabled_is_pause() -> None:
    assert employee_is_paused(SimpleNamespace(status=EmployeeStatus.DISABLED, deleted_at=None))
    assert employee_is_inert(SimpleNamespace(status=EmployeeStatus.DISABLED, deleted_at=None))
    assert employee_is_inert(
        SimpleNamespace(status=EmployeeStatus.DISABLED, deleted_at="2026-01-01")
    )


def test_company_paused_or_deleted() -> None:
    assert company_is_inert(SimpleNamespace(deleted_at=None, status="paused"))
    assert company_is_inert(SimpleNamespace(deleted_at="x", status="active"))
    assert not company_is_inert(SimpleNamespace(deleted_at=None, status="active"))
