"""Unit tests — L07 project domain helpers."""

from prodavan.domain.admin import CompanyAgentRuntimePolicy, attachment_max_bytes
from prodavan.domain.projects import (
    container_ref_for,
    is_allowed_attachment_filename,
    slugify_name,
    workspace_key_for,
)


def test_slugify_name() -> None:
    assert slugify_name("My Project!") == "my-project"


def test_workspace_and_container_ref() -> None:
    assert workspace_key_for("proj_abc123") == "abc123"
    assert container_ref_for("abc123") == "local-ws:abc123"


def test_attachment_extension_allowlist() -> None:
    assert is_allowed_attachment_filename("note.txt")
    assert is_allowed_attachment_filename("scan.PDF")
    assert not is_allowed_attachment_filename("malware.exe")
    assert not is_allowed_attachment_filename("noext")


def test_forbidden_attachment_content_magic() -> None:
    from prodavan.domain.projects import is_forbidden_attachment_content

    assert is_forbidden_attachment_content(b"MZ\x90\x00")
    assert is_forbidden_attachment_content(b"\x7fELF\x01\x01")
    assert not is_forbidden_attachment_content(b"hello text")
    assert not is_forbidden_attachment_content(b"%PDF-1.4")


def test_attachment_max_bytes_from_policy() -> None:
    policy = CompanyAgentRuntimePolicy(max_attachment_mb=5)
    assert attachment_max_bytes(policy) == 5 * 1024 * 1024
