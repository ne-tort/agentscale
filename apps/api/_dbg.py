import asyncio

from prodavan.application.migration.m04e_ports import SqlM04EEvidenceStore
from tests.migration.m04e_ports.test_m04e_evidence_store import (
    accepted,
    checkpoint,
    context,
    gates,
    version_row,
    version_two_row,
)
from tests.migration.m04e_ports.acceptance_fixtures import definition_row
from prodavan.application.migration.m04e_ports import version_candidate
from tests.migration.m04e_ports.sqlite_ledger import CHECKPOINT_TABLE, LedgerMirror


async def main() -> None:
    ledger = await LedgerMirror().create()
    store = SqlM04EEvidenceStore(
        session_factory=ledger.session_factory, allow_non_postgresql=True,
    )
    first = accepted(version_candidate(context(), version_row(), definition_row()))
    batch = await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(first,), page_exhausted=True,
    )
    print("first", batch.checkpoint)
    print("head", await ledger.single(CHECKPOINT_TABLE))
    stale = await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(accepted(version_candidate(context(), version_two_row(), definition_row())),),
        page_exhausted=True,
    )
    print("stale records", [(r.source_id, r.outcome) for r in stale.records])
    print("stale checkpoint", stale.checkpoint)
    await ledger.close()


asyncio.run(main())
