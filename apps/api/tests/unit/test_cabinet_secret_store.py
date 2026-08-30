"""Unit tests — cabinet-scoped secret store."""

from __future__ import annotations

from pathlib import Path

import pytest

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.secrets.cabinet_secret_store import (
    CabinetSecretStore,
    secret_ref_prefix,
)
from prodavan.infrastructure.secrets.store import RoutingSecretStore


def test_cabinet_file_put_get_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vault_addr", None)
    monkeypatch.setattr(settings, "secrets_dir", tmp_path)
    store = CabinetSecretStore(tmp_path)
    ref = store.put(cabinet_id="cab_1", secret="token-123")
    assert ref.startswith("file://cabinet_secrets/cab_1/")
    assert store.get(ref) == "token-123"
    assert secret_ref_prefix(ref).endswith("…") or len(ref) <= 28
    store.delete(ref)
    with pytest.raises(AppError) as exc:
        store.get(ref)
    assert exc.value.code == "SECRET_NOT_FOUND"


def test_cabinet_rejects_empty_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vault_addr", None)
    store = CabinetSecretStore(tmp_path)
    with pytest.raises(AppError) as exc:
        store.put(cabinet_id="cab_1", secret="   ")
    assert exc.value.code == "VALIDATION_ERROR"


def test_routing_reads_cabinet_file_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vault_addr", None)
    monkeypatch.setattr(settings, "secrets_dir", tmp_path)
    cab_store = CabinetSecretStore(tmp_path)
    ref = cab_store.put(cabinet_id="cab_x", secret="cab-secret")
    router = RoutingSecretStore(vault_store=None)
    assert router.get(ref) == "cab-secret"
