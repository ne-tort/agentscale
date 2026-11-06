"""Contract tests for the isolated equipment ingestion foundation.

No database, filesystem, network, or legacy equipment module is imported here.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from prodavan.application.equipment_ingestion import (
    IngestionBatch,
    IngestionRun,
    IngestionRunState,
    NormalizedOfferSnapshot,
    RawArtifact,
    SourceConfig,
    SourceKind,
)


NOW = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)


def test_source_config_keeps_secret_resolution_out_of_contract():
    source = SourceConfig(
        id="s4b-1",
        tenant_id="tenant-1",
        kind=SourceKind.S4B_WEB,
        display_name="S4B catalog",
        endpoint="https://supplier.example/catalog",
        credential_ref="secret:s4b-1",
        options={"locale": "ru-RU"},
    )
    assert source.credential_ref == "secret:s4b-1"
    assert source.options["locale"] == "ru-RU"

    with pytest.raises(ValidationError, match="requires mailbox"):
        SourceConfig(
            id="imap-1",
            tenant_id="tenant-1",
            kind=SourceKind.IMAP,
            display_name="Supplier mailbox",
        )


def test_run_requires_terminal_timestamp_and_failure_reason():
    with pytest.raises(ValidationError, match="terminal run"):
        IngestionRun(id="run-1", tenant_id="tenant-1", source_id="s4b-1", started_at=NOW, state=IngestionRunState.SUCCEEDED)

    with pytest.raises(ValidationError, match="error_message"):
        IngestionRun(
            id="run-1",
            tenant_id="tenant-1",
            source_id="s4b-1",
            started_at=NOW,
            finished_at=NOW,
            state=IngestionRunState.FAILED,
        )

    with pytest.raises(ValidationError, match="finished_at"):
        IngestionRun(
            id="run-1",
            tenant_id="tenant-1",
            source_id="s4b-1",
            started_at=NOW,
            finished_at=NOW - timedelta(seconds=1),
            state=IngestionRunState.FAILED,
            error_message="timeout",
        )


def test_batch_enforces_artifact_provenance_and_snapshot_count():
    artifact = RawArtifact(
        id="artifact-1",
        run_id="run-1",
        source_id="imap-1",
        captured_at=NOW,
        media_type="message/rfc822",
        byte_size=42,
        sha256="a" * 64,
        external_id="<message-1@example.test>",
    )
    offer = NormalizedOfferSnapshot(
        id="offer-1",
        run_id="run-1",
        artifact_id=artifact.id,
        source_id="imap-1",
        external_key="supplier-sku-1",
        supplier_name="Supplier LLC",
        title="Rack server",
        unit_price=Decimal("125000.00"),
        currency="RUB",
        quantity=Decimal("2"),
        unit="pcs",
        raw_locator="email://message-1/attachment-1/row-4",
    )
    run = IngestionRun(
        id="run-1",
        tenant_id="tenant-1",
        source_id="imap-1",
        started_at=NOW,
        finished_at=NOW,
        state=IngestionRunState.SUCCEEDED,
        artifact_ids=(artifact.id,),
        snapshot_count=1,
    )
    batch = IngestionBatch(run=run, artifacts=(artifact,), offers=(offer,))
    assert batch.offers[0].unit_price == Decimal("125000.00")

    with pytest.raises(ValidationError, match="artifact_id"):
        IngestionBatch(run=run, artifacts=(artifact,), offers=(offer.model_copy(update={"artifact_id": "missing"}),))

    with pytest.raises(ValidationError, match="unit_price and currency"):
        NormalizedOfferSnapshot(
            id="offer-2",
            run_id="run-1",
            artifact_id=artifact.id,
            source_id="imap-1",
            external_key="supplier-sku-2",
            supplier_name="Supplier LLC",
            title="Rack server",
            unit_price=Decimal("1"),
        )


def test_offer_rejects_naive_timestamp_and_invalid_hash():
    with pytest.raises(ValidationError, match="timezone-aware"):
        RawArtifact(
            id="artifact-1",
            run_id="run-1",
            source_id="s4b-1",
            captured_at=datetime(2026, 1, 15, 10, 30),
            media_type="text/csv",
            byte_size=1,
            sha256="b" * 64,
        )

    with pytest.raises(ValidationError, match="sha256"):
        RawArtifact(
            id="artifact-1",
            run_id="run-1",
            source_id="s4b-1",
            captured_at=NOW,
            media_type="text/csv",
            byte_size=1,
            sha256="not-a-sha",
        )
