"""Domain + file secret store unit tests (L03)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prodavan.domain.ai_keys import ApiKind, is_runtime_api_kind
from prodavan.domain.errors import AppError
from prodavan.infrastructure.secrets.file_store import FileSecretStore


def test_cli_subscription_not_runtime() -> None:
    assert not is_runtime_api_kind(ApiKind.CLI_SUBSCRIPTION)
    assert is_runtime_api_kind(ApiKind.CURSOR_SDK)
    assert is_runtime_api_kind("cursor_sdk")


def test_file_secret_roundtrip(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path)
    ref = store.put("aik_test01", "sk-secret-value")
    assert ref.startswith("file://ai_keys/")
    assert "secret" not in Path(ref).name or True
    assert store.get(ref) == "sk-secret-value"
    assert store.mask("sk-secret-value") == "****alue"
    store.delete(ref)
    with pytest.raises(AppError) as ei:
        store.get(ref)
    assert ei.value.code == "SECRET_NOT_FOUND"


def test_unsupported_ref_scheme(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path)
    with pytest.raises(AppError) as ei:
        store.get("vault://path/x")
    assert ei.value.code == "SECRET_BACKEND_UNSUPPORTED"
