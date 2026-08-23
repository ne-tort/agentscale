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


def test_pause_exempt_trigger_kinds() -> None:
    from prodavan.domain.projects import PAUSE_EXEMPT_TRIGGER_KINDS, SUBSCRIPTION_EXEMPT_TRIGGER_KINDS

    assert "project.prepare" in PAUSE_EXEMPT_TRIGGER_KINDS
    assert "chat.message" not in PAUSE_EXEMPT_TRIGGER_KINDS
    assert PAUSE_EXEMPT_TRIGGER_KINDS == SUBSCRIPTION_EXEMPT_TRIGGER_KINDS


def test_attachment_extension_allowlist() -> None:
    assert is_allowed_attachment_filename("note.txt")
    assert is_allowed_attachment_filename("scan.PDF")
    assert is_allowed_attachment_filename("data.json")
    assert not is_allowed_attachment_filename("malware.exe")
    assert not is_allowed_attachment_filename("noext")


def test_forbidden_attachment_content_magic() -> None:
    from prodavan.domain.projects import is_forbidden_attachment_content

    assert is_forbidden_attachment_content(b"MZ\x90\x00")
    assert is_forbidden_attachment_content(b"\x7fELF\x01\x01")
    assert is_forbidden_attachment_content(b"\0asm\x01\x00")
    assert is_forbidden_attachment_content(b"#!/bin/sh\necho hi")
    assert is_forbidden_attachment_content(b"\xef\xbb\xbf#!/usr/bin/env python3\n")
    assert is_forbidden_attachment_content(b"<?php echo 1;")
    assert not is_forbidden_attachment_content(b"hello text")
    assert not is_forbidden_attachment_content(b"%PDF-1.4")
    assert not is_forbidden_attachment_content(b"# markdown heading\n")


def test_sniff_attachment_content_type() -> None:
    from prodavan.domain.projects import sniff_attachment_content_type

    assert sniff_attachment_content_type(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert sniff_attachment_content_type(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert sniff_attachment_content_type(b"%PDF-1.7") == "application/pdf"
    assert sniff_attachment_content_type(b"plain", filename="note.txt") == "text/plain"
    assert sniff_attachment_content_type(b'{"a":1}', filename="data.json") == "application/json"
    assert sniff_attachment_content_type(b"x", fallback="application/json") == "application/json"


def test_project_is_idle() -> None:
    from datetime import UTC, datetime, timedelta

    from prodavan.domain.projects import project_is_idle

    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    recent = now - timedelta(hours=1)
    old = now - timedelta(hours=48)
    assert not project_is_idle(last_activity_at=recent, now=now, idle_pause_after_hours=24)
    assert project_is_idle(last_activity_at=old, now=now, idle_pause_after_hours=24)
    assert not project_is_idle(last_activity_at=old, now=now, idle_pause_after_hours=0)
    assert not project_is_idle(last_activity_at=None, now=now, idle_pause_after_hours=24)


def test_idle_pause_policy_default_off() -> None:
    policy = CompanyAgentRuntimePolicy()
    assert policy.idle_pause_enabled() is False
    on = CompanyAgentRuntimePolicy(idle_pause_after_hours=24)
    assert on.idle_pause_enabled() is True


def test_webhook_hmac_signature() -> None:
    from prodavan.domain.projects import verify_webhook_signature, webhook_signature

    body = b'{"text":"hi"}'
    sig = webhook_signature("s3cret", body)
    assert sig.startswith("sha256=")
    assert verify_webhook_signature(secret="s3cret", body=body, header=sig)
    assert not verify_webhook_signature(secret="s3cret", body=body, header="sha256=dead")


def test_attachment_max_bytes_from_policy() -> None:
    policy = CompanyAgentRuntimePolicy(max_attachment_mb=5)
    assert attachment_max_bytes(policy) == 5 * 1024 * 1024
