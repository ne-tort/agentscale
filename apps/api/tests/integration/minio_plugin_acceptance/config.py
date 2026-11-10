"""Explicit environment contract for the disposable real-MinIO acceptance run."""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

ENDPOINT_ENV = "MINIO_PLUGIN_ACCEPTANCE_ENDPOINT"
ROOT_ACCESS_KEY_ENV = "MINIO_PLUGIN_ACCEPTANCE_ROOT_ACCESS_KEY"
ROOT_SECRET_KEY_ENV = "MINIO_PLUGIN_ACCEPTANCE_ROOT_SECRET_KEY"
ADMIN_SECRET_KEY_ENV = "MINIO_PLUGIN_ACCEPTANCE_ADMIN_SECRET_KEY"
PRESIGN_SECRET_KEY_ENV = "MINIO_PLUGIN_ACCEPTANCE_PRESIGN_SECRET_KEY"
MC_BINARY_ENV = "MINIO_PLUGIN_ACCEPTANCE_MC_BINARY"
CONFIRM_ENV = "MINIO_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV"
REGION_ENV = "MINIO_PLUGIN_ACCEPTANCE_REGION"
REQUIRED_ENV = "MINIO_PLUGIN_ACCEPTANCE_REQUIRED"

ISOLATION_CONFIRMATION = "I_OWN_DISPOSABLE_MINIO_RESOURCES"
REQUIRED_INPUT_ENVS = (
    ENDPOINT_ENV,
    ROOT_ACCESS_KEY_ENV,
    ROOT_SECRET_KEY_ENV,
    ADMIN_SECRET_KEY_ENV,
    PRESIGN_SECRET_KEY_ENV,
    MC_BINARY_ENV,
    CONFIRM_ENV,
)
_REGION_RE = re.compile(r"^[a-z0-9-]{2,32}$")


class AcceptanceConfigurationError(RuntimeError):
    """A required acceptance input is absent or unsafe; values stay private."""


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    endpoint: str
    root_access_key: str = field(repr=False)
    root_secret_key: str = field(repr=False)
    admin_secret_key: str = field(repr=False)
    presign_secret_key: str = field(repr=False)
    mc_binary: str
    region: str = "us-east-1"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AcceptanceConfig":
        source = os.environ if env is None else env
        missing = [name for name in REQUIRED_INPUT_ENVS if not source.get(name, "").strip()]
        if missing:
            raise AcceptanceConfigurationError(
                "missing required acceptance environment: " + ", ".join(missing)
            )
        if source.get(CONFIRM_ENV) != ISOLATION_CONFIRMATION:
            raise AcceptanceConfigurationError(
                f"{CONFIRM_ENV} must exactly confirm disposable resource ownership"
            )

        endpoint = source[ENDPOINT_ENV].strip().rstrip("/")
        try:
            parsed = urlsplit(endpoint)
        except ValueError:
            raise AcceptanceConfigurationError("acceptance endpoint is invalid") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(character.isspace() for character in endpoint)
        ):
            raise AcceptanceConfigurationError(
                "acceptance endpoint must be an HTTP(S) origin without credentials/path/query"
            )

        root_access_key = source[ROOT_ACCESS_KEY_ENV].strip()
        secret_keys = tuple(
            source[name].strip()
            for name in (ROOT_SECRET_KEY_ENV, ADMIN_SECRET_KEY_ENV, PRESIGN_SECRET_KEY_ENV)
        )
        if (
            len(root_access_key) < 3
            or any(len(value) < 8 for value in secret_keys)
            or len(set(secret_keys)) != len(secret_keys)
            or root_access_key in secret_keys
            or any(character.isspace() or character == "\x00" for character in root_access_key)
            or any(
                character.isspace() or character == "\x00"
                for value in secret_keys
                for character in value
            )
        ):
            raise AcceptanceConfigurationError("disposable root/test credentials are invalid")

        region = source.get(REGION_ENV, "us-east-1").strip()
        if _REGION_RE.fullmatch(region) is None:
            raise AcceptanceConfigurationError(f"{REGION_ENV} is invalid")
        return cls(
            endpoint=endpoint,
            root_access_key=root_access_key,
            root_secret_key=source[ROOT_SECRET_KEY_ENV].strip(),
            admin_secret_key=source[ADMIN_SECRET_KEY_ENV].strip(),
            presign_secret_key=source[PRESIGN_SECRET_KEY_ENV].strip(),
            mc_binary=source[MC_BINARY_ENV].strip(),
            region=region,
        )


def endpoint_configured(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return bool(source.get(ENDPOINT_ENV, "").strip())


def acceptance_required(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(REQUIRED_ENV, "").strip() == "1"


def acceptance_requested(env: Mapping[str, str] | None = None) -> bool:
    return endpoint_configured(env) or acceptance_required(env)
