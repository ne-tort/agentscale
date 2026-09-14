"""Unit tests — owner-scoped module secret store."""

from __future__ import annotations

from pathlib import Path

import pytest

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.secrets.owner_module_secret_store import (
    OwnerModuleSecretStore,
    assert_owner_module_secret_scope,
    is_owner_module_secret_ref,
    parse_owner_module_secret_ref,
    secret_ref_prefix,
)
from prodavan.infrastructure.secrets.store import RoutingSecretStore


def test_owner_module_file_put_get_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vault_addr", None)
    monkeypatch.setattr(settings, "secrets_dir", tmp_path)
    store = OwnerModuleSecretStore(tmp_path)
    ref = store.put(owner_kind="platform", owner_id="platform", secret="dsn-secret")
    assert ref.startswith("file://module_secrets/platform/platform/")
    assert store.get(ref) == "dsn-secret"
    assert secret_ref_prefix(ref).endswith("…") or len(ref) <= 28
    assert is_owner_module_secret_ref(ref)
    assert parse_owner_module_secret_ref(ref) == (
        "platform",
        "platform",
        ref.rsplit("/", 1)[-1],
    )
    store.delete(ref)
    with pytest.raises(AppError) as exc:
        store.get(ref)
    assert exc.value.code == "SECRET_NOT_FOUND"


def test_owner_module_company_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vault_addr", None)
    store = OwnerModuleSecretStore(tmp_path)
    ref = store.put(owner_kind="company", owner_id="co_1", secret="pw")
    assert_owner_module_secret_scope(ref, owner_kind="company", owner_id="co_1")
    with pytest.raises(AppError) as exc:
        assert_owner_module_secret_scope(ref, owner_kind="company", owner_id="co_other")
    assert exc.value.code == "SECRET_SCOPE_VIOLATION"


def test_routing_reads_owner_module_file_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vault_addr", None)
    monkeypatch.setattr(settings, "secrets_dir", tmp_path)
    store = OwnerModuleSecretStore(tmp_path)
    ref = store.put(owner_kind="platform", owner_id="platform", secret="plat-secret")
    router = RoutingSecretStore(vault_store=None)
    assert router.get(ref) == "plat-secret"


def test_rejects_empty_and_bad_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vault_addr", None)
    store = OwnerModuleSecretStore(tmp_path)
    with pytest.raises(AppError) as exc:
        store.put(owner_kind="platform", owner_id="platform", secret="  ")
    assert exc.value.code == "VALIDATION_ERROR"
    with pytest.raises(AppError) as exc2:
        store.put(owner_kind="cabinet", owner_id="cab_1", secret="x")
    assert exc2.value.code == "VALIDATION_ERROR"


def test_assert_module_secret_ref_scope_owner_and_cabinet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from prodavan.infrastructure.secrets.owner_module_secret_store import (
        assert_module_secret_ref_scope,
    )

    monkeypatch.setattr(settings, "vault_addr", None)
    store = OwnerModuleSecretStore(tmp_path)
    owner_ref = store.put(owner_kind="platform", owner_id="platform", secret="x")
    assert_module_secret_ref_scope(
        owner_ref, owner_kind="platform", owner_id="platform"
    )
    with pytest.raises(AppError) as exc:
        assert_module_secret_ref_scope(owner_ref, cabinet_id="cab_1")
    assert exc.value.code == "SECRET_SCOPE_VIOLATION"
    cab_ref = "file://cabinet_secrets/cab_1/sec_abc"
    with pytest.raises(AppError) as exc2:
        assert_module_secret_ref_scope(
            cab_ref, owner_kind="platform", owner_id="platform"
        )
    assert exc2.value.code == "SECRET_SCOPE_VIOLATION"
