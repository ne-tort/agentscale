"""Local-only fixtures and collection gates; parent probes are excluded by confcutdir."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from .config import (
    AcceptanceConfig,
    acceptance_requested,
    acceptance_required,
)
from .acceptance_plugin import EARLY_GUARD_ATTRIBUTE
from .world import EntrypointWorld

PACKAGE_DIR = Path(__file__).resolve().parent
REQUIRED_CONFCUTDIR = "tests/integration/entrypoint_acceptance"
LOCAL_SKIP = (
    "local-only skip: complete ENTRYPOINT_ACCEPTANCE_* disposable environment is absent; "
    "a configured or REQUIRED=1 run must never skip"
)


def _assert_confcutdir(config: pytest.Config) -> None:
    actual = Path(str(getattr(config.option, "confcutdir", ""))).resolve()
    if actual != PACKAGE_DIR or not getattr(config, EARLY_GUARD_ATTRIBUTE, False):
        raise pytest.UsageError(
            "entrypoint acceptance requires -p tests.integration.entrypoint_acceptance.acceptance_plugin "
            "and --confcutdir=tests/integration/entrypoint_acceptance from apps/api. "
            "--noconftest and bare invocation are forbidden; parent probes must stay excluded."
        )


def pytest_configure(config: pytest.Config) -> None:
    _assert_confcutdir(config)
    config.addinivalue_line(
        "markers", "entrypoint_acceptance: real subprocess API/Gateway/Celery acceptance"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    _assert_confcutdir(config)
    for item in items:
        path = Path(str(item.fspath)).resolve()
        if PACKAGE_DIR not in path.parents:
            raise pytest.UsageError(f"outside item is forbidden in this isolated run: {item.nodeid}")
        if item.get_closest_marker("integration") is None:
            raise pytest.UsageError(f"integration marker missing: {item.nodeid}")
        if item.get_closest_marker("entrypoint_acceptance") is None:
            raise pytest.UsageError(f"entrypoint_acceptance marker missing: {item.nodeid}")
        for forbidden in ("skip", "skipif", "xfail"):
            if item.get_closest_marker(forbidden) is not None:
                raise pytest.UsageError(f"{forbidden} is forbidden: {item.nodeid}")


def pytest_collection_finish(session: pytest.Session) -> None:
    if acceptance_required():
        try:
            AcceptanceConfig.from_env()
        except Exception as exc:
            raise pytest.UsageError(
                "REQUIRED=1 entrypoint acceptance has an incomplete/unsafe environment: "
                f"{type(exc).__name__}; refusing a skipped run"
            ) from None


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not acceptance_requested():
        return
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    stats = getattr(reporter, "stats", {}) if reporter is not None else {}
    skipped = len(stats.get("skipped", ()))
    problems = []
    if int(getattr(session, "testscollected", 0) or 0) == 0:
        problems.append("collected=0")
    if skipped:
        problems.append(f"skipped={skipped}")
    if problems:
        session.exitstatus = 1
        if reporter is not None:
            reporter.write_sep("!", "invalid entrypoint acceptance run: " + ", ".join(problems))


@pytest.fixture(scope="session")
def entrypoint_config() -> AcceptanceConfig:
    if not acceptance_requested():
        pytest.skip(LOCAL_SKIP)
    return AcceptanceConfig.from_env()


@pytest_asyncio.fixture(scope="session")
async def world(
    entrypoint_config: AcceptanceConfig,
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[EntrypointWorld]:
    workspace = tmp_path_factory.mktemp("entrypoint-acceptance")
    value = await EntrypointWorld.open(config=entrypoint_config, workspace=workspace)
    try:
        yield value
    finally:
        try:
            await value.aclose()
        finally:
            value.processes.assert_all_stopped()


@pytest_asyncio.fixture(scope="session")
async def http_client(
    entrypoint_config: AcceptanceConfig,
    world: EntrypointWorld,
) -> AsyncIterator[httpx.AsyncClient]:
    limits = httpx.Limits(max_connections=16, max_keepalive_connections=8)
    async with httpx.AsyncClient(
        trust_env=False,
        timeout=httpx.Timeout(entrypoint_config.operation_timeout_seconds),
        limits=limits,
    ) as client:
        yield client
