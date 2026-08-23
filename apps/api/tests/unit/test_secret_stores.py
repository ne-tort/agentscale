"""Unit tests — Vault secret store + routing (L03)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.secrets.file_store import FileSecretStore
from prodavan.infrastructure.secrets.store import RoutingSecretStore
from prodavan.infrastructure.secrets.vault_store import VaultSecretStore


class _FakeTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and "/data/" in path:
            key = path.rsplit("/", 1)[-1]
            payload = request.read()
            import json

            body = json.loads(payload.decode())
            self.store[key] = body["data"]["value"]
            return httpx.Response(200, json={"data": {}})
        if request.method == "GET" and "/data/" in path:
            key = path.rsplit("/", 1)[-1]
            if key not in self.store:
                return httpx.Response(404, json={"errors": ["not found"]})
            return httpx.Response(200, json={"data": {"data": {"value": self.store[key]}}})
        if request.method == "DELETE":
            key = path.rsplit("/", 1)[-1]
            self.store.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(500, json={"errors": ["unexpected"]})


def test_vault_put_get_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport()
    monkeypatch.setattr(settings, "vault_addr", "http://vault.test")
    monkeypatch.setattr(settings, "vault_token", "tok")

    def fake_post(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    def fake_get(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.get(url, **kwargs)

    def fake_delete(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.delete(url, **kwargs)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "delete", fake_delete)

    store = VaultSecretStore(addr="http://vault.test", token="tok")
    ref = store.put("aik_test1", "super-secret")
    assert ref == "vault://ai_keys/aik_test1"
    assert store.get(ref) == "super-secret"
    store.delete(ref)


def test_routing_prefers_vault_when_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport()
    monkeypatch.setattr(settings, "vault_addr", "http://vault.test")
    monkeypatch.setattr(settings, "vault_token", "tok")
    monkeypatch.setattr(settings, "secrets_dir", tmp_path)

    def fake_post(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    def fake_get(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.get(url, **kwargs)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)

    router = RoutingSecretStore(
        file_store=FileSecretStore(tmp_path),
        vault_store=VaultSecretStore(addr="http://vault.test", token="tok"),
    )
    ref = router.put("aik_r1", "via-vault")
    assert ref.startswith("vault://")
    assert router.get(ref) == "via-vault"


def test_routing_reads_file_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vault_addr", None)
    file_store = FileSecretStore(tmp_path)
    ref = file_store.put("aik_f1", "on-disk")
    router = RoutingSecretStore(file_store=file_store, vault_store=None)
    assert router.get(ref) == "on-disk"


def test_file_rejects_vault_scheme(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path)
    with pytest.raises(AppError) as ei:
        store.get("vault://ai_keys/x")
    assert ei.value.code == "SECRET_BACKEND_UNSUPPORTED"
