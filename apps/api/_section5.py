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
    assert params == {"tenant_id": TENANT, "definition_id": DEFINITION_ID}


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


