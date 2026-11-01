"""MIG-EQ-01 acceptance fixtures; intentionally unexecuted during verification freeze."""

from datetime import UTC, datetime

from prodavan.application.migration.equipment_ingestion.ingestion import (
    FreshnessState,
    IngestionRunStatus,
    SourceIdentity,
    SupplierIdentity,
)

NOW = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
COMPLETE_SOURCE = SourceIdentity("source-a", "supplier-feed", "v1", "owner-a", "tenant-a")
INCOMPLETE_SOURCE = SourceIdentity(None, "supplier-feed", "v1", None, None)
VERIFIED_SUPPLIER = SupplierIdentity("supplier-a", "feed-vendor-7", "Vendor", True)
UNVERIFIED_SUPPLIER = SupplierIdentity(None, "feed-vendor-7", "Vendor", False)

CASES = (
    "missing canonical tenant/owner/source identity blocks ingestion before artifact/snapshot append",
    "missing verified supplier identity blocks ingestion; brand/display name is never supplier identity",
    "same run id with changed source or supplier identity conflicts and cannot resume another stream",
    "resume starts from persisted cursor; checkpoint CAS conflict prevents advancement or duplicate acceptance",
    "raw artifact digest, byte length and immutable reference bind every normalized snapshot/offer",
    "artifact ID replay with changed bytes is immutable conflict, never overwrite",
    "normalized offer source/supplier mismatch is rejected and cannot enter a positive snapshot",
    "snapshot carries tenant, owner, source, supplier, run, artifact, schema, observed time and digest",
    "source outage preserves last-good snapshot as stale; absent last-good yields unknown, never empty-success",
    "page budget produces resumable partial run; terminal source cursor produces succeeded run",
    "projection is rebuildable only from accepted immutable snapshots and versioned deterministic contract",
    "projection rebuild does not mutate source runs, snapshots, raw artifacts, or offer provenance",
    "projection must be invalidated/rebuilt after accepted snapshot change; stale generation cannot publish",
    "raw payload never appears in logs, promotion record or redacted audit evidence",
    "unknown source fields and unsupported normalization semantics quarantine/block rather than guessed mappings",
    "all fixtures remain unexecuted until coordinator lifts migration verification freeze",
)

EXPECTED_FAILURE_STATUS = IngestionRunStatus.BLOCKED
EXPECTED_STALE_STATE = FreshnessState.STALE
EXPECTED_NO_HISTORY_STATE = FreshnessState.UNKNOWN
