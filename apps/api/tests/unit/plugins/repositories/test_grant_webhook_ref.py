"""Opaque webhook reference persistence tests (not run during freeze)."""

from __future__ import annotations

from dataclasses import replace

from prodavan.infrastructure.persistence.models.plugin_platform import CapabilityGrantRow
from prodavan.infrastructure.persistence.repositories.plugins.grants import (
    SqlCapabilityGrantRepository,
)

from .conftest import NOW, FakeSession, grant_record, grant_row


def test_grant_row_round_trip_keeps_only_opaque_webhook_ref() -> None:
    repository = SqlCapabilityGrantRepository(session=FakeSession(), now=lambda: NOW)
    ref = "file://ai_keys/plugins/webhooks/co_123/mod_equipment/pinst_01/WEBHOOK_SECRET.secret"
    plaintext = "unit-test-webhook-value-never-persisted"
    candidate = replace(grant_record(), webhook_secret_ref=ref)

    values = repository._row_values(candidate, CapabilityGrantRow)
    restored = repository.to_record(grant_row(webhook_secret_ref=ref))

    assert values["webhook_secret_ref"] == ref
    assert restored.webhook_secret_ref == ref
    assert plaintext not in repr(values)
    assert plaintext not in repr(restored)
