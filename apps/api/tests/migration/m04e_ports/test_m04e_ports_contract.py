"""Acceptance contract for the M04E SQL ports; authored during the freeze.

Nothing here is executed yet (see ``docs/08-migration/_subagent-brief.md``). Every
case asserts on the emitted SQL *and* on the returned evidence, because a fake that
filtered like PostgreSQL would let a broken partition predicate pass. The sections
map onto the task's acceptance list:

1. cursor page read, deterministic order, bounded page;
2. candidate payload determinism;
3. unsatisfied gate -> named refusal;
4. proof found / proof not found;
5. ``find_existing`` over an exact key, absence -> ``None``;
6. no in-memory path;
7. redaction — only digests, reason codes and gate names leave an adapter.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from prodavan.application.migration.m04_scope.scope_persistence import (
    encode_mapping_canonical_evidence,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillCheckpoint,
    CandidateDecision,
    DefinitionVersionEvidenceBackfill,
    EvidenceStatus,
    SourceRowState,
    UpstreamGateEvidence,
    export_m05_evidence,
)
from prodavan.application.migration.m04e_ports import (
    EVIDENCE_LEDGER_TABLES,
    EVIDENCE_STORE_LEDGER_DDL,
    EVIDENCE_STORE_REQUIRED_DDL,
    EVIDENCE_STORE_SCHEMA_GAPS,
    GATE_ACTIVE_TYPED_PARENT_SCOPE,
    GATE_M04E_01_DEFINITION_FOUNDATION,
    GATE_M04_01_OWNER_REGISTRY,
    GATE_M04_02_TYPED_SCOPE,
    LEGACY_DEFINITION_SOURCE_KIND,
    LEGACY_VERSION_SOURCE_KIND,
    MAX_PAGE_SIZE,
    M04EPortReason,
    M04EPortRefused,
    M04E_PORT_WIRING_BLOCKERS,
    PortRefusal,
    SqlM04ECandidateProof,
    SqlM04ECandidateReader,
    SqlM04EEvidenceStore,
    SqlM04EUpstreamGate,
    build_m04e_evidence_store,
    build_m04e_sql_ports,
    definition_candidate,
    legacy_status_to_state,
    mapping_canonical_digest,
    read_promotion_evidence,
    require_partition,
    require_usable_context,
    version_candidate,
)

from .acceptance_fixtures import (
    CORRELATION_ID,
    DEFINITION_ID,
    MANIFEST_VERSION,
    OTHER_DEFINITION_ID,
    OTHER_TENANT,
    SCOPE_CANONICAL_SCHEMA,
    SECRET_MARKER,
    SHA_A,
    SHA_B,
    SHA_C,
    SNAPSHOT_DIGEST,
    SOURCE_SCHEMA,
    TENANT,
    _NeverOpenedFactory,
    cabinet_anchor_row,
    cabinet_grant_row,
    cabinet_row,
    company_row,
    context,
    definition_row,
    dialect_session,
    expected_source_digest,
    promotion_row,
    registry_row,
    scope_mapping_row,
    session,
    tables,
    version_row,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def checkpoint(**overrides) -> BackfillCheckpoint:
    ctx = context()
    values = {
        "partition": (ctx.tenant_id, ctx.app_definition_id),
        "phase": "version",
        "after_source_id": None,
        "sequence": 0,
        "source_snapshot_id": ctx.source_snapshot_id,
        "source_schema_version": ctx.source_schema_version,
        "source_snapshot_digest": ctx.source_snapshot_digest,
        "canonical_schema_version": ctx.canonical_schema_version,
        "writer_generation": ctx.writer_generation,
    }
    values.update(overrides)
    return BackfillCheckpoint(**values)


def version_rows(*version_ids: str) -> list[dict]:
    """Version rows with unique manifest-version and artifact keys, as the schema demands."""

    rows = []
    for index, version_id in enumerate(version_ids, start=1):
        manifest_version = f"1.0.{index}"
        rows.append(version_row(
            id=version_id,
            manifest_version=manifest_version,
            artifact_digest="sha256:" + str(index) * 64,
            artifact_uri=f"content://{TENANT}/{DEFINITION_ID}/{version_id}",
            manifest={
                "protocol": "1.0",
                "appId": "demo-plugin",
                "version": manifest_version,
                "displayName": "demo",
                "entrypoints": {"plugin": {}},
                "requestedCapabilities": [],
            },
        ))
    return rows


async def accepted_gate(fake, factory) -> UpstreamGateEvidence:
    """Gate evidence produced by the real gate port over an ``accepted`` revision."""

    fake.tables["promotion"] = [promotion_row(outcome="accepted")]
    evidence = await SqlM04EUpstreamGate(session_factory=factory).prove_partition(
        context=context(),
    )
    assert evidence is not None, "the accepted fixture must satisfy every gate"
    return evidence


# ---------------------------------------------------------------------------
# 1. CandidateReader — bounded page, stable cursor, deterministic order
# ---------------------------------------------------------------------------


async def test_reader_reads_one_bounded_page_and_binds_the_partition_tenant() -> None:
    fake, factory = session(tables(versions=version_rows("aver_0001", "aver_0002", "aver_0003")))

    page = await SqlM04ECandidateReader(session_factory=factory).read_page(
        context=context(), phase="version", after_source_id=None, limit=2,
    )

    assert [candidate.source_id for candidate in page] == ["aver_0001", "aver_0002"]
    assert {candidate.source_kind for candidate in page} == {LEGACY_VERSION_SOURCE_KIND}
    (sql, params), = fake.executed("FROM dynamic_app_versions")
    # The bound travels in SQL as a LIMIT — never as "read everything then slice".
    assert 'ORDER BY id COLLATE "C" LIMIT :page_limit' in sql
    assert params["page_limit"] == 2
    assert "app_definition_id = :definition_id" in sql


async def test_reader_advances_with_the_cursor_and_never_repeats_a_row() -> None:
    fake, factory = session(tables(versions=version_rows("aver_0001", "aver_0002", "aver_0003")))
    port = SqlM04ECandidateReader(session_factory=factory)

    first = await port.read_page(
        context=context(), phase="version", after_source_id=None, limit=2,
    )
    second = await port.read_page(
        context=context(), phase="version", after_source_id=first[-1].source_id, limit=2,
    )
    third = await port.read_page(
        context=context(), phase="version", after_source_id=second[-1].source_id, limit=2,
    )

    assert [candidate.source_id for candidate in first] == ["aver_0001", "aver_0002"]
    assert [candidate.source_id for candidate in second] == ["aver_0003"]
    assert third == ()
    consumed = [candidate.source_id for candidate in (*first, *second)]
    assert consumed == sorted(consumed) and len(set(consumed)) == len(consumed)
    cursor_sql, cursor_params = fake.executed('COLLATE "C" > :after_source_id')[0]
    assert 'COLLATE "C" > :after_source_id' in cursor_sql
    assert cursor_params["after_source_id"] == "aver_0002"


async def test_reader_refuses_a_page_that_violates_the_cursor() -> None:
    _fake, factory = session(
        tables(versions=version_rows("aver_0001", "aver_0002")), honour_cursor=False,
    )

    with pytest.raises(M04EPortRefused) as raised:
        await SqlM04ECandidateReader(session_factory=factory).read_page(
            context=context(), phase="version", after_source_id="aver_0001", limit=2,
        )

    assert raised.value.code is M04EPortReason.PAGE_ORDER_UNDETERMINED


async def test_reader_refuses_an_unsorted_source_page() -> None:
    _fake, factory = session(
        tables(versions=version_rows("aver_0002", "aver_0001")), order_rows=False,
    )

    with pytest.raises(M04EPortRefused) as raised:
        await SqlM04ECandidateReader(session_factory=factory).read_page(
            context=context(), phase="version", after_source_id=None, limit=2,
        )

    assert raised.value.code is M04EPortReason.PAGE_ORDER_UNDETERMINED


async def test_reader_refuses_a_page_beyond_the_requested_bound() -> None:
    _fake, factory = session()
    port = SqlM04ECandidateReader(session_factory=factory)
    candidate = version_candidate(context(), version_rows("aver_0001")[0], definition_row())

    with pytest.raises(M04EPortRefused) as raised:
        port._validate_page(
            (candidate, candidate), cursor=None, limit=1, phase="version", context=context(),
        )

    assert raised.value.code is M04EPortReason.PAGE_BOUND_VIOLATED


@pytest.mark.parametrize(
    ("phase", "cursor", "limit", "code"),
    [
        ("evidence", None, 10, M04EPortReason.PHASE_INVALID),
        ("version", "", 10, M04EPortReason.CURSOR_INVALID),
        ("version", None, 0, M04EPortReason.PAGE_LIMIT_OUT_OF_RANGE),
        ("version", None, MAX_PAGE_SIZE + 1, M04EPortReason.PAGE_LIMIT_OUT_OF_RANGE),
        ("version", None, True, M04EPortReason.PAGE_LIMIT_OUT_OF_RANGE),
    ],
)
async def test_reader_refuses_a_request_outside_the_cursor_contract(
    phase: str, cursor: str | None, limit: int, code: M04EPortReason,
) -> None:
    _fake, factory = session()

    with pytest.raises(M04EPortRefused) as raised:
        await SqlM04ECandidateReader(session_factory=factory).read_page(
            context=context(), phase=phase, after_source_id=cursor, limit=limit,
        )

    assert raised.value.code is code


async def test_reader_never_reads_another_partition() -> None:
    fake, factory = session(tables(
        definitions=[definition_row(id=OTHER_DEFINITION_ID, tenant_id=OTHER_TENANT)],
        versions=version_rows("aver_0009"),
    ))

    page = await SqlM04ECandidateReader(session_factory=factory).read_page(
        context=context(), phase="definition", after_source_id=None, limit=10,
    )

    assert page == ()
    (sql, params), = fake.executed("FROM dynamic_app_definitions")
    assert "tenant_id = :tenant_id" in sql
    assert params["tenant_id"] == TENANT
    assert OTHER_TENANT not in params.values()


async def test_reader_never_returns_a_version_of_another_definition() -> None:
    _fake, factory = session(tables(versions=(
        *version_rows("aver_0001"),
        version_row(id="aver_9999", app_definition_id=OTHER_DEFINITION_ID),
    )))

    page = await SqlM04ECandidateReader(session_factory=factory).read_page(
        context=context(), phase="version", after_source_id=None, limit=10,
    )

    assert [candidate.source_id for candidate in page] == ["aver_0001"]
    assert all(candidate.app_definition_id == DEFINITION_ID for candidate in page)


async def test_reader_binds_the_transaction_local_context_and_refuses_an_unproven_one() -> None:
    fake, factory = session()
    await SqlM04ECandidateReader(session_factory=factory).read_page(
        context=context(), phase="definition", after_source_id=None, limit=1,
    )
    applied_sql, applied_params = fake.executed("set_config('app.tenant_id'")[0]
    assert ", true)" in applied_sql
    assert not applied_sql.strip().upper().startswith("SET ")
    assert applied_params["tenant_id"] == TENANT
    assert fake.executed("current_setting('app.tenant_id', true)")

    _fake, unbound = session(readback={"tenant_id": OTHER_TENANT})
    with pytest.raises(M04EPortRefused) as unbound_error:
        await SqlM04ECandidateReader(session_factory=unbound).read_page(
            context=context(), phase="definition", after_source_id=None, limit=1,
        )
    assert unbound_error.value.code is M04EPortReason.TENANT_CONTEXT_UNBOUND

    _fake, exposed = session(readback={"row_security": "off"})
    with pytest.raises(M04EPortRefused) as exposed_error:
        await SqlM04ECandidateReader(session_factory=exposed).read_page(
            context=context(), phase="definition", after_source_id=None, limit=1,
        )
    assert exposed_error.value.code is M04EPortReason.ROW_SECURITY_DISABLED

    _fake, weak = session(readback={"transaction_isolation": "read committed"})
    strict = SqlM04ECandidateReader(session_factory=weak, require_serializable=True)
    with pytest.raises(M04EPortRefused) as weak_error:
        await strict.read_page(
            context=context(), phase="definition", after_source_id=None, limit=1,
        )
    assert weak_error.value.code is M04EPortReason.TRANSACTION_ISOLATION_INSUFFICIENT


async def test_reader_refuses_an_unusable_context_and_a_foreign_partition() -> None:
    _fake, factory = session()
    port = SqlM04ECandidateReader(session_factory=factory)

    with pytest.raises(M04EPortRefused) as unusable:
        await port.read_page(
            context=context(source_snapshot_digest="not-a-digest"),
            phase="definition", after_source_id=None, limit=1,
        )
    assert unusable.value.code is M04EPortReason.CONTEXT_UNUSABLE

    with pytest.raises(M04EPortRefused) as foreign:
        require_partition(context(), tenant_id=OTHER_TENANT, app_definition_id=DEFINITION_ID)
    assert foreign.value.code is M04EPortReason.PARTITION_MISMATCH

    # The exact partition passes both guards without raising.
    require_partition(context(), tenant_id=TENANT, app_definition_id=DEFINITION_ID)
    require_usable_context(context())


# ---------------------------------------------------------------------------
# 2. Candidate payload construction
# ---------------------------------------------------------------------------


def test_definition_candidate_digest_tracks_identity_and_ignores_timestamps() -> None:
    row = definition_row()
    base = definition_candidate(context(), row)

    assert base.source_digest == expected_source_digest(base)
    assert base.canonical_identity == (TENANT, "demo-plugin")
    # A non-identity column (updated_at) must digest identically, so a touch can never
    # be replayed as a source conflict.
    touched = definition_candidate(context(), {**row, "updated_at": "2026-11-04T00:00:00Z"})
    assert touched.source_digest == base.source_digest
    # An approval-relevant change is a real source change.
    republished = definition_candidate(context(), {**row, "current_version_id": "aver_0002"})
    assert republished.source_digest != base.source_digest


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("draft", SourceRowState.ACTIVE),
        ("published", SourceRowState.ACTIVE),
        ("disabled", SourceRowState.ARCHIVED),
        ("revoked", SourceRowState.ARCHIVED),
        ("unknown", SourceRowState.UNKNOWN),
        (None, SourceRowState.UNKNOWN),
    ],
)
def test_legacy_lifecycle_mapping_is_closed(status: str | None, state: SourceRowState) -> None:
    assert legacy_status_to_state(status) is state


def test_version_candidate_carries_the_current_link_only_when_published() -> None:
    definition = definition_row()
    published = version_candidate(context(), version_row(status="published"), definition)
    draft = version_candidate(context(), version_row(status="draft"), definition)

    assert published.payload["definition_current_version_id"] == "aver_0001"
    assert draft.payload["definition_current_version_id"] is None
    assert published.canonical_identity == (TENANT, DEFINITION_ID, MANIFEST_VERSION)
    assert published.source_digest == expected_source_digest(published)


def test_definition_candidates_are_built_for_the_context_lineage() -> None:
    candidate = definition_candidate(context(), definition_row())

    assert candidate.source_kind == LEGACY_DEFINITION_SOURCE_KIND
    assert candidate.schema_version == SOURCE_SCHEMA
    assert candidate.canonical_schema_version == context().canonical_schema_version
    assert candidate.payload["app_id"] == "demo-plugin"


# ---------------------------------------------------------------------------
# 3. UpstreamGatePort — unsatisfied gate -> named refusal
# ---------------------------------------------------------------------------


async def test_gate_names_the_unsatisfied_gate_and_reports_the_gates_that_passed() -> None:
    _fake, factory = session()
    decision = await SqlM04EUpstreamGate(session_factory=factory).partition_gate(
        context=context(),
    )

    assert decision.evidence is None
    assert decision.refusal is not None
    assert decision.refusal.code is M04EPortReason.M04E_01_PROMOTION_BLOCKED
    assert decision.refusal.gate_name == GATE_M04E_01_DEFINITION_FOUNDATION
    assert [name for name, _digest in decision.probes] == [
        GATE_M04_01_OWNER_REGISTRY, GATE_M04_02_TYPED_SCOPE,
    ]
    assert all(digest.startswith("sha256:") for _name, digest in decision.probes)


async def test_gate_returns_none_when_the_owner_gate_is_unsatisfied() -> None:
    _fake, factory = session(tables(companies=[company_row(status="paused")]))
    port = SqlM04EUpstreamGate(session_factory=factory)

    assert await port.prove_partition(context=context()) is None
    decision = await port.partition_gate(context=context())
    assert decision.refusal is not None
    assert decision.refusal.code is M04EPortReason.M04_01_OWNER_REGISTRY_UNPROVEN
    assert decision.refusal.gate_name == GATE_M04_01_OWNER_REGISTRY


async def test_gate_returns_none_when_the_typed_scope_is_unsatisfied() -> None:
    _fake, factory = session(tables(scope_mappings=[]))

    decision = await SqlM04EUpstreamGate(session_factory=factory).partition_gate(
        context=context(),
    )

    assert decision.evidence is None
    assert decision.refusal is not None
    assert decision.refusal.code is M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN
    assert decision.refusal.gate_name == GATE_M04_02_TYPED_SCOPE


async def test_gate_refuses_a_cabinet_scope_without_its_parent_chain() -> None:
    _fake, factory = session(tables(
        definitions=[definition_row(scope_kind="cabinet", scope_id="cab_0001")],
        registry=[registry_row(
            scope_kind="cabinet", scope_id="cab_0001", cabinet_id="cab_0001",
        )],
        cabinet_anchors=[],
        cabinet_grants=[cabinet_grant_row()],
        cabinets=[cabinet_row()],
    ))

    decision = await SqlM04EUpstreamGate(session_factory=factory).partition_gate(
        context=context(),
    )

    assert decision.refusal is not None
    assert decision.refusal.code is M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN
    assert decision.refusal.gate_name == GATE_ACTIVE_TYPED_PARENT_SCOPE


async def test_gate_proves_a_complete_cabinet_parent_chain() -> None:
    _fake, factory = session(tables(
        definitions=[definition_row(scope_kind="cabinet", scope_id="cab_0001")],
        registry=[registry_row(
            scope_kind="cabinet", scope_id="cab_0001", cabinet_id="cab_0001",
        )],
        cabinet_anchors=[cabinet_anchor_row()],
        cabinet_grants=[cabinet_grant_row()],
        cabinets=[cabinet_row()],
        promotion=[promotion_row(outcome="accepted")],
    ))

    evidence = await SqlM04EUpstreamGate(session_factory=factory).prove_partition(
        context=context(),
    )

    assert evidence is not None and evidence.valid


async def test_gate_refuses_a_project_scope_whose_parent_is_not_active() -> None:
    _fake, factory = session(tables(
        definitions=[definition_row(scope_kind="project", scope_id="prj_0001")],
        registry=[registry_row(
            scope_kind="project", scope_id="prj_0001",
            cabinet_id="cab_0001", project_id="prj_0001", project_status="active",
        )],
        cabinet_anchors=[cabinet_anchor_row()],
        cabinet_grants=[cabinet_grant_row()],
        cabinets=[cabinet_row()],
        project_anchors=[],
    ))

    decision = await SqlM04EUpstreamGate(session_factory=factory).partition_gate(
        context=context(),
    )

    assert decision.refusal is not None
    assert decision.refusal.gate_name == GATE_ACTIVE_TYPED_PARENT_SCOPE


async def test_gate_refuses_a_partition_without_any_definition_row() -> None:
    _fake, factory = session(tables(definitions=[]))

    decision = await SqlM04EUpstreamGate(session_factory=factory).partition_gate(
        context=context(),
    )

    assert decision.evidence is None
    assert decision.refusal is not None
    assert decision.refusal.code is M04EPortReason.M04E_01_DEFINITION_ABSENT


async def test_gate_refuses_promotion_evidence_from_another_snapshot() -> None:
    _fake, factory = session(tables(promotion=[
        promotion_row(outcome="accepted", source_snapshot_id="snapshot-other"),
    ]))

    decision = await SqlM04EUpstreamGate(session_factory=factory).partition_gate(
        context=context(),
    )

    assert decision.refusal is not None
    assert decision.refusal.code is M04EPortReason.M04E_01_PROMOTION_LINEAGE_MISMATCH


async def test_gate_is_satisfied_only_by_an_accepted_promotion_revision() -> None:
    """The foundation tables alone never satisfy ``MIG-M04E-01``.

    A blocked/quarantined promotion record is the only outcome this schema can
    store, so the gate refuses by name even though every other gate passes; the
    mapping it guards cannot be its own evidence.
    """

    _fake, blocked = session(tables())
    blocked_decision = await SqlM04EUpstreamGate(session_factory=blocked).partition_gate(
        context=context(),
    )
    assert blocked_decision.refusal is not None
    assert blocked_decision.refusal.code is M04EPortReason.M04E_01_PROMOTION_BLOCKED
    assert GATE_M04E_01_DEFINITION_FOUNDATION in blocked_decision.refusal.evidence_text()

    fake, accepted_factory = session(tables(promotion=[promotion_row(outcome="accepted")]))
    evidence = await SqlM04EUpstreamGate(
        session_factory=accepted_factory,
    ).prove_partition(context=context())

    assert evidence is not None
    assert evidence.valid
    assert evidence.tenant_id == TENANT
    assert evidence.source_snapshot_digest == SNAPSHOT_DIGEST
    assert evidence.transaction_id == context().transaction_id
    assert evidence.correlation_id == CORRELATION_ID
    for digest in (
        evidence.m04_gate_digest, evidence.m04e_gate_digest, evidence.active_scope_digest,
    ):
        assert digest.startswith("sha256:") and len(digest) == 71
    assert fake.executed("FROM m04e_definition_version_promotion_evidence")


async def test_gate_evidence_is_reproducible_for_the_same_partition() -> None:
    _fake, factory = session(tables())
    port = SqlM04EUpstreamGate(session_factory=factory)

    first = await port.partition_gate(context=context())
    second = await port.partition_gate(context=context())

    assert dict(first.probes) == dict(second.probes)


def test_mapping_canonical_digest_decodes_exactly_what_m04_packs() -> None:
    """The cross-stage digest contract: M04 packs, MIG-M04-02 unpacks — one spelling.

    ``scope_owner_mappings`` has no ``canonical_schema_version`` column, so the M04
    writer stores the canonical lineage *and* the digest as ``v1|<schema>|<digest>``
    (``scope_persistence.encode_mapping_canonical_evidence``).  ``mapping_canonical_digest``
    is the only M04E-side decoder of that column: it must return the digest for exactly
    the spelling the writer produces, and ``None`` (a refusal, never a guess) for a bare
    digest, a digestless pack, a malformed pack or a foreign prefix.  Before this
    contract existed the port demanded a bare ``sha256:`` value no M04 write can
    produce, so the two stages could never agree on a mapped scope.
    """

    packed = encode_mapping_canonical_evidence(SCOPE_CANONICAL_SCHEMA, SHA_C)
    assert packed == f"v1|{SCOPE_CANONICAL_SCHEMA}|{SHA_C}"
    assert mapping_canonical_digest(packed) == SHA_C

    # A pack without a digest is a quarantined/deferred mapping: not an acceptance.
    digestless = encode_mapping_canonical_evidence(SCOPE_CANONICAL_SCHEMA, None)
    assert mapping_canonical_digest(digestless) is None

    # Never a second, divergent reading of the column.
    assert mapping_canonical_digest(SHA_C) is None
    assert mapping_canonical_digest(None) is None
    assert mapping_canonical_digest("") is None
    assert mapping_canonical_digest("v1|scope-registry.v1|sha256:not-a-digest") is None
    assert mapping_canonical_digest(f"v2|{SCOPE_CANONICAL_SCHEMA}|{SHA_C}") is None
    assert mapping_canonical_digest(f"v1||{SHA_C}") is None


async def test_gate_accepts_the_packed_m04_evidence_and_refuses_a_bare_digest() -> None:
    """MIG-M04-02 end to end over the fixture the M04 writer's column shape models.

    The default ``scope_mapping_row`` carries the packed value, so a complete
    partition proves the typed-scope gate.  The same row with a bare ``sha256:``
    digest in ``canonical_payload_digest`` — a spelling M04 never writes — must be
    refused by name rather than silently accepted or mis-decoded.
    """

    _fake, factory = session(tables(promotion=[promotion_row(outcome="accepted")]))
    evidence = await SqlM04EUpstreamGate(session_factory=factory).prove_partition(
        context=context(),
    )
    assert evidence is not None and evidence.valid

    _bare, bare_factory = session(tables(
        scope_mappings=[scope_mapping_row(canonical_payload_digest=SHA_C)],
        promotion=[promotion_row(outcome="accepted")],
    ))
    decision = await SqlM04EUpstreamGate(session_factory=bare_factory).partition_gate(
        context=context(),
    )
    assert decision.evidence is None
    assert decision.refusal is not None
    assert decision.refusal.code is M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN
    assert decision.refusal.gate_name == GATE_M04_02_TYPED_SCOPE


async def test_promotion_evidence_read_is_bound_to_the_exact_partition() -> None:
    fake, _factory = session(tables(promotion=[
        promotion_row(id="prom_other", tenant_id=OTHER_TENANT, outcome="accepted"),
    ]))

    refusal = await read_promotion_evidence(fake, context=context())

    assert isinstance(refusal, PortRefusal)
    assert not refusal
    assert refusal.code is M04EPortReason.M04E_01_PROMOTION_ABSENT
    sql, params = fake.executed("FROM m04e_definition_version_promotion_evidence")[0]
    assert "tenant_id = :tenant_id" in sql and "definition_id = :definition_id" in sql
    # The gate reads one kind of envelope. The same table carries M05 handoff
    # markers and M05 promotion records, both stamped ``outcome='blocked'``, so
    # without this predicate the newest append would shadow a positive
    # promotion and the gate would refuse a partition it had already approved.
    assert "gate_evidence ->> 'schema' = :backfill_schema" in sql
    assert params == {
        "tenant_id": TENANT,
        "definition_id": DEFINITION_ID,
        "backfill_schema": "m04e-backfill-page.v1",
    }


# ---------------------------------------------------------------------------
# 4. CandidateProofPort — proof found / proof not found
# ---------------------------------------------------------------------------


async def test_candidate_proof_satisfies_the_backfill_contract_for_a_definition() -> None:
    fake, factory = session()
    gates = await accepted_gate(fake, factory)
    candidate = definition_candidate(context(), definition_row())

    decision = await SqlM04ECandidateProof(session_factory=factory).candidate_proof(
        context=context(), gates=gates, candidate=candidate,
    )

    assert decision.proof is not None and decision.refusal is None
    proof = decision.proof
    assert proof.tenant_id == TENANT and proof.app_definition_id == DEFINITION_ID
    assert proof.active_scope_proof_digest == gates.active_scope_digest
    assert proof.current_version_match_count == 1
    assert proof.current_version_id == "aver_0001"
    assert proof.current_version_lifecycle == "published"
    assert proof.same_definition_proof_digest is None
    assert proof.artifact_immutability_proof_digest is None
    for digest in (
        proof.owner_proof_digest,
        proof.active_scope_proof_digest,
        proof.current_version_proof_digest,
        proof.current_version_same_definition_digest,
        proof.current_version_immutable_artifact_digest,
    ):
        assert digest is not None and digest.startswith("sha256:")
    assert DefinitionVersionEvidenceBackfill._valid_candidate_proof(
        proof, candidate, context(), gates,
    )


async def test_candidate_proof_satisfies_the_backfill_contract_for_a_version() -> None:
    fake, factory = session()
    gates = await accepted_gate(fake, factory)
    candidate = version_candidate(context(), version_row(), definition_row())

    proof = await SqlM04ECandidateProof(session_factory=factory).prove_candidate(
        context=context(), gates=gates, candidate=candidate,
    )

    assert proof is not None
    assert proof.app_version_id == "aver_0001"
    assert proof.manifest_version == MANIFEST_VERSION
    assert proof.artifact_digest == SHA_A
    assert proof.same_definition_proof_digest.startswith("sha256:")
    assert proof.artifact_immutability_proof_digest.startswith("sha256:")
    assert DefinitionVersionEvidenceBackfill._valid_candidate_proof(
        proof, candidate, context(), gates,
    )


async def test_candidate_proof_returns_none_when_the_source_row_is_absent() -> None:
    fake, factory = session()
    gates = await accepted_gate(fake, factory)
    candidate = version_candidate(context(), version_row(), definition_row())
    fake.tables["versions"] = []
    port = SqlM04ECandidateProof(session_factory=factory)

    assert await port.prove_candidate(
        context=context(), gates=gates, candidate=candidate,
    ) is None
    decision = await port.candidate_proof(
        context=context(), gates=gates, candidate=candidate,
    )
    assert decision.proof is None
    assert decision.refusal is not None
    assert decision.refusal.code is M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID


async def test_candidate_proof_refuses_a_manifest_that_names_another_application() -> None:
    fake, factory = session()
    gates = await accepted_gate(fake, factory)
    manifest = dict(version_row()["manifest"], appId="other-plugin")
    fake.tables["versions"] = [version_row(manifest=manifest)]
    candidate = version_candidate(context(), version_row(manifest=manifest), definition_row())

    decision = await SqlM04ECandidateProof(session_factory=factory).candidate_proof(
        context=context(), gates=gates, candidate=candidate,
    )

    assert decision.proof is None
    assert decision.refusal is not None
    assert decision.refusal.code is M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID


async def test_candidate_proof_refuses_when_the_source_changed_under_the_page() -> None:
    fake, factory = session()
    gates = await accepted_gate(fake, factory)
    candidate = version_candidate(context(), version_row(), definition_row())
    fake.tables["versions"] = [version_row(artifact_digest=SHA_B)]

    with pytest.raises(M04EPortRefused) as raised:
        await SqlM04ECandidateProof(session_factory=factory).prove_candidate(
            context=context(), gates=gates, candidate=candidate,
        )

    assert raised.value.code is M04EPortReason.PROOF_SOURCE_CHANGED


async def test_candidate_proof_refuses_when_the_active_scope_moved_since_the_gate() -> None:
    fake, factory = session()
    gates = await accepted_gate(fake, factory)
    fake.tables["registry"] = [registry_row(checkpoint="c1:9:ffff")]

    with pytest.raises(M04EPortRefused) as raised:
        await SqlM04ECandidateProof(session_factory=factory).prove_candidate(
            context=context(), gates=gates,
            candidate=definition_candidate(context(), definition_row()),
        )

    assert raised.value.code is M04EPortReason.PROOF_ACTIVE_SCOPE_MISMATCH


async def test_candidate_proof_refuses_gate_evidence_from_another_lineage() -> None:
    fake, factory = session()
    gates = await accepted_gate(fake, factory)

    with pytest.raises(M04EPortRefused) as raised:
        await SqlM04ECandidateProof(session_factory=factory).prove_candidate(
            context=context(correlation_id="corr-other"),
            gates=gates,
            candidate=definition_candidate(context(), definition_row()),
        )

    assert raised.value.code is M04EPortReason.PROOF_GATES_INVALID


async def test_candidate_proof_refuses_a_candidate_from_another_partition() -> None:
    fake, factory = session()
    gates = await accepted_gate(fake, factory)
    other = definition_candidate(
        context(), definition_row(tenant_id=OTHER_TENANT, id=OTHER_DEFINITION_ID),
    )

    with pytest.raises(M04EPortRefused) as raised:
        await SqlM04ECandidateProof(session_factory=factory).prove_candidate(
            context=context(), gates=gates, candidate=other,
        )

    assert raised.value.code is M04EPortReason.PROOF_CANDIDATE_PARTITION_MISMATCH


async def test_candidate_proof_refuses_an_already_mapped_target_that_disagrees() -> None:
    fake, factory = session(tables(target_definitions=[{
        "id": DEFINITION_ID,
        "tenant_id": TENANT,
        "app_id": "other-plugin",
        "scope_kind": "tenant",
        "scope_id": TENANT,
        "lifecycle": "published",
        "current_version_id": "aver_0001",
        "source_snapshot_id": "snapshot-2026-11-04",
        "source_digest": SHA_A,
    }]))
    gates = await accepted_gate(fake, factory)

    decision = await SqlM04ECandidateProof(session_factory=factory).candidate_proof(
        context=context(), gates=gates,
        candidate=definition_candidate(context(), definition_row()),
    )

    assert decision.proof is None
    assert decision.refusal is not None
    assert decision.refusal.code is M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID


# ---------------------------------------------------------------------------
# 5. EvidenceStore — the ledger exists; the boundary is the partition, not a DDL gap
# ---------------------------------------------------------------------------
#
# These cases used to assert that ``find_existing`` / ``find_observation`` /
# ``record_blocked`` / ``classify_and_commit_page`` *raise*
# ``EvidenceStoreUnavailable`` with ``EVIDENCE_STORE_SCHEMA_GAPS``, because no
# registered table could carry a per-source ledger. Revision ``2026110315`` created
# ``m04e_definition_version_{mappings,quarantine,checkpoints,audit}``, so that
# expectation is now wrong: a store that refused would be refusing over tables that
# exist. What still has to be pinned *here* is the shape of the boundary:
#
# * the store is a real object over the four registered tables (section 6 proves none
#   of the four methods can exist without a database);
# * it refuses a non-PostgreSQL session unless a test mirror is explicitly allowed, so
#   a composition root cannot write the ledger without the RLS binding;
# * the schema-gap list no longer claims the tables are missing, and the required-DDL
#   list is empty while the reviewed spec of the four objects is preserved.
#
# The behaviour of the four methods — page commit, replay, conflict, CAS conflict,
# quarantine and audit balance — is exercised against a real SQLite mirror of the four
# registered tables in ``test_m04e_evidence_store.py``.


def test_the_ledger_tables_are_the_four_the_models_register() -> None:
    from prodavan.infrastructure.persistence.models import Base
    from prodavan.infrastructure.persistence.models.m04e_ledger import M04E_LEDGER_TABLES

    assert set(EVIDENCE_LEDGER_TABLES) == set(M04E_LEDGER_TABLES)
    for table in EVIDENCE_LEDGER_TABLES:
        assert table in Base.metadata.tables, f"{table} is not in the metadata"


def test_the_store_is_built_over_the_registered_tables_not_over_a_promise() -> None:
    store = SqlM04EEvidenceStore(session_factory=_NeverOpenedFactory())

    assert store.IS_FAIL_CLOSED_BOUNDARY is False
    assert store.ledger_tables == EVIDENCE_LEDGER_TABLES
    # The DDL the port used to demand is in the schema, so nothing is left to demand.
    # Keeping the list (empty) is what makes the *absence* of a gap explicit.
    assert EVIDENCE_STORE_REQUIRED_DDL == ()
    # The reviewed spec of those four objects is preserved verbatim: the ledger
    # revision's own contract test parses it as the specification it must satisfy.
    assert len(EVIDENCE_STORE_LEDGER_DDL) == 4
    assert {entry.split(" ", 1)[0] for entry in EVIDENCE_STORE_LEDGER_DDL} == set(
        EVIDENCE_LEDGER_TABLES
    )


def test_the_remaining_gaps_name_the_promotion_table_not_a_missing_ledger() -> None:
    """The three surviving gaps are facts about ``..._promotion_evidence``.

    They used to be mixed with four "no table carries X" entries. Those four are gone
    because the tables exist; asserting the survivors keeps a future reader from
    re-introducing a phantom missing-table blocker next to a working store.
    """

    assert len(EVIDENCE_STORE_SCHEMA_GAPS) == 3
    for gap in EVIDENCE_STORE_SCHEMA_GAPS:
        assert gap.startswith("m04e_promotion_"), gap
    rendered = " ".join(EVIDENCE_STORE_SCHEMA_GAPS)
    assert "ck_m04e_promotion_outcome" in rendered
    # The ledger is not missing, and the gap list must not pretend otherwise.
    for phantom in (
        "m04e_backfill_mapping_ledger_missing",
        "m04e_backfill_observation_ledger_missing",
        "m04e_backfill_checkpoint_ledger_missing",
        "m04e_backfill_audit_ledger_missing",
    ):
        assert phantom not in rendered, phantom


async def test_the_store_refuses_a_non_postgresql_session_unless_a_mirror_is_allowed() -> None:
    """A SQLite session without RLS must not silently become the production ledger."""

    fake, factory = dialect_session("sqlite")

    with pytest.raises(M04EPortRefused) as raised:
        await SqlM04EEvidenceStore(session_factory=factory).find_existing(
            context=context(), source_kind=LEGACY_DEFINITION_SOURCE_KIND,
            source_id=DEFINITION_ID, source_schema_version=SOURCE_SCHEMA,
        )

    assert raised.value.code is M04EPortReason.TRANSACTION_ISOLATION_INSUFFICIENT
    assert "PostgreSQL" in raised.value.refusal.detail
    # Refused before any statement: no read ran without a proven partition.
    assert fake.statements == []


async def test_the_store_refuses_an_observation_that_is_not_exactly_one_digest() -> None:
    """``find_observation`` answers an exact key; a malformed key is not "absent".

    ``ck_m04e_quarantine_single_observation`` admits exactly one observed digest. A
    caller naming both — or neither — asks a question the ledger cannot contain, so the
    store refuses instead of returning ``None``, which would read as "this observation
    was never recorded" and license a second quarantine row.
    """

    fake, factory = dialect_session("postgresql")
    store = SqlM04EEvidenceStore(session_factory=factory)

    for kwargs in (
        {"observed_source_digest": None, "observed_artifact_digest": None},
        {"observed_source_digest": SHA_A, "observed_artifact_digest": SHA_B},
    ):
        with pytest.raises(M04EPortRefused) as raised:
            await store.find_observation(
                context=context(), source_kind=LEGACY_VERSION_SOURCE_KIND,
                source_id="aver_0001", source_schema_version=SOURCE_SCHEMA, **kwargs,
            )
        assert raised.value.code is M04EPortReason.STORE_OUTCOME_UNREPRESENTABLE
        assert "exactly one observed digest" in raised.value.refusal.detail
    assert fake.statements == []


async def test_the_store_refuses_a_checkpoint_from_another_partition() -> None:
    """The cursor is part of the partition, never a free-floating argument."""

    fake, factory = dialect_session("postgresql")
    store = SqlM04EEvidenceStore(session_factory=factory)
    foreign = checkpoint(
        partition=(OTHER_TENANT, DEFINITION_ID),
        source_snapshot_id=context().source_snapshot_id,
        source_schema_version=context().source_schema_version,
        source_snapshot_digest=context().source_snapshot_digest,
        canonical_schema_version=context().canonical_schema_version,
    )

    with pytest.raises(M04EPortRefused) as raised:
        await store.record_blocked(
            context=context(), checkpoint=foreign,
            reason_code="upstream_owner_scope_gate_unproven",
        )

    assert raised.value.code is M04EPortReason.PARTITION_MISMATCH
    assert fake.statements == []


async def test_the_store_refuses_a_page_whose_gates_belong_to_another_correlation() -> None:
    """Gate evidence is re-checked by the store, not only by ``run_page``."""

    _fake, factory = dialect_session("postgresql")
    store = SqlM04EEvidenceStore(session_factory=factory)
    gates = UpstreamGateEvidence(
        tenant_id=TENANT, app_definition_id=DEFINITION_ID,
        source_snapshot_digest=SNAPSHOT_DIGEST, transaction_id="pg:1",
        correlation_id="corr-other", m04_gate_digest=SHA_A,
        m04e_gate_digest=SHA_B, active_scope_digest=SHA_C,
    )

    with pytest.raises(M04EPortRefused) as raised:
        await store.classify_and_commit_page(
            context=context(), gates=gates, expected_checkpoint=checkpoint(),
            decisions=(), page_exhausted=True,
        )

    assert raised.value.code is M04EPortReason.GATE_CONTEXT_INVALID


async def test_evidence_store_reads_the_partition_promotion_record_exactly() -> None:
    fake, factory = session()

    record = await SqlM04EEvidenceStore(session_factory=factory).latest_promotion_evidence(
        context=context(),
    )

    assert record is not None
    assert record.outcome == "blocked"
    assert record.tenant_id == TENANT and record.definition_id == DEFINITION_ID
    assert record.source_snapshot_id == "snapshot-2026-11-04"
    assert record.source_digest == SNAPSHOT_DIGEST
    (sql, params), = fake.executed("FROM m04e_definition_version_promotion_evidence")
    assert "tenant_id = :tenant_id" in sql and "definition_id = :definition_id" in sql
    # Same envelope filter as the gate above: the store and the gate must read
    # the same row, or one of them reports a promotion the other cannot see.
    assert "gate_evidence ->> 'schema' = :backfill_schema" in sql
    assert params == {
        "tenant_id": TENANT,
        "definition_id": DEFINITION_ID,
        "backfill_schema": "m04e-backfill-page.v1",
    }


async def test_evidence_store_returns_none_only_for_an_absent_partition_record() -> None:
    _fake, other_partition = session(tables(promotion=[
        promotion_row(id="prom_other", tenant_id=OTHER_TENANT, outcome="accepted"),
    ]))
    _fake, empty = session(tables(promotion=[]))

    assert await SqlM04EEvidenceStore(
        session_factory=other_partition,
    ).latest_promotion_evidence(context=context()) is None
    assert await SqlM04EEvidenceStore(session_factory=empty).latest_promotion_evidence(
        context=context(),
    ) is None


def test_the_wiring_blockers_no_longer_claim_an_unimplemented_store() -> None:
    """``m04e_evidence_store_port_unimplemented`` was true only until the store existed.

    Keeping it would report a blocker for a port that is implemented, which is how a
    diagnostic resource starts lying. What legitimately remains is the privilege review
    of the migration role and the two-state promotion CHECK.
    """

    assert not any(
        "m04e_evidence_store_port_unimplemented" in item
        for item in M04E_PORT_WIRING_BLOCKERS
    )
    rendered = " ".join(M04E_PORT_WIRING_BLOCKERS)
    assert "m04e_backfill_tenant_context_binding_unreviewed" in rendered
    assert "m04e_positive_promotion_gate_unrepresentable" in rendered


# ---------------------------------------------------------------------------
# 6. No in-memory path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "adapter",
    [SqlM04ECandidateReader, SqlM04EUpstreamGate, SqlM04ECandidateProof, SqlM04EEvidenceStore],
)
def test_every_adapter_refuses_to_exist_without_a_database(adapter: type) -> None:
    with pytest.raises(ValueError) as raised:
        adapter(session_factory=None)

    assert str(raised.value) == M04EPortReason.SESSION_FACTORY_MISSING.value


def test_builders_return_named_blockers_instead_of_a_stub() -> None:
    ports, blockers = build_m04e_sql_ports(session_factory=None)
    assert ports is None
    assert any("m04e_backfill_session_factory_missing" in blocker for blocker in blockers)

    _fake, factory = session()
    ports, blockers = build_m04e_sql_ports(session_factory=factory, page_size_limit=0)
    assert ports is None
    assert any("m04e_page_size_invalid" in blocker for blocker in blockers)

    ports, blockers = build_m04e_sql_ports(session_factory=factory)
    assert blockers == ()
    assert ports is not None
    assert ports.page_size_limit == MAX_PAGE_SIZE
    assert ports.reader.page_size_limit == MAX_PAGE_SIZE
    assert ports.blockers == M04E_PORT_WIRING_BLOCKERS

    store_port, store_blockers = build_m04e_evidence_store(session_factory=None)
    assert store_port is None
    assert store_blockers[0].startswith("m04e_backfill_session_factory_missing")
    assert store_blockers[1:] == EVIDENCE_STORE_SCHEMA_GAPS

    # Was ``store_port is None`` with the schema gaps: that encoded "the ledger cannot
    # exist". The four tables are registered and revision ``2026110315`` creates them,
    # so the builder now returns a usable store — and still returns the *review* items,
    # because the M04E-01 promotion gate cannot record an accepted outcome yet. A
    # builder that dropped them would report the stage as unblocked.
    store_port, store_blockers = build_m04e_evidence_store(session_factory=factory)
    assert isinstance(store_port, SqlM04EEvidenceStore)
    assert store_blockers == EVIDENCE_STORE_SCHEMA_GAPS
    assert store_port.required_ddl == ()


@pytest.mark.parametrize(
    ("adapter", "methods"),
    [
        (SqlM04ECandidateReader, ("read_page",)),
        (SqlM04EUpstreamGate, ("prove_partition",)),
        (SqlM04ECandidateProof, ("prove_candidate",)),
        (SqlM04EEvidenceStore, (
            "find_existing", "find_observation", "record_blocked", "classify_and_commit_page",
        )),
    ],
)
def test_adapters_expose_the_declared_port_surface(
    adapter: type, methods: tuple[str, ...],
) -> None:
    for name in methods:
        assert inspect.iscoroutinefunction(getattr(adapter, name)), (
            f"{adapter.__name__}.{name} must be the declared async port method"
        )


# ---------------------------------------------------------------------------
# 7. Redaction and shared gate vocabulary
# ---------------------------------------------------------------------------


def test_a_refusal_is_falsy_so_it_cannot_be_mistaken_for_evidence() -> None:
    refusal = PortRefusal(
        code=M04EPortReason.M04_01_OWNER_REGISTRY_UNPROVEN,
        detail="the retained company row is not active",
        partition=(TENANT, DEFINITION_ID),
        gate_name=GATE_M04_01_OWNER_REGISTRY,
        correlation_id=CORRELATION_ID,
    )

    assert not refusal
    assert refusal.evidence_text().startswith(M04EPortReason.M04_01_OWNER_REGISTRY_UNPROVEN.value)
    assert GATE_M04_01_OWNER_REGISTRY in refusal.evidence_text()


async def test_no_payload_or_manifest_text_reaches_a_refusal_or_a_proof() -> None:
    _fake, factory = session(tables(companies=[company_row(status="paused")]))
    decision = await SqlM04EUpstreamGate(session_factory=factory).partition_gate(
        context=context(),
    )
    assert decision.refusal is not None
    texts = [decision.refusal.evidence_text(), *decision.refusal.as_blocker_evidence()]
    assert texts and all(SECRET_MARKER not in text for text in texts)

    # Was "the store refuses and its refusal must not quote the payload". The store now
    # answers, so the redaction claim has to be made about what it can *hold*: the four
    # ledger tables have no column capable of carrying a payload, a manifest or a
    # description, and a refusal names only codes and digests. Asserted on the columns
    # rather than on one emitted statement, so a future column is caught everywhere.
    from prodavan.infrastructure.persistence.models import Base

    for table in EVIDENCE_LEDGER_TABLES:
        columns = {column.name for column in Base.metadata.tables[table].columns}
        for forbidden in ("display_name", "description", "manifest", "payload", "body"):
            assert forbidden not in columns, f"{table} exposes a raw {forbidden!r} column"
    assert all(SECRET_MARKER not in gap for gap in EVIDENCE_STORE_SCHEMA_GAPS)

    fake, proof_factory = session()
    gates = await accepted_gate(fake, proof_factory)
    proof = await SqlM04ECandidateProof(session_factory=proof_factory).prove_candidate(
        context=context(), gates=gates,
        candidate=version_candidate(context(), version_row(), definition_row()),
    )
    assert proof is not None
    rendered = [repr(proof)] + [
        str(getattr(proof, field.name)) for field in dataclasses.fields(proof)
    ]
    assert all(SECRET_MARKER not in value for value in rendered)


async def test_the_gate_names_the_same_gates_the_handoff_vocabulary_uses() -> None:
    fake, factory = session(tables(promotion=[promotion_row(outcome="accepted")]))
    decision = await SqlM04EUpstreamGate(session_factory=factory).partition_gate(
        context=context(),
    )
    names = {name for name, _digest in decision.probes}

    assert names == {
        GATE_M04_01_OWNER_REGISTRY,
        GATE_M04_02_TYPED_SCOPE,
        GATE_M04E_01_DEFINITION_FOUNDATION,
    }
    # The M05 handoff compares the same reviewed gate names; a partition that is
    # accepted here still exports BLOCKED while no accepted evidence rows exist.
    export = export_m05_evidence(
        tenant_id=TENANT, app_definition_id=DEFINITION_ID, records=(),
        source_snapshot_digest=SNAPSHOT_DIGEST, checkpoint=checkpoint(), gates=(),
    )
    assert export.status.value == "blocked"
    assert fake.executed("FROM scope_registry")
