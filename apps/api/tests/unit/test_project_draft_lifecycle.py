"""Unit tests — project draft lifecycle domain."""

from prodavan.domain.lifecycle import project_is_draft
from prodavan.domain.projects import ProjectStatus


class _Row:
    def __init__(self, status: str) -> None:
        self.status = status


def test_project_status_includes_draft() -> None:
    assert ProjectStatus.DRAFT == "draft"


def test_project_is_draft() -> None:
    assert project_is_draft(_Row("draft"))
    assert not project_is_draft(_Row("active"))
