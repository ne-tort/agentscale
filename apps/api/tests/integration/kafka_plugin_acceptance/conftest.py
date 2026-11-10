"""Local fixtures only. Topic/group/schema/Redis helpers remain separate modules.

This conftest stays active only behind the mandatory early-plugin/confcutdir
isolation guard; it must not be disabled with --noconftest. It does not mutate
global repository state or monkeypatch production code. Endpoint absence skips
this directory locally; an
acceptance run with REQUIRED=1 fails on that skip instead of passing.
"""

from __future__ import annotations

import logging
import re
from typing import AsyncIterator

import pytest

from .acceptance_plugin import (
    pytest_configure as _configure_acceptance,
    pytest_sessionfinish as _finish_acceptance,
)
from .config import AcceptanceConfig
from .harness import KafkaPluginAcceptanceHarness

_SAFE_LABEL = re.compile(r"[^a-z0-9]+")


def pytest_configure(config: pytest.Config) -> None:
    _configure_acceptance(config)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    _finish_acceptance(session, exitstatus)


def skip_without_endpoint() -> None:
    if AcceptanceConfig.bootstrap_missing():
        pytest.skip(
            "real Kafka endpoint is absent; set KAFKA_PLUGIN_ACCEPTANCE_BOOTSTRAP_SERVERS for local runs",
            allow_module_level=True,
        )


@pytest.fixture
def case_label(request: pytest.FixtureRequest) -> str:
    label = _SAFE_LABEL.sub("-", request.node.name.lower()).strip("-")[:24]
    return label or "case"


@pytest.fixture
async def harness(
    request: pytest.FixtureRequest,
    case_label: str,
    caplog: pytest.LogCaptureFixture,
) -> AsyncIterator[KafkaPluginAcceptanceHarness]:
    caplog.set_level(logging.INFO)
    config = AcceptanceConfig.from_env()
    acceptance = await KafkaPluginAcceptanceHarness.open(config=config, label=case_label)
    async with acceptance:
        yield acceptance
    acceptance.assert_no_sensitive_logs(caplog.text)
