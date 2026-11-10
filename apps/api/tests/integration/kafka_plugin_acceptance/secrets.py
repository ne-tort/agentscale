"""Test-only file SecretStore and sensitive-value assertions.

The store resolves exactly file://<safe-relative-path> under an explicitly
provided root. It never caches key material and never includes values in an
exception message.
"""

from __future__ import annotations

from pathlib import Path

from .config import AcceptanceConfig


class TestOnlyFileSecretStore:
    """Resolve refs from the operator-provisioned isolated secret root."""

    def __init__(self, root: str) -> None:
        resolved = Path(root).expanduser().resolve()
        if not resolved.is_dir():
            raise RuntimeError("acceptance secret root does not exist")
        self._root = resolved

    def get(self, secret_ref: str) -> str:
        path = self._path_for(secret_ref)
        try:
            value = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError("acceptance secret reference cannot be resolved") from exc
        value = value.strip()
        if not value:
            raise RuntimeError("acceptance secret is empty")
        return value

    def path_for(self, secret_ref: str) -> Path:
        return self._path_for(secret_ref)

    def _path_for(self, secret_ref: str) -> Path:
        if not isinstance(secret_ref, str) or not secret_ref.startswith("file://"):
            raise RuntimeError("only explicit file:// SecretStore references are accepted")
        relative = secret_ref[len("file://"):].strip()
        if not relative or "\\" in relative or relative.startswith("/"):
            raise RuntimeError("secret reference must be a safe relative path")
        parts = relative.split("/")
        if any(not part or part in {".", ".."} for part in parts):
            raise RuntimeError("secret reference path traversal is forbidden")
        try:
            path = (self._root / Path(*parts)).resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("acceptance secret reference cannot be resolved") from exc
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise RuntimeError("secret reference resolved outside the acceptance root") from exc
        if not path.is_file():
            raise RuntimeError("secret reference is not a regular file")
        return path


class SensitiveValues:
    """Collect only in-process values that must never appear in logs.

    This object is never printed; failures report a generic boolean reason.
    """

    def __init__(self) -> None:
        self._values: set[str] = set()

    def add(self, value: str | None) -> None:
        if value and value.strip():
            self._values.add(value.strip())

    def add_config(self, config: AcceptanceConfig, store: TestOnlyFileSecretStore) -> None:
        self.add(config.database_dsn)
        self.add(config.redis_dsn)
        self.add(config.event_secret_ref)
        self.add(config.claims_secret_ref)
        if config.sasl_username_ref:
            self.add(config.sasl_username_ref)
            self.add(config.sasl_password_ref)
        self.add(store.get(config.event_secret_ref))
        self.add(store.get(config.claims_secret_ref))
        if config.sasl_username_ref and config.sasl_password_ref:
            self.add(store.get(config.sasl_username_ref))
            self.add(store.get(config.sasl_password_ref))

    def contains(self, text: str) -> bool:
        return any(value in text for value in self._values)

def assert_no_sensitive_text(text: str, sensitive: SensitiveValues) -> None:
    if sensitive.contains(text):
        raise AssertionError("sensitive acceptance material leaked into captured output")
