from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.objects import ports
from prodavan.application.plugins.wiring.install import _zone_services
from prodavan.infrastructure.plugins.minio.adapters import MinioAuditAdapter
from tests.unit.plugins.minio.conftest import FakeAuditSink
from tests.unit.plugins.wiring.conftest import FakeCosign


@pytest.mark.asyncio
async def test_zone_object_audit_record_appends_sanitized_event_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    enabled_config: Any,
) -> None:
    monkeypatch.setattr(
        "prodavan.application.plugins.wiring.install._activation_mint",
        lambda _repos, _config: object(),
    )
    sink = FakeAuditSink()
    scale = object()
    runtime = SimpleNamespace(
        cosign=FakeCosign(),
        scale=scale,
        readiness=scale,
        state_store=object(),
        proxy=object(),
    )
    repos = SimpleNamespace(artifact_store=object(), audit=sink)
    overrides = {
        "object_buckets": object(),
        "tenant_db_secrets": object(),
        "tenant_db_admin": object(),
        "activation_mint": object(),
    }
    unwired: list[str] = []

    services = _zone_services(runtime, repos, enabled_config, overrides, unwired)
    object_store = services["object_store"]
    audit = object_store._audit
    assert isinstance(audit, MinioAuditAdapter)
    assert isinstance(audit, ports.AuditPort)

    await object_store._emit(
        company_id="co_123",
        plugin_id="mod_equipment",
        operation="grant",
        decision="allow",
        strict=True,
        reason="https://minio:9000/x?X-Amz-Signature=deadbeef",
    )

    assert unwired == []
    assert len(sink.rows) == 1
    row = sink.rows[0]
    assert row["event"] == "plugin_object_access"
    assert row["reason"].startswith("sha256:")
    assert row["reason_redacted"] is True
    assert row["redaction_version"] == "plugin-object-audit-v1"
    assert "deadbeef" not in str(row)
