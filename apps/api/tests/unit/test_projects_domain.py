"""Unit tests — L07 project domain helpers."""

from prodavan.domain.projects import container_ref_for, slugify_name, workspace_key_for


def test_slugify_name() -> None:
    assert slugify_name("My Project!") == "my-project"


def test_workspace_and_container_ref() -> None:
    assert workspace_key_for("proj_abc123") == "abc123"
    assert container_ref_for("abc123") == "local-ws:abc123"
