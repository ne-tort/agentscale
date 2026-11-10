"""Read-only resolution of Kubernetes Secret volume files.

Only ``k8s-secret://<secret-name>/<key>`` references are accepted. Kubernetes'
atomic-writer symlinks are supported when their resolved target stays inside
the configured mount root. No reference, path, value, or OS error is logged or
included in an exception.
"""

from __future__ import annotations

import re
from pathlib import Path

from prodavan.domain.errors import AppError

REF_PREFIX = "k8s-secret://"
DEFAULT_ROOT = Path("/var/run/secrets")
DEFAULT_MAX_VALUE_BYTES = 64 * 1024

_MAX_REF_LENGTH = len(REF_PREFIX) + 253 + 1 + 253
_REF_SHAPE = re.compile(r"k8s-secret://([^/]+)/([^/]+)")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")
_SECRET_KEY = re.compile(r"[A-Za-z0-9._-]+")


def _failure(code: str, detail: str) -> AppError:
    return AppError(
        code=code,
        title="Kubernetes secret resolution failed",
        status=500,
        detail=detail,
    )


def _read_only() -> AppError:
    return _failure(
        "SECRET_BACKEND_UNSUPPORTED",
        "k8s-secret references are read-only",
    )


class K8sFileSecretStore:
    """Resolve mounted Secret keys beneath one fixed, configured root."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        max_value_bytes: int = DEFAULT_MAX_VALUE_BYTES,
    ) -> None:
        if (
            isinstance(max_value_bytes, bool)
            or not isinstance(max_value_bytes, int)
            or not 1 <= max_value_bytes <= 1024 * 1024
        ):
            raise ValueError("max_value_bytes must be between 1 and 1048576")
        self._root = DEFAULT_ROOT if root is None else Path(root)
        self._max_value_bytes = max_value_bytes

    @staticmethod
    def _parse_ref(secret_ref: str) -> tuple[str, str]:
        if not isinstance(secret_ref, str) or not secret_ref.startswith(REF_PREFIX):
            raise _failure(
                "SECRET_BACKEND_UNSUPPORTED",
                "expected a k8s-secret reference",
            )
        match = _REF_SHAPE.fullmatch(secret_ref) if len(secret_ref) <= _MAX_REF_LENGTH else None
        if match is None:
            raise _failure("SECRET_REF_INVALID", "invalid k8s-secret reference")
        name, key = match.groups()
        labels = name.split(".")
        if (
            len(name) > 253
            or any(_DNS_LABEL.fullmatch(label) is None for label in labels)
            or len(key) > 253
            or key in {".", ".."}
            or _SECRET_KEY.fullmatch(key) is None
        ):
            raise _failure("SECRET_REF_INVALID", "invalid k8s-secret reference")
        return name, key

    def _resolved_path(self, name: str, key: str) -> Path:
        try:
            root = self._root.resolve(strict=True)
        except FileNotFoundError:
            raise _failure("SECRET_NOT_FOUND", "secret mount root is missing") from None
        except (OSError, RuntimeError, ValueError):
            raise _failure("SECRET_READ_FAILED", "secret mount root is unavailable") from None

        try:
            resolved = (root / name / key).resolve(strict=True)
        except FileNotFoundError:
            raise _failure("SECRET_NOT_FOUND", "mounted secret key is missing") from None
        except (OSError, RuntimeError, ValueError):
            raise _failure("SECRET_READ_FAILED", "mounted secret key is unavailable") from None

        if resolved == root or not resolved.is_relative_to(root):
            raise _failure("SECRET_PATH_FORBIDDEN", "mounted secret key escapes its root")
        try:
            if not resolved.is_file():
                raise _failure("SECRET_NOT_FOUND", "mounted secret key is not a file")
        except OSError:
            raise _failure("SECRET_READ_FAILED", "mounted secret key is unavailable") from None
        return resolved

    def resolve(self, secret_ref: str) -> str:
        """Return one bounded, non-empty UTF-8 value without exposing read errors."""

        name, key = self._parse_ref(secret_ref)
        path = self._resolved_path(name, key)
        try:
            with path.open("rb") as stream:
                raw = stream.read(self._max_value_bytes + 1)
        except FileNotFoundError:
            raise _failure("SECRET_NOT_FOUND", "mounted secret key is missing") from None
        except OSError:
            raise _failure("SECRET_READ_FAILED", "mounted secret key could not be read") from None
        if len(raw) > self._max_value_bytes:
            raise _failure("SECRET_VALUE_TOO_LARGE", "mounted secret value exceeds its byte limit")
        try:
            value = raw.decode("utf-8").strip()
        except UnicodeError:
            raise _failure("SECRET_READ_FAILED", "mounted secret value is not valid UTF-8") from None
        if not value:
            raise _failure("SECRET_VALUE_EMPTY", "mounted secret value is empty")
        return value

    def get(self, secret_ref: str) -> str:
        return self.resolve(secret_ref)

    def put(self, key_id: str, secret: str) -> str:
        raise _read_only()

    def delete(self, secret_ref: str) -> None:
        raise _read_only()


__all__ = [
    "DEFAULT_MAX_VALUE_BYTES",
    "DEFAULT_ROOT",
    "K8sFileSecretStore",
    "REF_PREFIX",
]
