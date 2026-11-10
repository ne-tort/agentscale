"""Offline acceptance tests for mounted Kubernetes Secret file refs.

Written under the migration verification freeze; these tests are not executed
by the implementation task and perform no cluster, Vault, or subprocess work.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from prodavan.domain.errors import AppError
from prodavan.infrastructure.plugins.minio.config import MinioPluginConfig
from prodavan.infrastructure.secrets.file_store import FileSecretStore
from prodavan.infrastructure.secrets.k8s_file_store import (
    DEFAULT_ROOT,
    K8sFileSecretStore,
)
from prodavan.infrastructure.secrets.store import RoutingSecretStore

ADMIN_NAME = "agentscale-minio-plugin-admin"
PRESIGNER_NAME = "agentscale-minio-plugin-presigner"
ADMIN_ACCESS_REF = f"k8s-secret://{ADMIN_NAME}/access-key"
ADMIN_SECRET_REF = f"k8s-secret://{ADMIN_NAME}/secret-key"
PRESIGN_ACCESS_REF = f"k8s-secret://{PRESIGNER_NAME}/access-key"
PRESIGN_SECRET_REF = f"k8s-secret://{PRESIGNER_NAME}/secret-key"


class _UnexpectedBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, secret_ref: str) -> str:
        self.calls.append(secret_ref)
        raise AssertionError("k8s-secret refs must not fall back to file/vault")

    def put(self, key_id: str, secret: str) -> str:
        raise AssertionError("unexpected write")

    def delete(self, secret_ref: str) -> None:
        self.calls.append(secret_ref)
        raise AssertionError("k8s-secret refs must not fall back to file/vault")


class _VaultStub:
    def get(self, secret_ref: str) -> str:
        assert secret_ref == "vault://ai_keys/legacy"
        return "legacy-vault-value"

    def put(self, key_id: str, secret: str) -> str:
        raise AssertionError("not needed by this test")

    def delete(self, secret_ref: str) -> None:
        raise AssertionError("not needed by this test")


def _write_key(root: Path, secret_name: str, key: str, value: str | bytes) -> Path:
    directory = root / secret_name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / key
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")
    return path


def _router(root: Path) -> tuple[RoutingSecretStore, _UnexpectedBackend, _UnexpectedBackend]:
    file_backend = _UnexpectedBackend()
    vault_backend = _UnexpectedBackend()
    router = RoutingSecretStore(
        file_store=file_backend,  # type: ignore[arg-type]
        vault_store=vault_backend,  # type: ignore[arg-type]
        k8s_file_store=K8sFileSecretStore(root),
    )
    return router, file_backend, vault_backend


def test_default_root_is_the_kubernetes_secret_mount_root() -> None:
    assert DEFAULT_ROOT == Path("/var/run/secrets")
    assert K8sFileSecretStore()._root == DEFAULT_ROOT


def test_minio_config_resolves_the_four_exact_refs_without_fallback(tmp_path: Path) -> None:
    values = {
        ADMIN_ACCESS_REF: "plugin-object-admin",
        ADMIN_SECRET_REF: "unit-admin-secret",
        PRESIGN_ACCESS_REF: "plugin-object-presigner",
        PRESIGN_SECRET_REF: "unit-presigner-secret",
    }
    for ref, value in values.items():
        name, key = ref.removeprefix("k8s-secret://").split("/")
        _write_key(tmp_path, name, key, f"{value}\n")
    router, file_backend, vault_backend = _router(tmp_path)
    env = {
        "PLUGIN_MINIO_ENDPOINT": "http://agentscale-minio:9000",
        "PLUGIN_MINIO_ADMIN_ACCESS_KEY_REF": ADMIN_ACCESS_REF,
        "PLUGIN_MINIO_ADMIN_SECRET_KEY_REF": ADMIN_SECRET_REF,
        "PLUGIN_MINIO_PRESIGN_ACCESS_KEY_REF": PRESIGN_ACCESS_REF,
        "PLUGIN_MINIO_PRESIGN_SECRET_KEY_REF": PRESIGN_SECRET_REF,
        "PLUGIN_MINIO_STS_ENABLED": "false",
        "PLUGIN_MINIO_REGION": "us-east-1",
    }

    config = MinioPluginConfig.from_secret_refs(secret_store=router, env=env)

    assert config.admin_access_key == values[ADMIN_ACCESS_REF]
    assert config.admin_secret_key == values[ADMIN_SECRET_REF]
    assert config.presign_access_key == values[PRESIGN_ACCESS_REF]
    assert config.presign_secret_key == values[PRESIGN_SECRET_REF]
    assert file_backend.calls == []
    assert vault_backend.calls == []


def test_missing_k8s_secret_does_not_fall_back_to_file_or_vault(tmp_path: Path) -> None:
    router, file_backend, vault_backend = _router(tmp_path)

    with pytest.raises(AppError) as caught:
        router.get(ADMIN_ACCESS_REF)

    assert caught.value.code == "SECRET_NOT_FOUND"
    assert file_backend.calls == []
    assert vault_backend.calls == []


@pytest.mark.parametrize(
    "secret_ref",
    [
        "k8s-secret://../access-key",
        "k8s-secret://agentscale-minio-plugin-admin/../secret-key",
        "k8s-secret://agentscale-minio-plugin-admin/..",
        "k8s-secret://agentscale-minio-plugin-admin/.",
        "k8s-secret:///etc/passwd",
        "k8s-secret://agentscale-minio-plugin-admin//etc/passwd",
        "k8s-secret://agentscale-minio-plugin-admin/access-key/extra",
        "k8s-secret://agentscale-minio-plugin-admin/%2e%2e",
        "k8s-secret://agentscale-minio-plugin-admin/access-key?raw=true",
        "k8s-secret://Agentscale-Minio-Plugin-Admin/access-key",
        "k8s-secret://agentscale-minio-plugin-admin/C:\\outside",
        "k8s-secret://agentscale-minio-plugin-admin\\../access-key",
        "file://ai_keys/legacy.secret",
    ],
)
def test_absolute_traversal_and_noncanonical_refs_are_denied(tmp_path: Path, secret_ref: str) -> None:
    store = K8sFileSecretStore(tmp_path)

    with pytest.raises(AppError) as caught:
        store.resolve(secret_ref)

    assert caught.value.code in {"SECRET_REF_INVALID", "SECRET_BACKEND_UNSUPPORTED"}


def test_symlink_outside_root_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "mounts"
    secret_dir = root / ADMIN_NAME
    secret_dir.mkdir(parents=True)
    outside = tmp_path / "outside-secret"
    outside.write_text("outside-value-sentinel", encoding="utf-8")
    try:
        (secret_dir / "access-key").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {type(exc).__name__}")

    with pytest.raises(AppError) as caught:
        K8sFileSecretStore(root).get(ADMIN_ACCESS_REF)

    assert caught.value.code == "SECRET_PATH_FORBIDDEN"
    assert "outside-value-sentinel" not in str(caught.value)


def test_kubernetes_atomic_writer_symlinks_inside_root_are_supported(tmp_path: Path) -> None:
    secret_dir = tmp_path / ADMIN_NAME
    data_dir = secret_dir / "..2026_11_09_00_00_00.000000000"
    data_dir.mkdir(parents=True)
    (data_dir / "access-key").write_text("plugin-object-admin\n", encoding="utf-8")
    try:
        (secret_dir / "..data").symlink_to(data_dir.name, target_is_directory=True)
        (secret_dir / "access-key").symlink_to("..data/access-key")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {type(exc).__name__}")

    assert K8sFileSecretStore(tmp_path).resolve(ADMIN_ACCESS_REF) == "plugin-object-admin"


@pytest.mark.parametrize("value", [b"", b" \n\t"])
def test_empty_values_are_denied(tmp_path: Path, value: bytes) -> None:
    _write_key(tmp_path, ADMIN_NAME, "access-key", value)

    with pytest.raises(AppError) as caught:
        K8sFileSecretStore(tmp_path).get(ADMIN_ACCESS_REF)

    assert caught.value.code == "SECRET_VALUE_EMPTY"


def test_oversize_value_is_bounded_and_denied(tmp_path: Path) -> None:
    _write_key(tmp_path, ADMIN_NAME, "access-key", b"123456789")
    store = K8sFileSecretStore(tmp_path, max_value_bytes=8)

    with pytest.raises(AppError) as caught:
        store.get(ADMIN_ACCESS_REF)

    assert caught.value.code == "SECRET_VALUE_TOO_LARGE"


def test_value_at_the_byte_limit_is_accepted(tmp_path: Path) -> None:
    _write_key(tmp_path, ADMIN_NAME, "access-key", b"12345678")

    assert K8sFileSecretStore(tmp_path, max_value_bytes=8).get(ADMIN_ACCESS_REF) == "12345678"


def test_read_errors_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    sentinel = "read-error-plaintext-sentinel"
    path = _write_key(tmp_path, ADMIN_NAME, "access-key", sentinel)
    original_open = Path.open

    def denied_open(self: Path, *args, **kwargs):
        if self == path:
            raise OSError(f"OS accidentally included {sentinel} and {self}")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_open)

    with pytest.raises(AppError) as caught:
        K8sFileSecretStore(tmp_path).get(ADMIN_ACCESS_REF)

    assert caught.value.code == "SECRET_READ_FAILED"
    for surface in (str(caught.value), repr(caught.value), caplog.text):
        assert sentinel not in surface
        assert str(path) not in surface


def test_invalid_utf8_has_a_sanitized_error(tmp_path: Path) -> None:
    _write_key(tmp_path, ADMIN_NAME, "access-key", b"\xff\xfe")

    with pytest.raises(AppError) as caught:
        K8sFileSecretStore(tmp_path).get(ADMIN_ACCESS_REF)

    assert caught.value.code == "SECRET_READ_FAILED"
    assert "codec" not in str(caught.value)


def test_mounted_backend_is_read_only_without_fallback(tmp_path: Path) -> None:
    router, file_backend, vault_backend = _router(tmp_path)

    with pytest.raises(AppError) as caught:
        router.delete(ADMIN_ACCESS_REF)

    assert caught.value.code == "SECRET_BACKEND_UNSUPPORTED"
    assert file_backend.calls == []
    assert vault_backend.calls == []


def test_existing_file_and_vault_paths_still_work(tmp_path: Path) -> None:
    legacy_file = FileSecretStore(tmp_path / "legacy-file")
    file_ref = legacy_file.put("aik_legacy", "legacy-file-value")
    router = RoutingSecretStore(
        file_store=legacy_file,
        vault_store=_VaultStub(),  # type: ignore[arg-type]
        k8s_file_store=K8sFileSecretStore(tmp_path / "mounted"),
    )

    assert router.get(file_ref) == "legacy-file-value"
    assert router.get("vault://ai_keys/legacy") == "legacy-vault-value"
