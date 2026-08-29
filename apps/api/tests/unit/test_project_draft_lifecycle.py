"""Unit tests — project draft lifecycle domain."""

from prodavan.domain.lifecycle import project_is_draft
from prodavan.domain.projects import ProjectStatus


class _Row:
    def __init__(self, status: str) -> None:
        self.status = status


def test_project_status_includes_draft() -> None:
    assert ProjectStatus.DRAFT == "draft"
    assert ProjectStatus.ERROR == "error"


def test_project_is_error_inert() -> None:
    from prodavan.domain.lifecycle import project_is_error, project_is_inert

    assert project_is_error(_Row("error"))
    assert project_is_inert(_Row("error"))
    assert not project_is_inert(_Row("active"))


def test_project_is_draft() -> None:
    assert project_is_draft(_Row("draft"))
    assert not project_is_draft(_Row("active"))


def test_agent_provider_from_key_row() -> None:
    from prodavan.application.project_service.public import agent_provider_from_key_row

    class _Key:
        def __init__(self, provider: str, api_kind: str) -> None:
            self.provider = provider
            self.api_kind = api_kind

    assert agent_provider_from_key_row(_Key("cursor", "cursor_sdk")) == "cursor"
    assert agent_provider_from_key_row(_Key("openai", "openai_api")) == "codex"
    assert agent_provider_from_key_row(_Key("anthropic", "anthropic_api")) == "claude_code"
