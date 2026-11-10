"""Fixtures and no-skip gate for the explicit real-MinIO acceptance package."""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
import pytest_asyncio

from prodavan.application.plugins.capabilities.adapters.objects import MinioObjectsAdapter
from prodavan.infrastructure.plugins.minio.adapters import (
    MinioBucketAdmin,
    MinioPresigner,
    MinioSts,
)

from .config import (
    AcceptanceConfig,
    AcceptanceConfigurationError,
    REQUIRED_INPUT_ENVS,
    acceptance_requested,
    acceptance_required,
    endpoint_configured,
)
from .resources import (
    MinioAcceptanceEnvironment,
    MinioProvisioningError,
    ResourceProvisioner,
    new_minio_sts,
)
from .support import MemorySecretStore


@dataclass(slots=True)
class ObjectStack:
    environment: MinioAcceptanceEnvironment
    buckets: MinioBucketAdmin = field(repr=False)
    sts: MinioSts = field(repr=False)
    presigner: MinioPresigner = field(repr=False)
    objects: MinioObjectsAdapter = field(repr=False)
    secret_store: MemorySecretStore = field(repr=False)


_PACKAGE_DIR = Path(__file__).resolve().parent
_ISOLATION_OPTION = "--confcutdir=tests/integration/minio_plugin_acceptance"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "minio_acceptance: requires an explicit disposable real MinIO")
    config.addinivalue_line("markers", "slow: controlled wall-clock wait for actual signature expiry")
    if acceptance_requested():
        confcutdir = getattr(config.option, "confcutdir", None)
        if confcutdir is None or Path(confcutdir).resolve() != _PACKAGE_DIR:
            raise pytest.UsageError(
                "isolated MinIO acceptance requires "
                f"{_ISOLATION_OPTION}; parent tests/conftest.py has import-time "
                "PostgreSQL/live API/kubectl side effects; --noconftest is forbidden"
            )


def pytest_collection_finish(session: pytest.Session) -> None:
    if acceptance_required():
        missing = [name for name in REQUIRED_INPUT_ENVS if not os.environ.get(name, "").strip()]
        if missing:
            raise pytest.UsageError(
                "required MinIO acceptance cannot be skipped; missing: " + ", ".join(missing)
            )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """An endpoint-selected acceptance run is invalid with zero tests or any skip."""
    if not acceptance_requested():
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = len(reporter.stats.get("skipped", ())) if reporter is not None else 0
    problems: list[str] = []
    if session.testscollected == 0:
        problems.append("collected=0")
    if skipped:
        problems.append(f"skipped={skipped}")
    if problems:
        session.exitstatus = 1
        if reporter is not None:
            reporter.write_sep("!", "invalid MinIO acceptance run: " + ", ".join(problems))


@pytest.fixture(autouse=True)
def suppress_transport_logs() -> Iterator[None]:
    """Prevent third-party HTTP/SigV4 debug logging, then restore process state."""
    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore", "boto3", "botocore", "urllib3")]
    previous = [(logger, logger.disabled) for logger in loggers]
    for logger in loggers:
        logger.disabled = True
    try:
        yield
    finally:
        for logger, disabled in previous:
            logger.disabled = disabled


@pytest.fixture
def isolated_transport_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Force ambient sentinels; success proves clients ignore them, not the network."""
    sentinels = {
        "HTTP_PROXY": "http://http-proxy.sentinel.invalid",
        "HTTPS_PROXY": "http://https-proxy.sentinel.invalid",
        "ALL_PROXY": "http://all-proxy.sentinel.invalid",
        "NO_PROXY": "no-proxy.sentinel.invalid",
        "PLUGIN_MINIO_ENDPOINT": "http://ambient-minio.sentinel.invalid",
        "AWS_ACCESS_KEY_ID": "ambient-aws-access-sentinel",
        "AWS_SECRET_ACCESS_KEY": "ambient-aws-secret-sentinel",
        "AWS_SESSION_TOKEN": "ambient-aws-token-sentinel",
        "MINIO_ROOT_PASSWORD": "ambient-minio-secret-sentinel",
        "PRODAVAN_APPLICATION_SECRET": "ambient-application-secret-sentinel",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    return sentinels


@pytest.fixture(scope="session")
def minio_environment() -> Iterator[MinioAcceptanceEnvironment]:
    if not endpoint_configured():
        if acceptance_required():
            pytest.fail("required MinIO acceptance has no explicit endpoint", pytrace=False)
        pytest.skip("local-only skip: explicit disposable MinIO endpoint is not configured")
    try:
        config = AcceptanceConfig.from_env()
    except AcceptanceConfigurationError as exc:
        pytest.fail(str(exc), pytrace=False)

    with TemporaryDirectory(prefix="minio-plugin-acceptance-") as directory:
        provisioner = ResourceProvisioner(config, Path(directory))
        try:
            environment = provisioner.provision()
        except MinioProvisioningError as exc:
            pytest.fail(str(exc), pytrace=False)
        try:
            yield environment
        finally:
            try:
                provisioner.cleanup()
            finally:
                provisioner.close()


@pytest_asyncio.fixture
async def object_stack(
    minio_environment: MinioAcceptanceEnvironment,
    isolated_transport_environment: dict[str, str],
) -> AsyncIterator[ObjectStack]:
    config = minio_environment.plugin_config
    buckets: MinioBucketAdmin | None = None
    presigner: MinioPresigner | None = None
    sts: MinioSts | None = None
    objects: MinioObjectsAdapter | None = None
    try:
        buckets = MinioBucketAdmin(
            config=config,
            client=minio_environment.new_s3_client(
                access_key=config.admin_access_key,
                secret_key=config.admin_secret_key,
            ),
        )
        presigner = MinioPresigner(
            config=config,
            client=minio_environment.new_s3_client(
                access_key=config.presign_access_key,
                secret_key=config.presign_secret_key,
            ),
            expected_prefix=minio_environment.identity.prefix,
        )
        sts = new_minio_sts(minio_environment, config)
        secret_store = MemorySecretStore()
        objects = MinioObjectsAdapter(
            buckets=buckets,
            sts=sts,
            presigner=presigner,
            secret_store=secret_store,
            timeout_seconds=20,
        )
        yield ObjectStack(
            environment=minio_environment,
            buckets=buckets,
            sts=sts,
            presigner=presigner,
            objects=objects,
            secret_store=secret_store,
        )
    finally:
        close_failed = False
        for resource in (objects, sts, presigner, buckets):
            if resource is None:
                continue
            try:
                await resource.aclose()
            except Exception:
                close_failed = True
        if close_failed:
            raise RuntimeError("one or more owned MinIO adapters failed to close")


@pytest_asyncio.fixture
async def http_client(
    isolated_transport_environment: dict[str, str],
) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=10.0),
        trust_env=False,
        follow_redirects=False,
    ) as client:
        yield client
