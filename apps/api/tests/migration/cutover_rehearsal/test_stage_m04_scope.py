"""Stage 1 rehearsal: M04 scope backfill writes real evidence into a live database.

What the stage is supposed to do, and what this module proves
============================================================
``ScopeMigrationCoordinator`` + ``SqlAlchemyScopeBackfillAdapter`` turn one untrusted
legacy scope row into canonical, reviewable evidence: an anchor set, a
``scope_registry`` row, a first mapping, a quarantine/audit trail and a CAS-advanced
checkpoint -- all inside one trusted transaction whose RLS context was applied and
proven.  Every claim below is checked against the rows the database actually holds,
not against the object the port returned.

Two results are *just as important* as the accepted one:

* **the partition must refuse to start without a usable trusted context**
  (``missing_trusted_transaction_context_or_port``) rather than map anything;
* **the tenant's cabinet must prove an active ``cabinet_company_grants`` row** --
  the seed deliberately withholds it in one test, and the stage must quarantine
  ``cabinet_membership_missing`` instead of accepting a cabinet the tenant does not
  hold.

The cross-tenant assertion is the third: a source row whose payload claims another
tenant is quarantined with ``cross_tenant_source_assertion``, *and* nothing is
written to ``scope_registry``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from prodavan.application.migration.m04_scope.scope_migration import (
    BackfillOutcome,
    MigrationEvidenceContext,
)
from prodavan.application.scope_registry.contracts import RlsContext

from .harness import (
    RehearsalHarness,
    initial_scope_checkpoint,
    new_seed,
    scope_candidate,
    scope_stream,
    seed_partition,
    sha256_digest,
)


async def _next_transaction_id(harness: RehearsalHarness) -> str:
    """A *real* database transaction id, read from an open transaction.

    M04 compares the application transaction id with the one the binder read inside
    the same transaction, so a fabricated value can never pass; this reads it the way
    a caller would (``txid_current()`` inside the unit of work).
    """

    async with harness.trusted.connect() as conn:
        async with conn.begin():
            return str(await conn.scalar(text("SELECT txid_current()::text")))


async def test_m04_accepts_a_cabinet_scope_and_writes_real_evidence(
    rehearsal: RehearsalHarness,
) -> None:
    seed = await seed_partition(rehearsal)
    context = rehearsal.evidence_context(seed, transaction_id=await _next_transaction_id(rehearsal))
    candidate = scope_candidate(seed)

    resume = await rehearsal.coordinator.resume_checkpoint(
        stream=scope_stream(seed), context=context
    )
    assert resume.reason_code == "checkpoint_initialized"
    assert resume.checkpoint == initial_scope_checkpoint(seed)

    result = await rehearsal.coordinator.process_row(
        candidate=candidate, checkpoint=resume.checkpoint, context=context
    )

    assert result.outcome is BackfillOutcome.ACCEPTED, (
        f"the M04 stage refused a valid cabinet scope: {result.reason_code}"
    )
    assert result.reason_code is None
    assert result.canonical_positive_writes is False, (
        "M04 scope evidence must never enable canonical positive writes"
    )

    # -- the canonical rows exist, with the exact source identity -----------------
    registry = (
        await rehearsal.rows(
            "SELECT id, tenant_id, scope_kind, scope_id, cabinet_id, owner_state, "
            "       source_kind, source_id, schema_version, payload_digest, checkpoint "
            "FROM scope_registry WHERE tenant_id = :tenant AND scope_kind = 'cabinet'",
            {"tenant": seed.tenant_id},
        )
    )
    assert len(registry) == 1, f"expected exactly one active cabinet row, got {registry}"
    row = registry[0]
    assert str(row[5]) == "active"
    assert str(row[2]) == "cabinet"
    assert str(row[3]) == seed.cabinet_id
    assert str(row[4]) == seed.cabinet_id
    assert (str(row[6]), str(row[7]), str(row[8])) == (
        candidate.source_kind,
        candidate.source_id,
        candidate.schema_version,
    )
    assert str(row[9]) == candidate.source_digest, (
        "the registry row must carry the source digest the mapping is keyed by"
    )
    assert str(row[10]).strip(), "the registry row must record the checkpoint token"

    # -- the anchors the policy chain reads were written --------------------------
    for table, column, value in (
        ("scope_owner_tenant_anchors", "tenant_id", seed.tenant_id),
        ("scope_owner_cabinet_anchors", "cabinet_id", seed.cabinet_id),
    ):
        anchors = await rehearsal.rows(
            f"SELECT owner_state, source_kind, source_id, payload_digest FROM {table} "
            f"WHERE {column} = :value",
            {"value": value},
        )
        assert len(anchors) == 1, f"{table}: expected exactly one anchor, got {anchors}"
        assert str(anchors[0][0]) == "active"
        assert str(anchors[0][1]) == candidate.source_kind

    # -- the mapping is the durable record of *this* source row -------------------
    mapping = (
        await rehearsal.rows(
            "SELECT scope_registry_id, mapping_state, source_payload_digest, "
            "       canonical_payload_digest, checkpoint FROM scope_owner_mappings "
            "WHERE source_kind = :kind AND source_id = :sid AND schema_version = :schema",
            {
                "kind": candidate.source_kind,
                "sid": candidate.source_id,
                "schema": candidate.schema_version,
            },
        )
    )
    assert len(mapping) == 1
    assert str(mapping[0][0]) == str(row[0]), "the mapping must point at the registry row"
    assert str(mapping[0][1]) == "mapped"
    assert str(mapping[0][2]) == candidate.source_digest
    canonical_evidence = str(mapping[0][3])
    assert canonical_evidence.startswith("v1|scope-registry.v1|sha256:"), (
        "the first mapping must preserve the canonical schema lineage it was written "
        f"under, got {canonical_evidence!r}"
    )
    assert result.canonical_digest is not None
    assert canonical_evidence.endswith(result.canonical_digest)

    # -- audit evidence names the operation and the correlation ------------------
    audit = (
        await rehearsal.rows(
            "SELECT event_kind, reason_code, correlation_id, checkpoint FROM scope_owner_audit "
            "WHERE source_kind = :kind AND source_id = :sid",
            {"kind": candidate.source_kind, "sid": candidate.source_id},
        )
    )
    assert len(audit) >= 1
    assert any(str(entry[0]) == "scope_scope_accepted" for entry in audit)
    assert all(str(entry[2]) == context.correlation_id for entry in audit)

    # -- the returned evidence is bound to this very transaction -----------------
    assert result.parent_evidence is not None
    assert result.parent_evidence.transaction_id == context.transaction_id
    assert result.parent_evidence.valid_parent_for(candidate.anchors)
    assert result.parent_evidence.cabinet_membership_kind == "cabinet_company_grant"
    assert result.rls_evidence is not None
    assert result.rls_evidence.transaction_id == context.transaction_id
    assert result.rls_evidence.database_role == "prodavan_service"
    assert result.rls_evidence.role_not_bypass_rls is True
    assert result.rls_evidence.typed_parent_policy_enforced is True, (
        "the chained tenant policy of scope_owner_mappings must be measured as enforced; "
        "a False here means the manifest never matched a real predicate"
    )
    assert result.actor_membership_evidence is not None
    assert result.actor_membership_evidence.relationship_kind == "employee_membership"
    assert result.actor_membership_evidence.active is True

    # -- the checkpoint advanced exactly one cursor position --------------------
    assert result.checkpoint.sequence == 1
    assert result.checkpoint.after_source_id == candidate.source_id
    durable = await rehearsal.rows(
        "SELECT checkpoint FROM scope_owner_mappings "
        "WHERE source_kind = :kind AND source_id = :sid",
        {"kind": candidate.source_kind, "sid": candidate.source_id},
    )
    assert durable[0][0] is not None


async def test_m04_refuses_without_a_usable_trusted_context(
    rehearsal: RehearsalHarness,
) -> None:
    """The stage must not map anything when the server-derived context is missing."""

    seed = await seed_partition(rehearsal)
    unusable = MigrationEvidenceContext(
        tenant_id=seed.tenant_id,
        authenticated=False,
        transaction_id=None,
        correlation_id=None,
        user_id=None,
        role=None,
        rls_context=None,
        writer_generation=None,
    )
    assert unusable.usable is False

    resume = await rehearsal.coordinator.resume_checkpoint(
        stream=scope_stream(seed), context=unusable
    )
    assert resume.reason_code == "missing_trusted_transaction_context_or_port"
    assert resume.checkpoint is None

    result = await rehearsal.coordinator.process_row(
        candidate=scope_candidate(seed), checkpoint=initial_scope_checkpoint(seed), context=unusable
    )
    assert result.outcome is BackfillOutcome.BLOCKED
    assert result.reason_code == "missing_trusted_transaction_context_or_port"
    assert result.checkpoint == initial_scope_checkpoint(seed), (
        "a blocked row must not advance the cursor"
    )
    assert await rehearsal.count("scope_registry") == 0
    assert await rehearsal.count("scope_owner_mappings") == 0
    assert await rehearsal.count("scope_owner_audit") == 0


async def test_m04_quarantines_a_cabinet_the_tenant_does_not_hold(
    rehearsal: RehearsalHarness,
) -> None:
    """No active ``cabinet_company_grants`` row -> quarantine, never acceptance."""

    seed = await seed_partition(rehearsal)
    await rehearsal.execute(
        "DELETE FROM cabinet_company_grants WHERE cabinet_id = :cabinet",
        {"cabinet": seed.cabinet_id},
    )
    context = rehearsal.evidence_context(seed, transaction_id=await _next_transaction_id(rehearsal))
    result = await rehearsal.coordinator.process_row(
        candidate=scope_candidate(seed),
        checkpoint=initial_scope_checkpoint(seed),
        context=context,
    )
    assert result.outcome is BackfillOutcome.QUARANTINED
    assert result.reason_code == "parent_cardinality_or_lifecycle_invalid"
    assert result.parent_evidence is not None
    assert result.parent_evidence.active_cabinet_memberships == 0
    assert result.reason_code is not None

    quarantine = await rehearsal.rows(
        "SELECT reason_code, resolved_tenant_id FROM scope_owner_quarantine "
        "WHERE source_id = :sid",
        {"sid": seed.source_id},
    )
    assert len(quarantine) == 1
    assert str(quarantine[0][1]) == seed.tenant_id, (
        "a quarantined row still records the tenant it *would* have belonged to, so a "
        "reviewer can find it; it is not silently dropped"
    )
    assert await rehearsal.count("scope_registry") == 0, (
        "a quarantined scope must not leave an active registry row behind"
    )
    assert result.checkpoint.sequence == 1, (
        "a quarantined row still advances the resumable cursor, otherwise the backfill "
        "would retry it forever"
    )


async def test_m04_quarantines_a_payload_that_claims_another_tenant(
    rehearsal: RehearsalHarness,
) -> None:
    """A source assertion contradicting the resolved tenant is never trusted."""

    seed = await seed_partition(rehearsal)
    foreign_tenant = new_seed().tenant_id
    context = rehearsal.evidence_context(seed, transaction_id=await _next_transaction_id(rehearsal))
    from prodavan.application.migration.m04_scope.scope_migration import ScopeCandidate

    candidate = ScopeCandidate(
        source_kind=seed.source_kind,
        source_id=seed.source_id,
        schema_version=seed.source_schema_version,
        payload=dict(seed.source_payload),
        anchors=seed.scope_anchors,
        source_state=scope_candidate(seed).source_state,
        asserted_tenant_id=foreign_tenant,
        asserted_cabinet_id=seed.cabinet_id,
    )
    result = await rehearsal.coordinator.process_row(
        candidate=candidate,
        checkpoint=initial_scope_checkpoint(seed),
        context=context,
    )
    assert result.outcome is BackfillOutcome.QUARANTINED
    assert result.reason_code == "cross_tenant_source_assertion"
    assert await rehearsal.count("scope_registry") == 0


async def test_m04_replays_a_row_without_writing_a_second_mapping(
    rehearsal: RehearsalHarness,
) -> None:
    """Idempotency at the M04 boundary: the same source row twice is a replay."""

    seed = await seed_partition(rehearsal)
    candidate = scope_candidate(seed)
    first_context = rehearsal.evidence_context(
        seed, transaction_id=await _next_transaction_id(rehearsal)
    )
    first = await rehearsal.coordinator.process_row(
        candidate=candidate,
        checkpoint=initial_scope_checkpoint(seed),
        context=first_context,
    )
    assert first.outcome is BackfillOutcome.ACCEPTED

    mappings_after_first = await rehearsal.count(
        "scope_owner_mappings", "source_id = :sid", {"sid": seed.source_id}
    )
    registry_after_first = await rehearsal.count("scope_registry", "tenant_id = :t",
                                                 {"t": seed.tenant_id})

    second_context = rehearsal.evidence_context(
        seed, transaction_id=await _next_transaction_id(rehearsal)
    )
    second = await rehearsal.coordinator.process_row(
        candidate=candidate, checkpoint=first.checkpoint, context=second_context
    )

    assert second.outcome is BackfillOutcome.REPLAY
    assert second.reason_code == "scope_accepted", (
        "the replay must return the original mapping's outcome, not a new decision"
    )
    assert second.mapping_id == first.mapping_id, "a replay must not create a second mapping"
    assert await rehearsal.count(
        "scope_owner_mappings", "source_id = :sid", {"sid": seed.source_id}
    ) == mappings_after_first
    assert await rehearsal.count("scope_owner_audit") >= 2, (
        "a replay is still audited: the observation is evidence too"
    )
    assert await rehearsal.count("scope_registry", "tenant_id = :t", {"t": seed.tenant_id}) == (
        registry_after_first
    )


async def test_m04_conflicts_on_a_changed_digest_and_keeps_the_first_mapping(
    rehearsal: RehearsalHarness,
) -> None:
    """A changed source digest is a conflict, never an overwrite."""

    seed = await seed_partition(rehearsal)
    candidate = scope_candidate(seed)
    first = await rehearsal.coordinator.process_row(
        candidate=candidate,
        checkpoint=initial_scope_checkpoint(seed),
        context=rehearsal.evidence_context(
            seed, transaction_id=await _next_transaction_id(rehearsal)
        ),
    )
    assert first.outcome is BackfillOutcome.ACCEPTED
    original_mapping = await rehearsal.rows(
        "SELECT scope_registry_id, source_payload_digest FROM scope_owner_mappings "
        "WHERE source_id = :sid",
        {"sid": seed.source_id},
    )

    from prodavan.application.migration.m04_scope.scope_migration import ScopeCandidate

    changed = ScopeCandidate(
        source_kind=seed.source_kind,
        source_id=seed.source_id,
        schema_version=seed.source_schema_version,
        payload={"scope_kind": "cabinet", "scope_id": seed.cabinet_id, "changed": True},
        anchors=seed.scope_anchors,
        source_state=scope_candidate(seed).source_state,
        asserted_tenant_id=seed.tenant_id,
        asserted_cabinet_id=seed.cabinet_id,
    )
    assert changed.source_digest != candidate.source_digest

    conflict = await rehearsal.coordinator.process_row(
        candidate=changed,
        checkpoint=first.checkpoint,
        context=rehearsal.evidence_context(
            seed, transaction_id=await _next_transaction_id(rehearsal)
        ),
    )
    assert conflict.outcome is BackfillOutcome.CONFLICT
    assert conflict.reason_code == "source_digest_changed"
    assert conflict.mapping_id == first.mapping_id
    assert conflict.quarantine_id is not None, (
        "a conflict must record a redacted observation of the digest that changed"
    )

    preserved = await rehearsal.rows(
        "SELECT scope_registry_id, source_payload_digest, mapping_state "
        "FROM scope_owner_mappings WHERE source_id = :sid",
        {"sid": seed.source_id},
    )
    assert len(preserved) == 1
    assert preserved[0][1] == original_mapping[0][1], "the first mapping digest was overwritten"
    assert str(preserved[0][0]) == str(original_mapping[0][0])
    observation = await rehearsal.rows(
        "SELECT source_payload_digest, reason_code FROM scope_owner_quarantine "
        "WHERE source_id = :sid",
        {"sid": seed.source_id},
    )
    assert len(observation) == 1
    # The quarantine row records the digest that was *observed*, not the one that was
    # mapped: the mapping keeps the original (asserted above), the observation keeps the
    # change, and together they let a reviewer see both sides without trusting the
    # caller's claim about which one changed.  (The first draft of this test expected
    # the original digest here and was wrong about which half of the pair lives where.)
    assert str(observation[0][0]) == changed.source_digest, (
        "the observation must record the digest that was actually seen"
    )
    assert str(observation[0][1]) == "source_digest_changed"


async def test_m04_defers_plugin_instance_scope_to_m05(rehearsal: RehearsalHarness) -> None:
    """M04-02 has no plugin-instance scope; the row is deferred by name, not mapped."""

    seed = await seed_partition(rehearsal)
    from prodavan.application.migration.m04_scope.scope_migration import ScopeCandidate, SourceRowState
    from prodavan.application.scope_registry.contracts import ScopeAnchors, ScopeKind

    anchors = ScopeAnchors(
        scope_kind=ScopeKind.PLUGIN_INSTANCE,
        scope_id=seed.plugin_instance_id,
        tenant_id=seed.tenant_id,
        cabinet_id=seed.cabinet_id,
        plugin_instance_id=seed.plugin_instance_id,
    )
    candidate = ScopeCandidate(
        source_kind=seed.source_kind,
        source_id=seed.source_id,
        schema_version=seed.source_schema_version,
        payload=dict(seed.source_payload),
        anchors=anchors,
        # ``SourceRowState.ACTIVE`` matters: an ``UNKNOWN`` source row is quarantined
        # as ``source_row_lifecycle_unknown`` before the scope kind is ever considered,
        # which would make this test pass for the wrong reason (it would prove the
        # lifecycle guard, not the plugin-instance deferral).
        source_state=SourceRowState.ACTIVE,
        asserted_tenant_id=seed.tenant_id,
        asserted_cabinet_id=seed.cabinet_id,
    )
    result = await rehearsal.coordinator.process_row(
        candidate=candidate,
        checkpoint=initial_scope_checkpoint(seed),
        context=rehearsal.evidence_context(
            seed, transaction_id=await _next_transaction_id(rehearsal)
        ),
    )
    assert result.outcome is BackfillOutcome.DEFERRED
    assert result.reason_code == "plugin_instance_deferred_m05"
    assert await rehearsal.count("scope_registry") == 0
    assert result.checkpoint.sequence == 1


async def test_m04_checkpoint_is_a_real_cas_cursor(rehearsal: RehearsalHarness) -> None:
    """A stale expected checkpoint is a conflict, and the durable cursor survives."""

    seed = await seed_partition(rehearsal)
    candidate = scope_candidate(seed)
    first = await rehearsal.coordinator.process_row(
        candidate=candidate,
        checkpoint=initial_scope_checkpoint(seed),
        context=rehearsal.evidence_context(
            seed, transaction_id=await _next_transaction_id(rehearsal)
        ),
    )
    assert first.outcome is BackfillOutcome.ACCEPTED

    # A second, *different* source row from a stale (already consumed) checkpoint.
    other = scope_candidate(seed, source_id=f"{seed.source_id}-b")
    stale = await rehearsal.coordinator.process_row(
        candidate=other,
        checkpoint=initial_scope_checkpoint(seed),
        context=rehearsal.evidence_context(
            seed, transaction_id=await _next_transaction_id(rehearsal)
        ),
    )
    assert stale.outcome is BackfillOutcome.CONFLICT
    assert stale.reason_code == "checkpoint_compare_and_swap_conflict"
    assert stale.audit_event_id is not None, (
        "a conflict that left the caller's cursor in place must still be audited; the "
        "coordinator refuses a CONFLICT without an audit identity, so an omission here "
        "would surface as a RuntimeError after the transaction committed"
    )
    assert stale.checkpoint == initial_scope_checkpoint(seed), (
        "a cursor conflict must preserve the caller's checkpoint, not invent a new one"
    )
    stale_audit = await rehearsal.rows(
        "SELECT reason_code FROM scope_owner_audit WHERE id = :id", {"id": stale.audit_event_id}
    )
    assert len(stale_audit) == 1
    assert str(stale_audit[0][0]) == "checkpoint_compare_and_swap_conflict"
    assert await rehearsal.count(
        "scope_owner_mappings", "source_id = :sid", {"sid": other.source_id}
    ) == 0, "a stale page must not write a mapping for a row it never legitimately consumed"

    resumed = await rehearsal.coordinator.resume_checkpoint(
        stream=scope_stream(seed),
        context=rehearsal.evidence_context(
            seed, transaction_id=await _next_transaction_id(rehearsal)
        ),
    )
    assert resumed.reason_code == "checkpoint_resumed"
    assert resumed.checkpoint is not None
    assert resumed.checkpoint.sequence == 1
    assert resumed.checkpoint.after_source_id == seed.source_id, (
        "the durable cursor is the one the accepted row advanced to"
    )


async def test_m04_binding_is_attested_by_the_database_transaction_not_the_caller(
    rehearsal: RehearsalHarness,
) -> None:
    """What the binding actually proves, and what it only echoes.

    Two different identifiers travel in :class:`RlsBindingEvidence`:

    * ``database_transaction_id`` -- read with ``txid_current()`` inside the caller's
      open transaction.  This is the one that is *attested*: it cannot be produced
      without the GUCs having been applied to that very transaction, and
      ``page_transaction`` re-reads it and refuses a binding whose value drifted.
    * ``application_transaction_id`` -- the opaque id the caller handed in, returned
      unchanged.  ``_verify_binding`` compares it with the same caller-supplied value,
      so on its own it is an echo, not a proof.  That gap is recorded in
      ``infrastructure/persistence/migration_scope/WIRING.md`` ("no binding between the
      opaque application transaction ID and the adapter-owned PostgreSQL transaction")
      and this test states it explicitly instead of asserting a refusal that the code
      cannot produce.

    The third identifier that *is* checked independently is the principal: a context
    whose ``rls_context`` contradicts the resolved principal is refused before any
    statement runs.
    """

    seed = await seed_partition(rehearsal)
    context = rehearsal.evidence_context(
        seed, transaction_id=await _next_transaction_id(rehearsal)
    )

    async with rehearsal.scope_adapter.page_transaction(context=context) as page:
        inside = await _next_transaction_id_in(page.session)
        assert page.binding.database_transaction_id == inside, (
            "the binding must name the database transaction it was applied to"
        )
        assert page.binding.application_transaction_id == context.transaction_id
        assert page.binding.tenant_id == seed.tenant_id
        assert page.binding.database_role == "prodavan_service"
        assert page.binding.trusted_service is True, (
            "unresolved mapping/quarantine evidence needs the trusted service capability"
        )
        assert page.binding.all_verified()

    # An opaque id the caller invented is echoed, never verified -- documented, not
    # silently relied upon.
    echoed = rehearsal.evidence_context(seed, transaction_id="opaque-app-tx")
    async with rehearsal.scope_adapter.page_transaction(context=echoed) as page:
        assert page.binding.application_transaction_id == "opaque-app-tx"
        assert page.binding.database_transaction_id == await _next_transaction_id_in(page.session)

    # A context that contradicts the resolved principal is refused outright.
    from dataclasses import replace

    from prodavan.infrastructure.persistence.rls.context import RlsContextError

    contradicting = replace(
        context,
        rls_context=RlsContext(
            tenant_id=seed.tenant_id,
            cabinet_id=seed.project_id,  # not the cabinet the principal resolved
            project_id=None,
            user_id=seed.employee_id,
            role="company_admin",
        ),
        transaction_id=await _next_transaction_id(rehearsal),
    )
    with pytest.raises(RlsContextError) as excinfo:
        async with rehearsal.scope_adapter.page_transaction(context=contradicting):
            pytest.fail("a context contradicting the resolved principal must not bind")
    assert excinfo.value.reason_code == "cross_tenant_context"
    assert await rehearsal.count("scope_owner_mappings") == 0


async def _next_transaction_id_in(session) -> str:
    """``txid_current()`` of an already-open session's transaction."""

    return str((await session.execute(text("SELECT txid_current()::text"))).scalar())


async def test_m04_binder_refuses_a_writer_generation_it_was_not_issued_for(
    rehearsal: RehearsalHarness,
) -> None:
    """A stale writer generation is a lineage defect, not a retryable outage."""

    seed = await seed_partition(rehearsal)
    context = rehearsal.evidence_context(
        seed,
        transaction_id=await _next_transaction_id(rehearsal),
        writer_generation=seed.writer_generation + 1,
    )
    resume = await rehearsal.coordinator.resume_checkpoint(
        stream=scope_stream(seed), context=context
    )
    assert resume.reason_code == "writer_generation_mismatch"
    assert resume.checkpoint is None


async def test_m04_evidence_digests_are_derived_from_stored_rows(
    rehearsal: RehearsalHarness,
) -> None:
    """The canonical digest in the mapping must be recomputable from the registry row.

    ``ScopeMigrationCoordinator._canonical_digest`` is the reviewed formula; recomputing
    it here from the *stored* row is what proves the ledger digest describes the row a
    reviewer will read, not a value the port happened to hold in memory.
    """

    seed = await seed_partition(rehearsal)
    result = await rehearsal.coordinator.process_row(
        candidate=scope_candidate(seed),
        checkpoint=initial_scope_checkpoint(seed),
        context=rehearsal.evidence_context(
            seed, transaction_id=await _next_transaction_id(rehearsal)
        ),
    )
    assert result.outcome is BackfillOutcome.ACCEPTED
    row = (
        await rehearsal.rows(
            "SELECT scope_kind, scope_id, tenant_id, cabinet_id, project_id, "
            "       plugin_instance_id, owner_state FROM scope_registry "
            "WHERE tenant_id = :tenant",
            {"tenant": seed.tenant_id},
        )
    )[0]
    recomputed = sha256_digest(
        {
            "canonical_schema_version": "scope-registry.v1",
            "identity": [str(value) if value is not None else None for value in row[:6]],
            "owner_state": str(row[6]),
        }
    )
    assert recomputed == result.canonical_digest, (
        "the ledger's canonical digest does not describe the registry row it points at"
    )
