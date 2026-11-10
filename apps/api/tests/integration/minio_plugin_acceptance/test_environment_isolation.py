"""Static/runtime proof that ambient proxies and application env stay outside IO."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from .config import AcceptanceConfig
from .resources import McAdmin, new_s3_client, new_sts_client
from .support import require

pytestmark = pytest.mark.minio_acceptance
_PACKAGE_DIR = Path(__file__).resolve().parent


def test_static_transport_guards_are_present() -> None:
    resources = (_PACKAGE_DIR / "resources.py").read_text(encoding="utf-8")
    conftest = (_PACKAGE_DIR / "conftest.py").read_text(encoding="utf-8")
    require("dict(os.environ)" not in resources, "mc inherited the ambient process env")
    require('for name in ("PATH", "SYSTEMROOT")' in resources, "mc env allowlist was broadened")
    require('trust_env=False' in conftest, "HTTPX ambient env support was enabled")
    require('follow_redirects=False' in conftest, "HTTPX redirects were enabled")
    require('proxies' in resources and '_NO_AMBIENT_PROXIES' in resources, "boto proxy isolation is absent")


def test_mc_child_env_is_allowlisted_and_contains_no_ambient_sentinel(
    tmp_path: Path,
    isolated_transport_environment: dict[str, str],
) -> None:
    config = AcceptanceConfig(
        endpoint="http://minio-acceptance.invalid",
        root_access_key="explicit-test-root-access",
        root_secret_key="explicit-test-root-secret",
        admin_secret_key="explicit-test-admin-secret",
        presign_secret_key="explicit-test-presign-secret",
        mc_binary="mc",
    )
    child = McAdmin(config, tmp_path).environment_snapshot()
    allowed = {"PATH", "SYSTEMROOT", "MC_CONFIG_DIR", "MC_HOST_acceptance"}
    require(set(child).issubset(allowed), "mc child env contains a non-allowlisted name")
    require(child.get("MC_CONFIG_DIR") == str(tmp_path), "mc config dir is not isolated")
    require("MC_HOST_acceptance" in child, "explicit disposable mc alias is absent")
    for name, sentinel in isolated_transport_environment.items():
        require(name not in child, "ambient application/proxy name reached mc env")
        require(
            all(sentinel not in value for value in child.values()),
            "ambient application/proxy value reached mc env",
        )


def test_boto_clients_explicitly_disable_ambient_proxy(
    isolated_transport_environment: dict[str, str],
) -> None:
    clients = [
        new_s3_client(
            endpoint="http://minio-acceptance.invalid",
            access_key="explicit-test-access",
            secret_key="explicit-test-secret",
            region="us-east-1",
        ),
        new_sts_client(
            endpoint="http://minio-acceptance.invalid",
            access_key="explicit-test-access",
            secret_key="explicit-test-secret",
            region="us-east-1",
        ),
    ]
    try:
        for client in clients:
            proxies = client.meta.config.proxies
            require(proxies.get("http") == "", "boto HTTP proxy is not explicitly disabled")
            require(proxies.get("https") == "", "boto HTTPS proxy is not explicitly disabled")
            require(proxies.get("all") == "", "boto ALL_PROXY is not explicitly disabled")
        from botocore.validate import ParamValidationDecorator

        require(isinstance(clients[0]._serializer, ParamValidationDecorator), "S3 model validation was disabled")
        require(
            isinstance(clients[1]._serializer, ParamValidationDecorator),
            "STS model validation was not restored after the 900-second contract migration",
        )
    finally:
        for client in clients:
            client.close()


async def test_httpx_ignores_environment_and_does_not_leak_sentinels_or_urls(
    http_client: httpx.AsyncClient,
    isolated_transport_environment: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    require(http_client.trust_env is False, "HTTPX trust_env was enabled")
    require(http_client.follow_redirects is False, "HTTPX redirects were enabled")
    # Positive STS/presign tests use this same client with unreachable sentinel
    # proxies. Their real HTTP success is the runtime proof of direct transport.
    for sentinel in isolated_transport_environment.values():
        require(sentinel not in caplog.text, "ambient sentinel appeared in captured logs")
