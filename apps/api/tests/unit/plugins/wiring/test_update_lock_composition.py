"""Enabled admin composition contract for the distributed update lock."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from prodavan.application.plugins.wiring import install
from prodavan.infrastructure.persistence.repositories.plugins.update_lock import (
    SqlPluginUpdateLock,
)
from tests.unit.plugins.admin.fakes import Harness


def _invoke(monkeypatch: pytest.MonkeyPatch, *, session_factory, override=None):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return replace(Harness().ports(), update_lock=kwargs["update_lock"])

    def fake_wrap(step, fn, *args, **kwargs):
        if fn is fake_build:
            return fake_build(**kwargs)
        if fn is SqlPluginUpdateLock:
            return fn(**kwargs)
        if getattr(fn, "__name__", "") == "_zone_services":
            return {
                "tenant_db": object(),
                "object_store": object(),
                "object_buckets": object(),
                "artifact_registry": object(),
                "activator": object(),
            }
        return object()

    monkeypatch.setattr(install, "_load", lambda *args: fake_build)
    monkeypatch.setattr(install, "_wrap", fake_wrap)
    repos = SimpleNamespace(
        grant_store=SimpleNamespace(_session_factory=session_factory, _session=None),
        instance_store=object(),
        artifact_store=object(),
        grant_revocation=object(),
        publication_approvals=object(),
        audit=object(),
    )
    overrides = {
        "platform_admin": object(),
        "routes": object(),
        "instance_store": object(),
        "webhook_secrets": object(),
        "webhook_secret_resolver": object(),
        "secret_projection": object(),
        "secret_delivery_control": object(),
        "deployment_admin": object(),
    }
    if override is not None:
        overrides["update_lock"] = override
    ports = install._admin_ports(
        runtime=object(),
        repos=repos,
        config=SimpleNamespace(claims_secret="test-secret"),
        ov=overrides,
        unwired=[],
        services_out={},
        in_memory=[],
    )
    return ports, captured


def test_enabled_composition_lock_type_identity_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def shared_factory():
        raise AssertionError("composition test must not open SQL sessions")

    ports, captured = _invoke(monkeypatch, session_factory=shared_factory)
    assert isinstance(ports.update_lock, SqlPluginUpdateLock)
    assert ports.update_lock._session_factory is shared_factory
    assert captured["update_lock"] is ports.update_lock

    explicit = object()
    ports, captured = _invoke(
        monkeypatch, session_factory=shared_factory, override=explicit
    )
    assert ports.update_lock is explicit
    assert captured["update_lock"] is explicit


def test_enabled_composition_missing_shared_session_factory_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(install.PluginCompositionError, match="SqlPluginUpdateLock"):
        _invoke(monkeypatch, session_factory=None)
