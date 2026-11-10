"""Pytest wiring for the opt-in real-K8s plugin acceptance harness.

Isolation guarantees:
* without PLUGIN_ACCEPTANCE_KUBECONFIG every test skips with an explicit reason;
* with it, the run FAILS if zero acceptance tests were collected or any of them
  skipped (no silent green acceptance);
* when this directory is collected by the umbrella suite, the two autouse
  fixtures below override the umbrella ones so no Postgres wipe/seed and no
  global auth/Redis mutation ever happens for this K8s-only harness;
* the documented invocation (-c this directory's pytest.ini plus the required
  --confcutdir=tests/e2e/k8s/plugin_acceptance) keeps only this package's
  conftest loaded, so parent probe conftests are never imported; a static
  pytest_configure guard fails fast if they somehow were.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest

from .acceptance_config import (
    ENV_KUBECONFIG,
    AcceptanceConfig,
    acceptance_requested,
)
from .cluster import AcceptanceCluster, cleanup_expired_namespaces
from .lifecycle import AcceptanceIds

_STATS = {"collected": 0, "skipped": 0, "nodeids": set()}

SKIP_REASON = (
    "real-K8s plugin acceptance is opt-in: set PLUGIN_ACCEPTANCE_KUBECONFIG to an explicit "
    "disposable non-prod kubeconfig and PLUGIN_ACCEPTANCE_NAMESPACE_PREFIX; see README.md"
)


@pytest.fixture(autouse=True)
def clean_engine_cache() -> Iterator[None]:
    """Override the umbrella DB fixture: this harness never wipes/seeds Postgres."""
    yield


@pytest.fixture(autouse=True)
def _auth_command_bus_isolation() -> Iterator[None]:
    """Override the umbrella auth/Redis fixture: no global settings mutation."""
    yield


@pytest.fixture(scope="session")
def acceptance_config() -> AcceptanceConfig | None:
    if not acceptance_requested():
        return None
    return AcceptanceConfig.from_env()


@pytest.fixture(scope="session")
def _ttl_janitor(acceptance_config: AcceptanceConfig | None) -> None:
    if acceptance_config is None or not acceptance_config.ttl_janitor:
        return
    asyncio.run(cleanup_expired_namespaces(acceptance_config))


@pytest.fixture(scope="module")
def cluster(
    request: pytest.FixtureRequest,
    acceptance_config: AcceptanceConfig | None,
    _ttl_janitor: None,
) -> Iterator[AcceptanceCluster]:
    if acceptance_config is None:
        pytest.skip(SKIP_REASON)
    tag = getattr(request.module, "CLUSTER_TAG", "misc")
    live = asyncio.run(AcceptanceCluster.create(acceptance_config, tag))
    try:
        yield live
    finally:
        asyncio.run(live.destroy())


@pytest.fixture()
def make_ids(cluster: AcceptanceCluster):
    def _make(label: str) -> AcceptanceIds:
        return AcceptanceIds.new(cluster.config.run_id, label)

    return _make


#: Parent conftests that run PostgreSQL/live-API/kubectl probes AT IMPORT time.
#: They must stay unloaded for an acceptance run; --noconftest is NOT an answer
#: because it would also disable this package's fixtures and execution gates.
_PARENT_PROBE_CONFTESTS = ("/tests/conftest.py", "/tests/e2e/conftest.py")


def _loaded_parent_probe_conftests(config: pytest.Config) -> list[str]:
    loaded: list[str] = []
    for plugin in config.pluginmanager.get_plugins():
        path = getattr(plugin, "__file__", None)
        if not isinstance(path, str):
            continue
        normalized = path.replace("\\", "/")
        if any(normalized.endswith(suffix) for suffix in _PARENT_PROBE_CONFTESTS):
            loaded.append(normalized)
    return loaded


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "plugin_acceptance: opt-in real-K8s acceptance on a disposable non-prod cluster"
    )
    # Static isolation guard: with a kubeconfig configured, acceptance must run
    # with --confcutdir=tests/e2e/k8s/plugin_acceptance so parent probe conftests
    # are never imported, while this package's own conftest stays active.
    if acceptance_requested():
        loaded = _loaded_parent_probe_conftests(config)
        if loaded:
            raise pytest.UsageError(
                "plugin acceptance requires an isolated invocation: "
                "python -m pytest -c tests/e2e/k8s/plugin_acceptance/pytest.ini "
                "--confcutdir=tests/e2e/k8s/plugin_acceptance tests/e2e/k8s/plugin_acceptance "
                "(parent probe conftests were loaded: " + ", ".join(sorted(loaded)) + ")"
            )


def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list) -> None:
    for item in items:
        path = str(getattr(item, "fspath", "")).replace("\\", "/")
        if "/plugin_acceptance/" not in path:
            continue
        item.add_marker("plugin_acceptance")
        _STATS["collected"] += 1
        _STATS["nodeids"].add(item.nodeid)


def pytest_collection_finish(session: pytest.Session) -> None:
    """Collection-scope guard: with a kubeconfig, collect ONLY this package.

    -c alone does not scope collection (and testpaths is shlex-split and used
    only when no path argument is given), so the explicit package path in the
    documented command is what keeps collection inside plugin_acceptance. Any
    collected item outside it aborts the run before a single test executes.
    """
    if not acceptance_requested():
        return
    escaped: list[str] = []
    for item in session.items:
        path = str(getattr(item, "fspath", "")).replace("\\", "/")
        if "/plugin_acceptance/" not in path:
            escaped.append(item.nodeid)
    if escaped:
        raise pytest.UsageError(
            "plugin acceptance collection escaped the harness package; use the explicit path: "
            "python -m pytest -c tests/e2e/k8s/plugin_acceptance/pytest.ini "
            "--confcutdir=tests/e2e/k8s/plugin_acceptance tests/e2e/k8s/plugin_acceptance "
            f"(foreign items: {len(escaped)}, first: {escaped[0]})"
        )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.nodeid in _STATS["nodeids"] and report.skipped:
        _STATS["skipped"] += 1


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Acceptance with a kubeconfig must be collected>0 and skipped==0."""
    if not os.environ.get(ENV_KUBECONFIG, "").strip():
        return
    # With a kubeconfig configured and this conftest active, acceptance must be
    # real: zero collected acceptance tests or any skip fails the whole run.
    if _STATS["collected"] == 0 or _STATS["skipped"]:
        session.exitstatus = 1
        print(
            "PLUGIN ACCEPTANCE CONTRACT VIOLATION: with "
            f"{ENV_KUBECONFIG} set, collected={_STATS['collected']} and "
            f"skipped={_STATS['skipped']} (required: collected>0, skipped=0)"
        )
