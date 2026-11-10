"""Explicit, fail-closed acceptance environment.

No production setting, DSN or secret has a fallback. Reading this module only
validates shape; secret material stays in the externally provisioned files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

BOOTSTRAP = "KAFKA_PLUGIN_ACCEPTANCE_BOOTSTRAP_SERVERS"
DATABASE_DSN = "KAFKA_PLUGIN_ACCEPTANCE_DATABASE_DSN"
REDIS_DSN = "KAFKA_PLUGIN_ACCEPTANCE_REDIS_DSN"
SECRET_ROOT = "KAFKA_PLUGIN_ACCEPTANCE_SECRET_ROOT"
EVENT_SECRET_REF = "KAFKA_PLUGIN_ACCEPTANCE_EVENT_SIGNING_SECRET_REF"
CLAIMS_SECRET_REF = "KAFKA_PLUGIN_ACCEPTANCE_CLAIMS_SIGNING_SECRET_REF"
ISOLATED_CONFIRM = "KAFKA_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV"
SECURITY_PROTOCOL = "KAFKA_PLUGIN_ACCEPTANCE_SECURITY_PROTOCOL"
SASL_USERNAME_REF = "KAFKA_PLUGIN_ACCEPTANCE_SASL_USERNAME_REF"
SASL_PASSWORD_REF = "KAFKA_PLUGIN_ACCEPTANCE_SASL_PASSWORD_REF"
TLS_CA_FILE = "KAFKA_PLUGIN_ACCEPTANCE_TLS_CA_FILE"
SASL_MECHANISM = "KAFKA_PLUGIN_ACCEPTANCE_SASL_MECHANISM"
ALLOW_PLAINTEXT = "KAFKA_PLUGIN_ACCEPTANCE_ALLOW_LOCAL_PLAINTEXT"
OPERATION_TIMEOUT = "KAFKA_PLUGIN_ACCEPTANCE_OPERATION_TIMEOUT_SECONDS"
REBALANCE_TIMEOUT = "KAFKA_PLUGIN_ACCEPTANCE_REBALANCE_TIMEOUT_SECONDS"
REQUIRED = "KAFKA_PLUGIN_ACCEPTANCE_REQUIRED"
REQUIRE_COMPLETE = "KAFKA_PLUGIN_ACCEPTANCE_REQUIRE_COMPLETE"

_ISOLATED_PHRASE = "I_OWN_ISOLATED_TEST_RESOURCES"
_PLAINTEXT_PHRASE = "LOCAL_TEST_ONLY_NO_SHARED_ENVIRONMENT"
_SECRET_REF_PREFIX = "file://"


class AcceptanceConfigurationError(RuntimeError):
    """A required acceptance-only input is missing or unsafe."""


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    bootstrap_servers: str
    database_dsn: str = field(repr=False)
    redis_dsn: str = field(repr=False)
    secret_root: str
    event_secret_ref: str = field(repr=False)
    claims_secret_ref: str = field(repr=False)
    isolated_confirmed: bool
    security_protocol: str
    tls_ca_file: str | None
    sasl_mechanism: str
    allow_local_plaintext: bool
    operation_timeout_seconds: float
    rebalance_timeout_seconds: float
    sasl_username_ref: str | None = field(default=None, repr=False)
    sasl_password_ref: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "AcceptanceConfig":
        source = os.environ if env is None else env
        bootstrap = _text(source, BOOTSTRAP, required=True)
        dsn = _text(source, DATABASE_DSN, required=True)
        redis = _text(source, REDIS_DSN, required=True)
        root = _text(source, SECRET_ROOT, required=True)
        event_ref = _text(source, EVENT_SECRET_REF, required=True)
        claims_ref = _text(source, CLAIMS_SECRET_REF, required=True)
        if not event_ref.startswith(_SECRET_REF_PREFIX) or not claims_ref.startswith(_SECRET_REF_PREFIX):
            raise AcceptanceConfigurationError(
                "event and claims signing secrets must use file:// SecretStore references"
            )
        if event_ref == claims_ref:
            raise AcceptanceConfigurationError("event and claims signing key references must differ")
        if source.get(ISOLATED_CONFIRM, "") != _ISOLATED_PHRASE:
            raise AcceptanceConfigurationError(
                f"{ISOLATED_CONFIRM} must exactly confirm an isolated test environment"
            )

        protocol = _text(source, SECURITY_PROTOCOL, default="SASL_SSL")
        if protocol not in {"SASL_SSL", "PLAINTEXT"}:
            raise AcceptanceConfigurationError("security protocol must be SASL_SSL or PLAINTEXT")
        username_ref = _text(source, SASL_USERNAME_REF, default="") or None
        password_ref = _text(source, SASL_PASSWORD_REF, default="") or None
        ca_file = _text(source, TLS_CA_FILE, default="") or None
        mechanism = _text(source, SASL_MECHANISM, default="SCRAM-SHA-512")
        plaintext_allowed = source.get(ALLOW_PLAINTEXT, "") == _PLAINTEXT_PHRASE
        if protocol == "SASL_SSL":
            if not username_ref or not password_ref or not ca_file:
                raise AcceptanceConfigurationError(
                    "SASL_SSL requires username/password SecretStore references and a CA file"
                )
            if username_ref == event_ref or password_ref == event_ref or username_ref == claims_ref or password_ref == claims_ref:
                raise AcceptanceConfigurationError("Kafka credential refs must differ from signing refs")
        else:
            if not plaintext_allowed:
                raise AcceptanceConfigurationError(
                    f"PLAINTEXT requires {ALLOW_PLAINTEXT}={_PLAINTEXT_PHRASE}"
                )
            if username_ref or password_ref or ca_file:
                raise AcceptanceConfigurationError("PLAINTEXT must not carry SASL/TLS credential inputs")

        try:
            parts = urlsplit(dsn)
            if parts.scheme != "postgresql+asyncpg" or not parts.path.lstrip("/") or parts.username is None:
                raise AcceptanceConfigurationError("database DSN must be a postgresql+asyncpg URL with credentials")
            rparts = urlsplit(redis)
            if rparts.scheme not in {"redis", "rediss"} or not rparts.netloc:
                raise AcceptanceConfigurationError("Redis DSN must use redis:// or rediss://")
            if protocol == "SASL_SSL" and rparts.scheme != "rediss" and not plaintext_allowed:
                raise AcceptanceConfigurationError("Redis must use rediss:// unless local plaintext is explicitly allowed")
        except AcceptanceConfigurationError:
            raise
        except Exception as exc:
            raise AcceptanceConfigurationError("database/Redis DSN validation failed") from exc

        return cls(
            bootstrap_servers=bootstrap,
            database_dsn=dsn,
            redis_dsn=redis,
            secret_root=root,
            event_secret_ref=event_ref,
            claims_secret_ref=claims_ref,
            isolated_confirmed=True,
            security_protocol=protocol,
            sasl_username_ref=username_ref,
            sasl_password_ref=password_ref,
            tls_ca_file=ca_file,
            sasl_mechanism=mechanism,
            allow_local_plaintext=plaintext_allowed,
            operation_timeout_seconds=_bounded_float(source, OPERATION_TIMEOUT, default=20.0, minimum=2.0, maximum=60.0),
            rebalance_timeout_seconds=_bounded_float(source, REBALANCE_TIMEOUT, default=30.0, minimum=5.0, maximum=90.0),
        )

    @staticmethod
    def bootstrap_missing(env: dict[str, str] | None = None) -> bool:
        source = os.environ if env is None else env
        return not source.get(BOOTSTRAP, "").strip()


def acceptance_required(env: dict[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(REQUIRED, "") == "1"


def require_complete(env: dict[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(REQUIRE_COMPLETE, "") == "1"


def _text(source: dict[str, str], name: str, *, required: bool = False, default: str = "") -> str:
    value = source.get(name, default).strip()
    if required and not value:
        raise AcceptanceConfigurationError(f"{name} is required; there is no endpoint or environment fallback")
    return value


def _bounded_float(source: dict[str, str], name: str, *, default: float, minimum: float, maximum: float) -> float:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise AcceptanceConfigurationError(f"{name} must be numeric") from exc
    if not minimum <= value <= maximum or value != value:
        raise AcceptanceConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value
