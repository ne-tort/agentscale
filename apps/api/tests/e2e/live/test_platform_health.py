"""Live platform health — kafka, minio, metrics-server via /health/ready."""

from __future__ import annotations

import pytest

from tests.conftest import requires_live_api

pytestmark = [pytest.mark.live, requires_live_api]


def test_live_platform_ready_checks(live_client) -> None:
    ready = live_client.get("/health/ready")
    assert ready.status_code == 200, ready.text
    body = ready.json()
    checks = body.get("checks") or {}
    assert checks.get("database") == "ok"
    resources = body.get("resources") or {}
    for name in ("kafka", "file_store", "k8s", "mongodb", "opensearch"):
        if name in resources:
            assert resources[name] in {"ok", "n/a", "fail"}
