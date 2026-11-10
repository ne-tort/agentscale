"""Explicit isolated environment for the real entrypoint acceptance harness.

No application DSN, endpoint, credential, selector or environment is inherited.
This module performs shape checks only; network IO belongs to the fixtures.
"""
from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from prodavan.domain.plugins.helpers import plugin_instance_id_for

PREFIX = "ENTRYPOINT_ACCEPTANCE_"
REQUIRED_ENV = PREFIX + "REQUIRED"
CONFIRM_ENV = PREFIX + "CONFIRM_ISOLATED_TEST_ENV"
ISOLATION_CONFIRMATION = "I_OWN_DISPOSABLE_ENTRYPOINT_RESOURCES"
CI_EVIDENCE_CONFIRMATION = "VERIFIED_BY_DISPOSABLE_TEST_CI"

POSTGRES_ADMIN_DSN = PREFIX + "POSTGRES_ADMIN_DSN"
POSTGRES_DSN = PREFIX + "POSTGRES_DSN"
REDIS_URL = PREFIX + "REDIS_URL"
REDIS_DB = PREFIX + "REDIS_DB"
MONGODB_URL = PREFIX + "MONGODB_URL"
MONGODB_DB = PREFIX + "MONGODB_DB"
KAFKA_BOOTSTRAP_SERVERS = PREFIX + "KAFKA_BOOTSTRAP_SERVERS"
KAFKA_SASL_USERNAME = PREFIX + "KAFKA_SASL_USERNAME"
KAFKA_SASL_PASSWORD = PREFIX + "KAFKA_SASL_PASSWORD"
KAFKA_TLS_CA_FILE = PREFIX + "KAFKA_TLS_CA_FILE"
KAFKA_SASL_MECHANISM = PREFIX + "KAFKA_SASL_MECHANISM"
MINIO_ENDPOINT = PREFIX + "MINIO_ENDPOINT"
MINIO_ROOT_ACCESS_KEY = PREFIX + "MINIO_ROOT_ACCESS_KEY"
MINIO_ROOT_SECRET_KEY = PREFIX + "MINIO_ROOT_SECRET_KEY"
MINIO_MC_BINARY = PREFIX + "MINIO_MC_BINARY"
MINIO_REGION = PREFIX + "MINIO_REGION"
TEST_BUNDLE_REF = PREFIX + "TEST_BUNDLE_REF"
TEST_BACKEND_REF = PREFIX + "TEST_BACKEND_REF"
TEST_PLUGIN_ID = PREFIX + "TEST_PLUGIN_ID"
TEST_VERSION = PREFIX + "TEST_VERSION"
TEST_SOURCE_ID = PREFIX + "TEST_SOURCE_ID"
TEST_SOURCE_REPOSITORY = PREFIX + "TEST_SOURCE_REPOSITORY"
TEST_COMMIT_SHA = PREFIX + "TEST_COMMIT_SHA"
TEST_RUN_URL = PREFIX + "TEST_RUN_URL"
TEST_SCAN_EVIDENCE = PREFIX + "TEST_SCAN_EVIDENCE"
TEST_CSP_EVIDENCE = PREFIX + "TEST_CSP_EVIDENCE"
COSIGN_IDENTITY = PREFIX + "COSIGN_IDENTITY"
COSIGN_ISSUER = PREFIX + "COSIGN_ISSUER"
COSIGN_BINARY = PREFIX + "COSIGN_BINARY"
COSIGN_KMS_KEY_REF = PREFIX + "COSIGN_KMS_KEY_REF"
COMPANY_ID = PREFIX + "COMPANY_ID"
FOREIGN_COMPANY_ID = PREFIX + "FOREIGN_COMPANY_ID"
PLUGIN_INSTANCE_ID = PREFIX + "PLUGIN_INSTANCE_ID"
FOREIGN_PLUGIN_INSTANCE_ID = PREFIX + "FOREIGN_PLUGIN_INSTANCE_ID"
GRANT_ID = PREFIX + "GRANT_ID"
FOREIGN_GRANT_ID = PREFIX + "FOREIGN_GRANT_ID"
PROJECT_ID = PREFIX + "PROJECT_ID"
PROVIDER_JOB_ID = PREFIX + "PROVIDER_JOB_ID"
CABINET_ID = PREFIX + "CABINET_ID"
K8S_NAMESPACE = PREFIX + "K8S_NAMESPACE"
K8S_STATE_CONFIGMAP = PREFIX + "K8S_STATE_CONFIGMAP"
K8S_SERVICE_HOST = PREFIX + "K8S_SERVICE_HOST"
K8S_SERVICE_PORT = PREFIX + "K8S_SERVICE_PORT"
INTERNAL_GATEWAY_URL_TEMPLATE = PREFIX + "INTERNAL_GATEWAY_URL_TEMPLATE"
STARTUP_TIMEOUT_SECONDS = PREFIX + "STARTUP_TIMEOUT_SECONDS"
OPERATION_TIMEOUT_SECONDS = PREFIX + "OPERATION_TIMEOUT_SECONDS"
SHUTDOWN_TIMEOUT_SECONDS = PREFIX + "SHUTDOWN_TIMEOUT_SECONDS"

_REQUIRED_VALUES = (
    POSTGRES_ADMIN_DSN, POSTGRES_DSN, REDIS_URL, REDIS_DB,
    MONGODB_URL, MONGODB_DB, KAFKA_BOOTSTRAP_SERVERS,
    KAFKA_SASL_USERNAME, KAFKA_SASL_PASSWORD, KAFKA_TLS_CA_FILE,
    MINIO_ENDPOINT, MINIO_ROOT_ACCESS_KEY, MINIO_ROOT_SECRET_KEY, MINIO_MC_BINARY,
    TEST_BUNDLE_REF, TEST_BACKEND_REF, TEST_PLUGIN_ID, TEST_VERSION,
    TEST_SOURCE_ID, TEST_SOURCE_REPOSITORY, TEST_COMMIT_SHA, TEST_RUN_URL,
    TEST_SCAN_EVIDENCE, TEST_CSP_EVIDENCE, COSIGN_IDENTITY, COSIGN_ISSUER,
    COMPANY_ID, FOREIGN_COMPANY_ID, PLUGIN_INSTANCE_ID,
    FOREIGN_PLUGIN_INSTANCE_ID, GRANT_ID, FOREIGN_GRANT_ID, PROJECT_ID, PROVIDER_JOB_ID,
    CABINET_ID, K8S_NAMESPACE, K8S_STATE_CONFIGMAP, K8S_SERVICE_HOST,
    INTERNAL_GATEWAY_URL_TEMPLATE, CONFIRM_ENV,
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,40}$")
_PLUGIN_ID = re.compile(r"^acceptance_[a-z0-9_]{1,45}$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


class AcceptanceConfigurationError(RuntimeError):
    """A configured acceptance run is incomplete or unsafe; never a skip."""


def acceptance_required(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(REQUIRED_ENV, "").strip() == "1"


def acceptance_requested(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return acceptance_required(source) or any(
        name.startswith(PREFIX) and name != REQUIRED_ENV and value.strip()
        for name, value in source.items()
    )


def _text(source: Mapping[str, str], name: str, *, default: str = "") -> str:
    value = source.get(name, default).strip()
    if not value:
        raise AcceptanceConfigurationError(f"{name} is required; no ambient fallback is allowed")
    return value


def _float(source: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise AcceptanceConfigurationError(f"{name} must be numeric") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise AcceptanceConfigurationError(f"{name} must be within {low}..{high}")
    return value


def _safe_id(value: str, name: str) -> str:
    if _SAFE_ID.fullmatch(value) is None:
        raise AcceptanceConfigurationError(f"{name} has an invalid identifier")
    return value


def _origin(value: str, name: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise AcceptanceConfigurationError(f"{name} is invalid") from None
    if (
        parsed.scheme not in {"http", "https"} or not parsed.netloc
        or parsed.username is not None or parsed.password is not None
        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
    ):
        raise AcceptanceConfigurationError(f"{name} must be an HTTP(S) origin without credentials/path")
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    postgres_admin_dsn: str = field(repr=False)
    postgres_dsn: str = field(repr=False)
    redis_url: str = field(repr=False)
    redis_db: int
    mongodb_url: str = field(repr=False)
    mongodb_db: str
    kafka_bootstrap_servers: str
    kafka_sasl_username: str = field(repr=False)
    kafka_sasl_password: str = field(repr=False)
    kafka_tls_ca_file: str
    kafka_sasl_mechanism: str
    minio_endpoint: str
    minio_root_access_key: str = field(repr=False)
    minio_root_secret_key: str = field(repr=False)
    minio_mc_binary: str
    minio_region: str
    bundle_ref: str
    backend_ref: str
    plugin_id: str
    version: str
    source_id: str
    source_repository: str
    commit_sha: str
    run_url: str
    cosign_identity: str
    cosign_issuer: str
    cosign_binary: str
    cosign_kms_key_ref: str | None
    company_id: str
    foreign_company_id: str
    plugin_instance_id: str
    foreign_plugin_instance_id: str
    grant_id: str
    foreign_grant_id: str
    project_id: str
    provider_job_id: str
    cabinet_id: str
    k8s_namespace: str
    k8s_state_configmap: str
    k8s_service_host: str
    k8s_service_port: str
    internal_gateway_url_template: str
    startup_timeout_seconds: float
    operation_timeout_seconds: float
    shutdown_timeout_seconds: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AcceptanceConfig":
        source = os.environ if env is None else env
        missing = [name for name in _REQUIRED_VALUES if not source.get(name, "").strip()]
        if missing:
            raise AcceptanceConfigurationError(
                "configured entrypoint acceptance is incomplete: " + ", ".join(missing)
            )
        if source.get(CONFIRM_ENV) != ISOLATION_CONFIRMATION:
            raise AcceptanceConfigurationError(
                f"{CONFIRM_ENV} must equal {ISOLATION_CONFIRMATION}; refusing shared/live resources"
            )
        for name in (TEST_SCAN_EVIDENCE, TEST_CSP_EVIDENCE):
            if source.get(name) != CI_EVIDENCE_CONFIRMATION:
                raise AcceptanceConfigurationError(
                    f"{name} must carry the explicit disposable-CI evidence marker"
                )
        plugin_id = _text(source, TEST_PLUGIN_ID)
        if _PLUGIN_ID.fullmatch(plugin_id) is None or plugin_id == "hello_plugin":
            raise AcceptanceConfigurationError(
                "TEST_PLUGIN_ID must be a dedicated acceptance_* artifact, never a production selector"
            )
        version = _text(source, TEST_VERSION)
        if _SEMVER.fullmatch(version) is None:
            raise AcceptanceConfigurationError("TEST_VERSION must be SemVer")
        commit = _text(source, TEST_COMMIT_SHA)
        if _COMMIT.fullmatch(commit) is None:
            raise AcceptanceConfigurationError("TEST_COMMIT_SHA must be a 40-character lowercase Git SHA")
        raw_db = _text(source, REDIS_DB)
        try:
            redis_db = int(raw_db, 10)
        except ValueError:
            raise AcceptanceConfigurationError("REDIS_DB must be a non-negative integer") from None
        if redis_db < 0:
            raise AcceptanceConfigurationError("REDIS_DB must be a non-negative integer")
        redis_url = _text(source, REDIS_URL)
        parsed_redis = urlsplit(redis_url)
        if parsed_redis.scheme not in {"redis", "rediss"} or not parsed_redis.netloc:
            raise AcceptanceConfigurationError("REDIS_URL must use redis:// or rediss://")
        if parsed_redis.path not in {"", "/", f"/{redis_db}"} or parsed_redis.query or parsed_redis.fragment:
            raise AcceptanceConfigurationError("REDIS_URL database must match REDIS_DB and have no query/fragment")
        ca_file = _text(source, KAFKA_TLS_CA_FILE)
        if not Path(ca_file).is_file():
            raise AcceptanceConfigurationError("KAFKA_TLS_CA_FILE must name an existing disposable-cluster CA")
        mechanism = source.get(KAFKA_SASL_MECHANISM, "SCRAM-SHA-512").strip()
        if mechanism not in {"SCRAM-SHA-256", "SCRAM-SHA-512"}:
            raise AcceptanceConfigurationError("KAFKA_SASL_MECHANISM is unsupported")
        for name in (COMPANY_ID, FOREIGN_COMPANY_ID, PLUGIN_INSTANCE_ID,
                     FOREIGN_PLUGIN_INSTANCE_ID, GRANT_ID, FOREIGN_GRANT_ID,
                     PROJECT_ID, PROVIDER_JOB_ID, CABINET_ID):
            _safe_id(_text(source, name), name)
        for name in (COMPANY_ID, FOREIGN_COMPANY_ID):
            if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", _text(source, name)) is None:
                raise AcceptanceConfigurationError(f"{name} must be canonical lowercase tenant id")
        if len({_text(source, COMPANY_ID), _text(source, FOREIGN_COMPANY_ID)}) != 2:
            raise AcceptanceConfigurationError("tenant ids must differ")
        if len({_text(source, PLUGIN_INSTANCE_ID), _text(source, FOREIGN_PLUGIN_INSTANCE_ID)}) != 2:
            raise AcceptanceConfigurationError("instance ids must differ")
        if len({_text(source, GRANT_ID), _text(source, FOREIGN_GRANT_ID)}) != 2:
            raise AcceptanceConfigurationError("grant ids must differ")
        expected_instance = plugin_instance_id_for(
            _text(source, COMPANY_ID), plugin_id
        )
        if _text(source, PLUGIN_INSTANCE_ID) != expected_instance:
            raise AcceptanceConfigurationError(
                "PLUGIN_INSTANCE_ID must equal plugin_instance_id_for(COMPANY_ID, TEST_PLUGIN_ID) "
                "for the source-backed installation scope flow"
            )
        for name in (K8S_NAMESPACE, K8S_STATE_CONFIGMAP):
            if _DNS_LABEL.fullmatch(_text(source, name)) is None:
                raise AcceptanceConfigurationError(f"{name} must be a DNS label")
        service_port = source.get(K8S_SERVICE_PORT, "443").strip()
        if not service_port.isdigit() or not 1 <= int(service_port) <= 65535:
            raise AcceptanceConfigurationError("K8S_SERVICE_PORT must be 1..65535")
        template = _text(source, INTERNAL_GATEWAY_URL_TEMPLATE)
        if template.count("{port}") != 1:
            raise AcceptanceConfigurationError("INTERNAL_GATEWAY_URL_TEMPLATE must contain exactly one {port}")
        try:
            _origin(template.format(port=18080), INTERNAL_GATEWAY_URL_TEMPLATE)
        except (ValueError, KeyError):
            raise AcceptanceConfigurationError("INTERNAL_GATEWAY_URL_TEMPLATE is invalid") from None
        sa_dir = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        if not (sa_dir / "token").is_file() or not (sa_dir / "ca.crt").is_file():
            raise AcceptanceConfigurationError(
                "full acceptance must run inside the disposable Kubernetes environment "
                "with the scoped ServiceAccount token and CA mounted"
            )
        return cls(
            postgres_admin_dsn=_text(source, POSTGRES_ADMIN_DSN),
            postgres_dsn=_text(source, POSTGRES_DSN),
            redis_url=redis_url,
            redis_db=redis_db,
            mongodb_url=_text(source, MONGODB_URL),
            mongodb_db=_text(source, MONGODB_DB),
            kafka_bootstrap_servers=_text(source, KAFKA_BOOTSTRAP_SERVERS),
            kafka_sasl_username=_text(source, KAFKA_SASL_USERNAME),
            kafka_sasl_password=_text(source, KAFKA_SASL_PASSWORD),
            kafka_tls_ca_file=ca_file,
            kafka_sasl_mechanism=mechanism,
            minio_endpoint=_origin(_text(source, MINIO_ENDPOINT), MINIO_ENDPOINT),
            minio_root_access_key=_text(source, MINIO_ROOT_ACCESS_KEY),
            minio_root_secret_key=_text(source, MINIO_ROOT_SECRET_KEY),
            minio_mc_binary=_text(source, MINIO_MC_BINARY),
            minio_region=source.get(MINIO_REGION, "us-east-1").strip(),
            bundle_ref=_text(source, TEST_BUNDLE_REF),
            backend_ref=_text(source, TEST_BACKEND_REF),
            plugin_id=plugin_id,
            version=version,
            source_id=_text(source, TEST_SOURCE_ID),
            source_repository=_text(source, TEST_SOURCE_REPOSITORY),
            commit_sha=commit,
            run_url=_text(source, TEST_RUN_URL),
            cosign_identity=_text(source, COSIGN_IDENTITY),
            cosign_issuer=_text(source, COSIGN_ISSUER),
            cosign_binary=source.get(COSIGN_BINARY, "cosign").strip(),
            cosign_kms_key_ref=source.get(COSIGN_KMS_KEY_REF, "").strip() or None,
            company_id=_text(source, COMPANY_ID),
            foreign_company_id=_text(source, FOREIGN_COMPANY_ID),
            plugin_instance_id=_text(source, PLUGIN_INSTANCE_ID),
            foreign_plugin_instance_id=_text(source, FOREIGN_PLUGIN_INSTANCE_ID),
            grant_id=_text(source, GRANT_ID),
            foreign_grant_id=_text(source, FOREIGN_GRANT_ID),
            project_id=_text(source, PROJECT_ID),
            provider_job_id=_text(source, PROVIDER_JOB_ID),
            cabinet_id=_text(source, CABINET_ID),
            k8s_namespace=_text(source, K8S_NAMESPACE),
            k8s_state_configmap=_text(source, K8S_STATE_CONFIGMAP),
            k8s_service_host=_text(source, K8S_SERVICE_HOST),
            k8s_service_port=service_port,
            internal_gateway_url_template=template,
            startup_timeout_seconds=_float(source, STARTUP_TIMEOUT_SECONDS, 120.0, 5.0, 600.0),
            operation_timeout_seconds=_float(source, OPERATION_TIMEOUT_SECONDS, 45.0, 2.0, 180.0),
            shutdown_timeout_seconds=_float(source, SHUTDOWN_TIMEOUT_SECONDS, 20.0, 2.0, 120.0),
        )

    @property
    def redis_dsn(self) -> str:
        parsed = urlsplit(self.redis_url)
        return urlunsplit((parsed.scheme, parsed.netloc, f"/{self.redis_db}", "", ""))

    def internal_gateway_url(self, port: int) -> str:
        if not 1 <= port <= 65535:
            raise ValueError("invalid ephemeral gateway port")
        return self.internal_gateway_url_template.format(port=port)
