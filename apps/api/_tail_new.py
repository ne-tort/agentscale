# ---------------------------------------------------------------------------
# EvidenceStore — the durable per-source ledger (revision ``2026110315``)
# ---------------------------------------------------------------------------
#
# The four tables this adapter writes are registered models
# (``infrastructure/persistence/models/m04e_ledger.py``) created by revision
# ``2026110315_m04e_ledger.py``:
#
# * ``m04e_definition_version_mappings`` — the *first* mapping ever recorded for one
#   exact source identity ``(tenant_id, definition_id, source_kind, source_id,
#   source_schema_version)``, unique on that key, so a second row for an
#   already-mapped source cannot exist even if a writer asked for one;
# * ``m04e_definition_version_quarantine`` — one changed-digest *observation*,
#   unique on the source identity plus both observed digests with
#   ``NULLS NOT DISTINCT``, so ``find_observation`` is an exact lookup;
# * ``m04e_definition_version_checkpoints`` — the durable cursor of one
#   ``(tenant_id, definition_id, phase)`` stream: one head row, a globally unique
#   ``checkpoint_token`` and the monotonicity trigger;
# * ``m04e_definition_version_audit`` — the append-only, redacted per-page event
#   with balanced counters.
#
# What the store guarantees, and how
# ---------------------------------
# * **One transaction per call.** ``classify_and_commit_page`` opens exactly one
#   transaction: classification rows, quarantine observations, the checkpoint
#   advance and the audit event commit or roll back together. There is no
#   "write evidence, then advance the cursor" path.
# * **CAS on the observed cursor.** The head row is locked ``FOR UPDATE`` (a no-op
#   on SQLite) and the advance carries ``AND sequence = :observed AND
#   checkpoint_token = :observed``. ``rowcount == 0`` is *not* an exception: it is
#   the reviewed "stale cursor" answer, returned as the durable head after the
#   page's writes are rolled back.
# * **Replay does not move the cursor.** A page whose classifications all replay
#   writes nothing — no mapping row, no observation, no audit event, no CAS — and
#   returns the stored rows plus the stored head.
# * **A changed digest is never a rewrite.** A different ``source_digest`` (or
#   different verified artifact bytes) for an already-mapped source appends a
#   quarantine observation and returns the *first* mapping's source digest with
#   ``status=conflict``; the mapping row is never ``UPDATE``d. Replaying the same
#   observation returns the recorded row with ``outcome=replay``.
# * **Append-only.** No statement here is an ``UPDATE``/``DELETE`` against
#   ``..._mappings``, ``..._quarantine`` or ``..._audit``. The single ``UPDATE`` is
#   the checkpoint CAS on the one mutable head; the ORM guards and the
#   ``trg_m04e_ledger_append_only_*`` triggers back it up.
# * **Redaction.** Only identifiers, reason codes, sha256 digests, cursor tokens and
#   counters are written. ``proof_evidence`` receives *named digests*;
#   ``blocker_evidence`` receives short, single-line guidance strings that the
#   backfill contract itself produces.

#: The four registered ledger tables, exactly as the models name them.
EVIDENCE_MAPPING_LEDGER_TABLE: Final = "m04e_definition_version_mappings"
EVIDENCE_QUARANTINE_LEDGER_TABLE: Final = "m04e_definition_version_quarantine"
EVIDENCE_CHECKPOINT_LEDGER_TABLE: Final = "m04e_definition_version_checkpoints"
EVIDENCE_AUDIT_LEDGER_TABLE: Final = "m04e_definition_version_audit"

EVIDENCE_LEDGER_TABLES: Final[tuple[str, ...]] = (
    EVIDENCE_MAPPING_LEDGER_TABLE,
    EVIDENCE_QUARANTINE_LEDGER_TABLE,
    EVIDENCE_CHECKPOINT_LEDGER_TABLE,
    EVIDENCE_AUDIT_LEDGER_TABLE,
)

#: The four objects the port used to *require* and revision ``2026110315`` created.
#: Kept verbatim (and renamed from ``EVIDENCE_STORE_REQUIRED_DDL``): the ledger
#: revision's own contract test parses these column lists as the specification it
#: must satisfy, so the text stays the reviewed spec rather than being deleted along
#: with the blocker it used to explain.
EVIDENCE_STORE_LEDGER_DDL: Final[tuple[str, ...]] = (
    "m04e_definition_version_mappings (tenant_id, definition_id, source_kind, source_id, "
    "source_schema_version, source_digest, canonical_digest, canonical_identity, phase, "
    "status, reason_code, proof_evidence, artifact_digest, verified_artifact_digest, "
    "checkpoint, correlation_id) with uq (tenant, definition, source_kind, source_id, "
    "source_schema_version)",
    "m04e_definition_version_quarantine (…, observed_source_digest, "
    "observed_artifact_digest, reason_code, blocker_evidence, checkpoint) with "
    "uq (tenant, definition, source_kind, source_id, source_schema_version, "
    "observed_source_digest, observed_artifact_digest)",
    "m04e_definition_version_checkpoints (tenant_id, definition_id, phase, "
    "after_source_id, sequence, phase_complete, source_snapshot_id, "
    "source_schema_version, source_snapshot_digest, canonical_schema_version, "
    "writer_generation, checkpoint_token) read FOR UPDATE and advanced by CAS",
    "m04e_definition_version_audit (append-only redacted event stream, trusted writer "
    "privileges, UPDATE/DELETE denied)",
)

#: DDL the ``EvidenceStore`` port still lacks. **Empty**: the four ledger objects
#: above exist, are registered in ``Base.metadata`` and are what
#: :class:`SqlM04EEvidenceStore` reads and writes, so nothing is missing from the
#: schema for this boundary any more. The constant is kept (rather than deleted) so
#: a caller that reports "required DDL" has an honest, empty answer instead of a
#: stale list, and so the next gap has an obvious place to be recorded.
EVIDENCE_STORE_REQUIRED_DDL: Final[tuple[str, ...]] = ()

#: What still stops the *surrounding* M04E stage from claiming a page commit, now
#: that the evidence boundary itself is implemented. Each entry is a fact about a
#: table this task is not allowed to change, not about the ledger:
#:
#: * ``m04e_definition_version_promotion_evidence`` is still keyed
#:   ``(tenant_id, definition_id, revision_id)`` — one promotion revision per
#:   partition, not one row per mapped source — so it remains the *gate* record, not
#:   a per-source ledger;
#: * its ``ck_m04e_promotion_outcome`` still admits only ``('blocked',
#:   'quarantined')``, so ``SqlM04EUpstreamGate`` refuses ``MIG-M04E-01`` and no page
#:   reaches the store in production until a reviewed revision can record an
#:   accepted foundation promotion (the ledger *can* record ``accepted`` for its own
#:   rows — that is exactly why it exists);
#: * its four NOT NULL sha256 gate proofs are still unrepresentable for a blocked
#:   page, for the same reason.
#:
#: The first two used to be phrased as *missing ledger* gaps. That phrasing is gone:
#: the ledger exists, and repeating "no table carries the mapping key" would now be
#: false. What remains is the promotion-evidence/two-state-CHECK gap, which is a data
#: migration on a table owned by revision ``2026110302``.
EVIDENCE_STORE_SCHEMA_GAPS: Final[tuple[str, ...]] = (
    "m04e_promotion_evidence_is_per_partition: uq_m04e_promotion_revision is "
    "(tenant_id, definition_id, revision_id); it records one promotion revision for a "
    "partition, not one row per mapped source identity. The per-source ledger is "
    "m04e_definition_version_mappings and is not this table; the M04E-01 gate still "
    "reads the promotion record",
    "m04e_promotion_accepted_outcome_unrepresentable: ck_m04e_promotion_outcome permits "
    "only ('blocked','quarantined') on "
    "m04e_definition_version_promotion_evidence, so the M04E-01 gate cannot observe an "
    "accepted foundation promotion even though the ledger can record an accepted "
    "mapping. Closing it rewrites an applied CHECK (a data migration, not a schema fix)",
    "m04e_promotion_blocked_digests_unrepresentable: source_digest, target_digest, "
    "current_version_resolution_digest and artifact_equality_digest are NOT NULL "
    "sha256 gate proofs on the promotion-evidence table; a blocked page has no truthful "
    "value for them and inventing one would fabricate gate evidence",
)

#: Reason codes the store itself refuses with. They extend :class:`M04EPortReason` so
#: one grep finds every M04E refusal in a log.
STORE_FIRST_MAPPING_CONFLICT: Final = "m04e_backfill_first_mapping_conflict"
STORE_LEDGER_WRITE_REFUSED: Final = "m04e_backfill_ledger_write_refused"
STORE_OUTCOME_UNREPRESENTABLE: Final = "m04e_backfill_outcome_unrepresentable"
STORE_DECISION_INVALID: Final = "m04e_backfill_decision_invalid"

#: ``sha256:`` digest shape, identical to the backfill contract's own predicate.
_SHA256_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Longest guidance string a redacted ``blocker_evidence`` entry may carry.
_MAX_BLOCKER_LENGTH: Final = 400

#: Reasons a replayed observation may carry. Anything else recorded in the quarantine
#: ledger is not an observation of a changed digest, and answering it from
#: ``find_observation`` would fake a conflict replay.
_CONFLICT_REASONS: Final[frozenset[str]] = frozenset({
    "source_digest_changed", "artifact_digest_changed",
})

#: ``status`` column value -> the ``BackfillOutcome`` it records.
_OUTCOME_BY_STATUS: Final[Mapping[str, BackfillOutcome]] = {
    "accepted": BackfillOutcome.ACCEPTED,
    "replay": BackfillOutcome.REPLAY,
    "quarantined": BackfillOutcome.QUARANTINED,
    "conflict": BackfillOutcome.CONFLICT,
    "blocked": BackfillOutcome.BLOCKED,
}

#: The classification a *fresh* mapping row records for one decision status.
#: ``replay`` is absent on purpose: a replayed classification creates no row.
_FIRST_MAPPING_OUTCOME: Final[Mapping[EvidenceStatus, BackfillOutcome]] = {
    EvidenceStatus.ACCEPTED: BackfillOutcome.ACCEPTED,
    EvidenceStatus.QUARANTINED: BackfillOutcome.QUARANTINED,
    EvidenceStatus.CONFLICT: BackfillOutcome.CONFLICT,
}


class _PageCasLost(RuntimeError):
    """Internal: the checkpoint CAS matched no row. Never leaves the store."""


def _ledger_id(prefix: str) -> str:
    """``prefix_`` plus 24 hex characters — the models' ``String(80)`` id shape."""

    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _checkpoint_token() -> str:
    """A fresh, globally unique CAS token for exactly one cursor advance."""

    return f"m04e-cp:{uuid.uuid4().hex}"


def _start_marker(phase: str) -> str:
    """The cursor's own spelling when ``after_source_id`` is still empty.

    The ledger's ``checkpoint`` columns are non-empty by CHECK, and "no source
    consumed yet" is a real state that must be written down rather than as an empty
    string. ``m04e-start:<phase>`` says exactly that and cannot collide with a source
    identity, which the reader requires to be a non-empty id.
    """

    return f"m04e-start:{phase}"


def _source_predicate(
    table: Any, *, tenant_id: str, definition_id: str, source_kind: str,
    source_id: str, source_schema_version: str,
) -> Any:
    """The exact five-column source key as a SQL predicate."""

    return sa.and_(
        table.c.tenant_id == tenant_id,
        table.c.definition_id == definition_id,
        table.c.source_kind == source_kind,
        table.c.source_id == source_id,
        table.c.source_schema_version == source_schema_version,
    )


def _observation_predicate(
    table: Any, *, tenant_id: str, definition_id: str, source_kind: str,
    source_id: str, source_schema_version: str,
    observed_source_digest: str | None, observed_artifact_digest: str | None,
) -> Any:
    """The exact observation key.

    ``uq_m04e_quarantine_observation`` is ``NULLS NOT DISTINCT``, so an absent digest
    is a *value* for uniqueness. The predicate spells ``IS NULL`` explicitly instead
    of relying on ``= NULL``, which never matches.
    """

    def _equals(column: Any, value: str | None) -> Any:
        return column.is_(None) if value is None else column == value

    return sa.and_(
        _source_predicate(
            table,
            tenant_id=tenant_id, definition_id=definition_id, source_kind=source_kind,
            source_id=source_id, source_schema_version=source_schema_version,
        ),
        _equals(table.c.observed_source_digest, observed_source_digest),
        _equals(table.c.observed_artifact_digest, observed_artifact_digest),
    )


def _checkpoint_from_row(row: Mapping[str, Any]) -> BackfillCheckpoint:
    """Rebuild the contract's checkpoint value from one durable head row."""

    return BackfillCheckpoint(
        partition=(str(row["tenant_id"]), str(row["definition_id"])),
        phase=str(row["phase"]),
        after_source_id=(
            str(row["after_source_id"]) if row.get("after_source_id") is not None else None
        ),
        sequence=int(row["sequence"]),
        source_snapshot_id=str(row["source_snapshot_id"]),
        source_schema_version=str(row["source_schema_version"]),
        source_snapshot_digest=str(row["source_snapshot_digest"]),
        canonical_schema_version=str(row["canonical_schema_version"]),
        writer_generation=(
            int(row["writer_generation"]) if row.get("writer_generation") is not None else None
        ),
        phase_complete=bool(row["phase_complete"]),
    )


def _status_of(outcome: BackfillOutcome) -> EvidenceStatus:
    """The ``EvidenceStatus`` a row written with ``outcome`` reports back."""

    return {
        BackfillOutcome.ACCEPTED: EvidenceStatus.ACCEPTED,
        BackfillOutcome.QUARANTINED: EvidenceStatus.QUARANTINED,
        BackfillOutcome.CONFLICT: EvidenceStatus.CONFLICT,
    }[outcome]


def _proof_evidence(decision: CandidateDecision) -> dict[str, str]:
    """Named sha256 proof digests for one accepted classification.

    Mirrors ``run_page``'s own expectation exactly: ``owner`` / ``active_scope``,
    plus ``same_definition`` / ``immutable_artifact`` / ``artifact_content`` for a
    version, plus the four current-version proofs when one is linked. Only digests
    reach the column — no payload, manifest body, description or secret.
    """

    proof = decision.proof
    if proof is None:
        return {}
    evidence: dict[str, str] = {
        "owner": proof.owner_proof_digest,
        "active_scope": proof.active_scope_proof_digest,
    }
    candidate = decision.candidate
    if candidate.phase == "version":
        evidence["same_definition"] = proof.same_definition_proof_digest or ""
        evidence["immutable_artifact"] = proof.artifact_immutability_proof_digest or ""
        evidence["artifact_content"] = decision.verified_artifact_digest or ""
    if proof.current_version_id:
        evidence["current_version"] = proof.current_version_proof_digest or ""
        evidence["current_same_definition"] = (
            proof.current_version_same_definition_digest or ""
        )
        evidence["current_artifact_immutability"] = (
            proof.current_version_immutable_artifact_digest or ""
        )
        evidence["current_artifact_content"] = decision.verified_current_artifact_digest or ""
    return evidence


def _quarantine_guidance(reason_code: str | None) -> tuple[str, ...]:
    """The backfill contract's own guidance for a quarantine reason.

    The mapping ledger stores the *reason code*; the guidance is a pure function of
    it (``DefinitionVersionEvidenceBackfill._quarantine_guidance``). Calling that
    function (rather than copying its table) means a replayed quarantine returns
    exactly the guidance the first page returned, and the two can never drift.
    """

    return DefinitionVersionEvidenceBackfill._quarantine_guidance(reason_code)


def _payload_artifact_uri(candidate: EvidenceCandidate) -> str | None:
    """The artifact URI the candidate itself carries (a version only)."""

    if candidate.phase != "version":
        return None
    uri = candidate.payload.get("artifact_uri")
    return uri if isinstance(uri, str) and uri.strip() else None


def _payload_artifact_digest(candidate: EvidenceCandidate) -> str | None:
    """The source artifact digest the candidate itself carries (a version only)."""

    if candidate.phase != "version":
        return None
    digest = candidate.payload.get("artifact_digest")
    return digest if isinstance(digest, str) and digest.strip() else None


def _record_from_mapping_row(
    row: Mapping[str, Any], *, checkpoint: BackfillCheckpoint,
    outcome: BackfillOutcome, artifact_uri: str | None = None,
    current_artifact_digest: str | None = None,
) -> BackfillRecord:
    """Rebuild one ``BackfillRecord`` from a mapping ledger row, exactly.

    Everything the backfill contract compares is carried verbatim: the source
    identity and digest the row was written with, the canonical identity/digest it
    recorded (``None`` for a non-accepted row, which is what the contract demands),
    the artifact identity and the named proof digests.

    ``outcome=REPLAY`` reports the status the row *itself* records: a replayed
    acceptance is still an acceptance, a replayed quarantine still a quarantine. That
    is what ``run_page`` compares against, and collapsing it to a single "replay"
    status would lose the classification the row stands for.
    """

    identity = tuple(str(part) for part in (row.get("canonical_identity") or ()))
    if outcome is BackfillOutcome.REPLAY:
        status = _status_of(_OUTCOME_BY_STATUS.get(str(row["status"]), BackfillOutcome.QUARANTINED))
    else:
        status = _status_of(outcome)
    artifact_digest = row.get("artifact_digest")
    reason_code = str(row["reason_code"]) if row.get("reason_code") is not None else None
    return BackfillRecord(
        source_kind=str(row["source_kind"]),
        source_id=str(row["source_id"]),
        schema_version=str(row["source_schema_version"]),
        source_digest=str(row["source_digest"]),
        canonical_digest=(
            str(row["canonical_digest"]) if row.get("canonical_digest") is not None else None
        ),
        canonical_identity=identity,
        checkpoint=checkpoint,
        status=status,
        reason_code=reason_code,
        correlation_id=str(row["correlation_id"]),
        tenant_id=str(row["tenant_id"]),
        app_definition_id=str(row["definition_id"]),
        phase=str(row["phase"]),
        source_snapshot_digest=checkpoint.source_snapshot_digest,
        proof_evidence=tuple(sorted(
            (str(name), str(value))
            for name, value in (row.get("proof_evidence") or {}).items()
        )),
        artifact_uri=artifact_uri,
        artifact_digest=str(artifact_digest) if artifact_digest is not None else None,
        verified_artifact_digest=(
            str(row["verified_artifact_digest"])
            if row.get("verified_artifact_digest") is not None else None
        ),
        current_artifact_digest=current_artifact_digest,
        writer_generation=checkpoint.writer_generation,
        outcome=outcome,
        blocker_evidence=(
            _quarantine_guidance(reason_code)
            if status is EvidenceStatus.QUARANTINED else ()
        ),
    )


def _record_from_observation_row(
    row: Mapping[str, Any], *, checkpoint: BackfillCheckpoint, reason_code: str,
) -> BackfillRecord:
    """Rebuild the conflict record one quarantine observation stands for.

    The *original* source digest is the one the first mapping recorded (the
    quarantine row carries it), the observed digest is the one this observation
    recorded, and the canonical identity and digest stay empty: a conflicted source
    exposes no canonical claim.
    """

    return BackfillRecord(
        source_kind=str(row["source_kind"]),
        source_id=str(row["source_id"]),
        schema_version=str(row["source_schema_version"]),
        source_digest=str(row["source_digest"]),
        canonical_digest=None,
        canonical_identity=(),
        checkpoint=checkpoint,
        status=EvidenceStatus.CONFLICT,
        reason_code=reason_code,
        correlation_id=str(row["correlation_id"]),
        tenant_id=str(row["tenant_id"]),
        app_definition_id=str(row["definition_id"]),
        phase=str(row["phase"]),
        source_snapshot_digest=checkpoint.source_snapshot_digest,
        observed_source_digest=(
            str(row["observed_source_digest"])
            if row.get("observed_source_digest") is not None else None
        ),
        observed_artifact_digest=(
            str(row["observed_artifact_digest"])
            if row.get("observed_artifact_digest") is not None else None
        ),
        blocker_evidence=tuple(str(item) for item in (row.get("blocker_evidence") or ())),
        writer_generation=checkpoint.writer_generation,
        outcome=BackfillOutcome.CONFLICT,
    )


class SqlM04EEvidenceStore(_SessionReads):
    """Durable ``EvidenceStore`` over the four registered M04E ledger tables.

    See the section comment above for the invariants. The class holds no state
    between calls and no session: every method opens its own transaction through the
    injected ``async_sessionmaker`` and proves the transaction-local tenant context
    before reading or writing a row.

    ``allow_non_postgresql`` exists for the **test harness only**: the SQLite mirror in
    ``tests/migration/m04e_ports`` reproduces the four tables (JSONB -> JSON) so the
    page commit, replay, CAS conflict, quarantine and audit balance are executable,
    but SQLite has neither transaction-local GUCs nor RLS. The flag defaults to off,
    so a production composition root cannot run the ledger without the RLS binding:
    the first call raises a named refusal instead.
    """

    #: This is not a refusal-only boundary any more: it reads and writes the ledger.
    IS_FAIL_CLOSED_BOUNDARY: Final = False

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        require_serializable: bool = False,
        allow_non_postgresql: bool = False,
    ) -> None:
        super().__init__(
            session_factory=session_factory, require_serializable=require_serializable,
        )
        self._allow_non_postgresql = allow_non_postgresql

    @property
    def blockers(self) -> tuple[str, ...]:
        """Named gaps that still stop a *production* page commit from running."""

        return EVIDENCE_STORE_SCHEMA_GAPS

    @property
    def required_ddl(self) -> tuple[str, ...]:
        """DDL this boundary still lacks — empty; the ledger exists."""

        return EVIDENCE_STORE_REQUIRED_DDL

    @property
    def ledger_tables(self) -> tuple[str, ...]:
        return EVIDENCE_LEDGER_TABLES

    # -- transaction -------------------------------------------------------

    @asynccontextmanager
    async def _page(self, context: BackfillContext) -> AsyncIterator[AsyncSession]:
        """One transaction with a proven tenant context (or a proven mirror)."""

        require_usable_context(context)
        async with self._session_factory() as session:
            dialect = self._dialect_name(session)
            if dialect != "postgresql" and not self._allow_non_postgresql:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.TRANSACTION_ISOLATION_INSUFFICIENT,
                    "the M04E ledger requires PostgreSQL transaction-local tenant context "
                    "and row security; no other dialect can prove the partition",
                    context=context,
                ))
            async with session.begin():
                if dialect == "postgresql":
                    await bind_m04e_transaction_context(
                        session, context, require_serializable=self._require_serializable,
                    )
                yield session

    @staticmethod
    def _dialect_name(session: AsyncSession) -> str:
        bind = session.get_bind()
        return str(getattr(getattr(bind, "dialect", None), "name", "")) or "unknown"

    # -- reads -------------------------------------------------------------

    async def find_existing(
        self, *, context: BackfillContext, source_kind: str, source_id: str,
        source_schema_version: str,
    ) -> BackfillRecord | None:
        """The first mapping ever recorded for this exact source identity.

        ``None`` means the identity is genuinely unmapped: the lookup is the exact
        five-column key of ``uq_m04e_mapping_source`` inside this partition, so a row
        for the same source under another tenant/definition — or under another source
        schema version — cannot be mistaken for it.

        The record reports the stored *classification* (``accepted`` /
        ``quarantined`` / ``conflict``) with the immutable source digest; ``run_page``
        is the side that decides whether this page replays or conflicts.
        """

        self._require_source_key(
            context, source_kind=source_kind, source_id=source_id,
            source_schema_version=source_schema_version,
        )
        async with self._page(context) as session:
            row = await self._first_mapping(
                session, context,
                source_kind=source_kind, source_id=source_id,
                source_schema_version=source_schema_version,
            )
            if row is None:
                return None
            checkpoint = await self._durable_checkpoint(
                session, context, phase=str(row["phase"]),
            )
        return _record_from_mapping_row(
            row, checkpoint=checkpoint, outcome=self._stored_outcome(row),
        )

    async def find_observation(
        self, *, context: BackfillContext, source_kind: str, source_id: str,
        source_schema_version: str, observed_source_digest: str | None,
        observed_artifact_digest: str | None,
    ) -> BackfillRecord | None:
        """The exact changed-digest observation previously recorded, if any.

        Exactly one of the two observed digests must be named: the table's
        ``ck_m04e_quarantine_single_observation`` admits nothing else, and a caller
        naming both (or neither) asks an unanswerable question, so it is refused
        rather than answered with ``None``.
        """

        self._require_source_key(
            context, source_kind=source_kind, source_id=source_id,
            source_schema_version=source_schema_version,
        )
        if (observed_source_digest is None) == (observed_artifact_digest is None):
            raise M04EPortRefused(_refusal(
                M04EPortReason.STORE_OUTCOME_UNREPRESENTABLE,
                "an observation names exactly one observed digest (source xor artifact)",
                context=context,
            ))
        for digest in (observed_source_digest, observed_artifact_digest):
            if digest is not None and not _SHA256_RE.fullmatch(digest):
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_OUTCOME_UNREPRESENTABLE,
                    "an observed digest must be a sha256 digest",
                    context=context,
                ))
        async with self._page(context) as session:
            row = await self._observation_row(
                session, context, source_kind=source_kind, source_id=source_id,
                source_schema_version=source_schema_version,
                observed_source_digest=observed_source_digest,
                observed_artifact_digest=observed_artifact_digest,
            )
            if row is None:
                return None
            reason_code = str(row["reason_code"])
            if reason_code not in _CONFLICT_REASONS:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_OUTCOME_UNREPRESENTABLE,
                    "the recorded observation is not a changed source/artifact digest",
                    context=context,
                ))
            checkpoint = await self._durable_checkpoint(
                session, context, phase=str(row["phase"]),
            )
        return _record_from_observation_row(
            row, checkpoint=checkpoint, reason_code=reason_code,
        )

    # -- blocked evidence --------------------------------------------------

    async def record_blocked(
        self, *, context: BackfillContext, checkpoint: BackfillCheckpoint,
        reason_code: str, blocker_evidence: Sequence[str] = (),
    ) -> BackfillRecord:
        """Append one redacted ``page_blocked`` audit event; never move the cursor.

        A blocked page has no classification to write, so no mapping and no
        quarantine row is created: the audit event *is* the evidence — zero counters
        and a non-empty reason code, which is exactly what
        ``ck_m04e_audit_counters_balance`` and
        ``ck_m04e_audit_blocked_requires_reason`` require. The checkpoint the caller
        holds is echoed back untouched, which is what ``run_page`` compares.
        """

        self._require_checkpoint_lineage(context, checkpoint)
        if not isinstance(reason_code, str) or not reason_code.strip():
            raise M04EPortRefused(_refusal(
                M04EPortReason.STORE_LEDGER_WRITE_REFUSED,
                "a blocked page must name a redacted reason code",
                context=context,
            ))
        evidence = self._redacted_blockers(blocker_evidence)
        async with self._page(context) as session:
            stored = await self._checkpoint_row(session, context, phase=checkpoint.phase)
            sequence = int(stored["sequence"]) if stored is not None else checkpoint.sequence
            await session.execute(sa.insert(EVIDENCE_AUDIT_TABLE_MODEL.__table__).values(
                id=_ledger_id("m04eaudit"),
                event_id=_digest({
                    "tenant_id": context.tenant_id,
                    "app_definition_id": context.app_definition_id,
                    "correlation_id": context.correlation_id,
                    "transaction_id": context.transaction_id,
                    "event_kind": "page_blocked",
                    "phase": checkpoint.phase,
                    "sequence": sequence,
                    "reason_code": reason_code,
                }),
                tenant_id=context.tenant_id,
                definition_id=context.app_definition_id,
                phase=checkpoint.phase,
                event_kind="page_blocked",
                reason_code=reason_code,
                correlation_id=context.correlation_id,
                checkpoint=checkpoint.after_source_id or _start_marker(checkpoint.phase),
                sequence=sequence,
                actor=context.actor,
                transaction_id=context.transaction_id,
                record_count=0,
                accepted_count=0,
                replay_count=0,
                quarantined_count=0,
                conflict_count=0,
                blocker_evidence=list(evidence),
            ))
        return BackfillRecord(
            source_kind="m04e_backfill",
            source_id="blocked",
            schema_version=context.source_schema_version,
            source_digest=context.source_snapshot_digest,
            canonical_digest=None,
            canonical_identity=(),
            checkpoint=checkpoint,
            status=EvidenceStatus.BLOCKED,
            reason_code=reason_code,
            correlation_id=context.correlation_id,
            tenant_id=context.tenant_id,
            app_definition_id=context.app_definition_id,
            phase=checkpoint.phase,
            source_snapshot_digest=context.source_snapshot_digest,
            transaction_id=context.transaction_id,
            writer_generation=context.writer_generation,
            outcome=BackfillOutcome.BLOCKED,
            blocker_evidence=evidence,
        )

    # -- the page commit ----------------------------------------------------

    async def classify_and_commit_page(
        self, *, context: BackfillContext, gates: UpstreamGateEvidence,
        expected_checkpoint: BackfillCheckpoint, decisions: Sequence[CandidateDecision],
        page_exhausted: bool,
    ) -> BackfillBatch:
        """Append evidence + audit and CAS the cursor in **one** transaction.

        The order inside the transaction is what makes the invariants hold:

        1. the head row is locked and re-read; a head that is not the cursor the
           caller expected is a *stale cursor*, answered with the durable head and
           **no write at all**;
        2. each decision is classified against the ledger (first mapping, exact
           observation, canonical-identity duplicate) and the resulting rows are
           appended — never updated;
        3. the audit event is appended with counters derived from those rows;
        4. the checkpoint is advanced by CAS on the observed ``sequence`` **and**
           ``checkpoint_token``; a zero-row CAS rolls the whole page back and returns
           the durable head.

        A stale cursor is reported as ``BackfillBatch((), durable_head, ...)``: the
        reviewed answer is "nothing was written and here is where the stream really
        is", not an exception raised from inside a committed transaction.
        """

        self._require_checkpoint_lineage(context, expected_checkpoint)
        self._require_gates(context, gates)
        ordered = self._ordered_decisions(context, expected_checkpoint, decisions)
        try:
            async with self._page(context) as session:
                batch = await self._commit_page(
                    session, context=context, expected_checkpoint=expected_checkpoint,
                    decisions=ordered, page_exhausted=page_exhausted,
                )
        except _PageCasLost:
            async with self._page(context) as session:
                durable = await self._durable_checkpoint(
                    session, context, phase=expected_checkpoint.phase,
                )
            return BackfillBatch((), durable, durable.phase_complete)
        return batch

    async def _commit_page(
        self, session: AsyncSession, *, context: BackfillContext,
        expected_checkpoint: BackfillCheckpoint, decisions: Sequence[CandidateDecision],
        page_exhausted: bool,
    ) -> BackfillBatch:
        """The body of one page commit, inside the caller's transaction."""

        head = await self._lock_checkpoint_row(
            session, context, phase=expected_checkpoint.phase,
        )
        if head is not None and str(head["phase"]) != expected_checkpoint.phase:
            # The cursor already moved on to the next phase (definition ->
            # version). That is a stale cursor, and the durable head says so.
            durable = _checkpoint_from_row(head)
            return BackfillBatch((), durable, durable.phase_complete)
        if head is None:
            if expected_checkpoint.sequence != 0 or expected_checkpoint.after_source_id:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_CHECKPOINT_LEDGER_MISSING,
                    "no durable checkpoint head exists for this partition/phase; the "
                    "requested cursor cannot be replayed",
                    context=context,
                ))
            observed_sequence = 0
            observed_token: str | None = None
            durable = self._fresh_checkpoint(context, phase=expected_checkpoint.phase)
        else:
            observed_sequence = int(head["sequence"])
            observed_token = str(head["checkpoint_token"])
            durable = _checkpoint_from_row(head)

        if (
            observed_sequence != expected_checkpoint.sequence
            or durable.after_source_id != expected_checkpoint.after_source_id
            or durable.phase_complete != expected_checkpoint.phase_complete
        ):
            return BackfillBatch((), durable, durable.phase_complete)

        records, consumed = await self._classify(
            session, context=context, checkpoint=durable, decisions=decisions,
        )
        next_checkpoint = self._next_checkpoint(
            context, current=durable, decisions=decisions, consumed=consumed,
            page_exhausted=page_exhausted,
        )
        if next_checkpoint == durable:
            # An all-replay page (or an empty page that moves nothing) writes
            # nothing: no row, no audit event, no CAS.
            return BackfillBatch(tuple(records), durable, durable.phase_complete)

        token = _checkpoint_token()
        counters = self._counters(records)
        await session.execute(sa.insert(EVIDENCE_AUDIT_TABLE_MODEL.__table__).values(
            id=_ledger_id("m04eaudit"),
            event_id=_digest({
                "tenant_id": context.tenant_id,
                "app_definition_id": context.app_definition_id,
                "correlation_id": context.correlation_id,
                "transaction_id": context.transaction_id,
                "event_kind": "page_committed",
                "phase": durable.phase,
                "sequence": observed_sequence,
                "checkpoint_token": token,
            }),
            tenant_id=context.tenant_id,
            definition_id=context.app_definition_id,
            phase=durable.phase,
            event_kind="page_committed",
            reason_code=None,
            correlation_id=context.correlation_id,
            checkpoint=token,
            sequence=observed_sequence,
            actor=context.actor,
            transaction_id=context.transaction_id,
            record_count=counters["record_count"],
            accepted_count=counters["accepted_count"],
            replay_count=counters["replay_count"],
            quarantined_count=counters["quarantined_count"],
            conflict_count=counters["conflict_count"],
            blocker_evidence=[],
        ))

        table = EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__
        if head is None:
            created = await session.execute(sa.insert(table).values(
                id=_ledger_id("m04ecp"),
                tenant_id=context.tenant_id,
                definition_id=context.app_definition_id,
                phase=next_checkpoint.phase,
                after_source_id=next_checkpoint.after_source_id,
                sequence=next_checkpoint.sequence,
                phase_complete=next_checkpoint.phase_complete,
                source_snapshot_id=context.source_snapshot_id,
                source_schema_version=context.source_schema_version,
                source_snapshot_digest=context.source_snapshot_digest,
                canonical_schema_version=context.canonical_schema_version,
                writer_generation=context.writer_generation,
                checkpoint_token=token,
            ))
            if int(created.rowcount or 0) != 1:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_LEDGER_WRITE_REFUSED,
                    "the durable checkpoint head could not be created",
                    context=context,
                ))
        else:
            advanced = await session.execute(
                sa.update(table)
                .where(
                    sa.and_(
                        table.c.tenant_id == context.tenant_id,
                        table.c.definition_id == context.app_definition_id,
                        table.c.phase == durable.phase,
                        table.c.sequence == observed_sequence,
                        table.c.checkpoint_token == observed_token,
                    )
                )
                .values(
                    after_source_id=next_checkpoint.after_source_id,
                    sequence=next_checkpoint.sequence,
                    phase=next_checkpoint.phase,
                    phase_complete=next_checkpoint.phase_complete,
                    checkpoint_token=token,
                )
            )
            if int(advanced.rowcount or 0) != 1:
                # Lost the CAS: roll the page's evidence back and report the head.
                raise _PageCasLost()
        stamped = tuple(replace(record, checkpoint=next_checkpoint) for record in records)
        return BackfillBatch(stamped, next_checkpoint, next_checkpoint.phase_complete)

    # -- internals: rows ----------------------------------------------------

    async def _first_mapping(
        self, session: AsyncSession, context: BackfillContext, *,
        source_kind: str, source_id: str, source_schema_version: str,
    ) -> Mapping[str, Any] | None:
        rows = _rows(await session.execute(
            sa.select(EVIDENCE_MAPPING_TABLE_MODEL.__table__)
            .where(_source_predicate(
                EVIDENCE_MAPPING_TABLE_MODEL.__table__,
                tenant_id=context.tenant_id,
                definition_id=context.app_definition_id,
                source_kind=source_kind, source_id=source_id,
                source_schema_version=source_schema_version,
            ))
            .limit(1)
        ))
        return rows[0] if rows else None

    async def _observation_row(
        self, session: AsyncSession, context: BackfillContext, *,
        source_kind: str, source_id: str, source_schema_version: str,
        observed_source_digest: str | None, observed_artifact_digest: str | None,
    ) -> Mapping[str, Any] | None:
        rows = _rows(await session.execute(
            sa.select(EVIDENCE_QUARANTINE_TABLE_MODEL.__table__)
            .where(_observation_predicate(
                EVIDENCE_QUARANTINE_TABLE_MODEL.__table__,
                tenant_id=context.tenant_id,
                definition_id=context.app_definition_id,
                source_kind=source_kind, source_id=source_id,
                source_schema_version=source_schema_version,
                observed_source_digest=observed_source_digest,
                observed_artifact_digest=observed_artifact_digest,
            ))
            .limit(1)
        ))
        return rows[0] if rows else None

    async def _checkpoint_row(
        self, session: AsyncSession, context: BackfillContext, *, phase: str,
    ) -> Mapping[str, Any] | None:
        """The head row of one ``(tenant, definition, phase)`` stream, if any.

        The definition→version transition advances the *same* head row, so a drained
        definition stream reads as its version head: a caller holding the
        definition-phase cursor is not told "no ledger" once the phase moved.
        """

        table = EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__
        rows = _rows(await session.execute(
            sa.select(table)
            .where(sa.and_(
                table.c.tenant_id == context.tenant_id,
                table.c.definition_id == context.app_definition_id,
                table.c.phase == phase,
            ))
            .limit(1)
        ))
        if rows:
            return rows[0]
        if phase == "definition":
            return await self._checkpoint_row(session, context, phase="version")
        return None

    async def _durable_checkpoint(
        self, session: AsyncSession, context: BackfillContext, *, phase: str,
    ) -> BackfillCheckpoint:
        row = await self._checkpoint_row(session, context, phase=phase)
        return (
            _checkpoint_from_row(row) if row is not None
            else self._fresh_checkpoint(context, phase=phase)
        )

    async def _lock_checkpoint_row(
        self, session: AsyncSession, context: BackfillContext, *, phase: str,
    ) -> Mapping[str, Any] | None:
        """``SELECT ... FOR UPDATE`` of the head row (compiles away on SQLite).

        One row per ``(tenant, definition)`` is enough: the definition→version
        transition updates the same head, so locking by partition (without the phase
        predicate) is what makes a concurrent phase transition visible as a stale
        cursor instead of a lost update.
        """

        table = EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__
        rows = _rows(await session.execute(
            sa.select(table)
            .where(sa.and_(
                table.c.tenant_id == context.tenant_id,
                table.c.definition_id == context.app_definition_id,
            ))
            .order_by(table.c.phase)
            .with_for_update()
        ))
        if not rows:
            return None
        matching = [row for row in rows if str(row["phase"]) == phase]
        return matching[0] if matching else rows[0]

    # -- internals: classification -----------------------------------------

    def _ordered_decisions(
        self, context: BackfillContext, checkpoint: BackfillCheckpoint,
        decisions: Sequence[CandidateDecision],
    ) -> tuple[CandidateDecision, ...]:
        """Validate and order one page's classifications.

        The store refuses a page that is not about its own partition, whose
        candidates are outside the checkpoint lineage, that repeats a source
        identity, that carries a status the ledger cannot store (``blocked`` is a page
        event, not a classification) or that is not strictly ascending after the
        cursor. A page that cannot be ordered correctly must abort rather than commit
        a cursor that skips or re-reads rows.
        """

        if isinstance(decisions, (str, bytes)) or not isinstance(decisions, Sequence):
            raise M04EPortRefused(_refusal(
                M04EPortReason.STORE_DECISION_INVALID,
                "decisions must be a sequence of CandidateDecision",
                context=context,
            ))
        previous: str | None = None
        seen: set[tuple[str, str, str]] = set()
        for decision in decisions:
            if not isinstance(decision, CandidateDecision):
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_DECISION_INVALID,
                    "every page entry must be a CandidateDecision",
                    context=context,
                ))
            candidate = decision.candidate
            if (candidate.tenant_id, candidate.app_definition_id) != (
                context.tenant_id, context.app_definition_id,
            ):
                raise M04EPortRefused(_refusal(
                    M04EPortReason.PARTITION_MISMATCH,
                    "a candidate belongs to another tenant/definition partition",
                    context=context,
                ))
            if candidate.phase != checkpoint.phase:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.PARTITION_MISMATCH,
                    "a candidate belongs to another backfill phase than the cursor",
                    context=context,
                ))
            if (
                candidate.schema_version != context.source_schema_version
                or candidate.canonical_schema_version != context.canonical_schema_version
            ):
                raise M04EPortRefused(_refusal(
                    M04EPortReason.PARTITION_MISMATCH,
                    "a candidate is outside the checkpoint schema lineage",
                    context=context,
                ))
            if decision.status not in _FIRST_MAPPING_OUTCOME and (
                decision.status is not EvidenceStatus.REPLAY
            ):
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_OUTCOME_UNREPRESENTABLE,
                    "a blocked page is audit evidence, not a per-source classification; "
                    "use record_blocked",
                    context=context,
                ))
            key = (candidate.source_kind, candidate.source_id, candidate.schema_version)
            if key in seen:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_DECISION_INVALID,
                    "a page repeats one source identity",
                    context=context,
                ))
            seen.add(key)
            cursor = checkpoint.after_source_id
            if cursor is not None and candidate.source_id <= cursor:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_DECISION_INVALID,
                    "a candidate is at or before the durable cursor",
                    context=context,
                ))
            if previous is not None and candidate.source_id <= previous:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_DECISION_INVALID,
                    "the page is not strictly ascending by source identity",
                    context=context,
                ))
            previous = candidate.source_id
        return tuple(decisions)

    async def _classify(
        self, session: AsyncSession, *, context: BackfillContext,
        checkpoint: BackfillCheckpoint, decisions: Sequence[CandidateDecision],
    ) -> tuple[list[BackfillRecord], list[str]]:
        """Turn classifications into ledger rows (append-only); collect the cursor."""

        records: list[BackfillRecord] = []
        consumed: list[str] = []
        seen: set[tuple[str, ...]] = set()
        for decision in decisions:
            record, consumes = await self._classify_one(
                session, context=context, checkpoint=checkpoint,
                decision=decision, seen=seen,
            )
            if decision.candidate.canonical_identity:
                seen.add(decision.candidate.canonical_identity)
            if consumes:
                consumed.append(decision.candidate.source_id)
            records.append(record)
        return records, consumed

    async def _classify_one(
        self, session: AsyncSession, *, context: BackfillContext,
        checkpoint: BackfillCheckpoint, decision: CandidateDecision,
        seen: set[tuple[str, ...]],
    ) -> tuple[BackfillRecord, bool]:
        """One decision against the ledger; the record and whether it consumed a slot."""

        candidate = decision.candidate
        existing = await self._first_mapping(
            session, context,
            source_kind=candidate.source_kind, source_id=candidate.source_id,
            source_schema_version=candidate.schema_version,
        )
        verified = decision.verified_artifact_digest
        current_digest = decision.verified_current_artifact_digest
        artifact_uri = _payload_artifact_uri(candidate)

        if existing is not None:
            stored_source_digest = str(existing["source_digest"])
            stored_verified = existing.get("verified_artifact_digest")
            # 1. A changed source digest: append (or replay) an exact observation.
            #    The first mapping stays byte-identical — that is the point of the
            #    conflict branch.
            if stored_source_digest != candidate.source_digest:
                return await self._conflict_record(
                    session, context=context, checkpoint=checkpoint,
                    decision=decision, recorded_source_digest=stored_source_digest,
                    observed_source_digest=candidate.source_digest,
                    observed_artifact_digest=None,
                    reason_code="source_digest_changed",
                )
            # 2. Same source digest, different verified artifact bytes: an immutable
            #    artifact conflict, recorded as an artifact observation.
            if (
                stored_verified is not None
                and verified is not None
                and str(stored_verified) != verified
            ):
                return await self._conflict_record(
                    session, context=context, checkpoint=checkpoint,
                    decision=decision, recorded_source_digest=stored_source_digest,
                    observed_source_digest=None,
                    observed_artifact_digest=verified,
                    reason_code="artifact_digest_changed",
                )
            stored_outcome = self._stored_outcome(existing)
            if self._is_exact_replay(
                existing, decision=decision, stored_outcome=stored_outcome,
            ):
                if stored_outcome is BackfillOutcome.CONFLICT:
                    # A stored conflict (a canonical-identity conflict, which has no
                    # observation to replay) is re-reported as itself and consumes
                    # its slot: ``run_page`` refuses to call a non-digest conflict a
                    # ``replay``, and inventing a replay would be the store lying
                    # about which outcome it holds.
                    return _record_from_mapping_row(
                        existing, checkpoint=checkpoint, outcome=BackfillOutcome.CONFLICT,
                        artifact_uri=artifact_uri, current_artifact_digest=current_digest,
                    ), True
                # An exact replay of the recorded classification: nothing is written
                # and the cursor does not move.
                return _record_from_mapping_row(
                    existing, checkpoint=checkpoint, outcome=BackfillOutcome.REPLAY,
                    artifact_uri=artifact_uri, current_artifact_digest=current_digest,
                ), False
            raise M04EPortRefused(_refusal(
                M04EPortReason.STORE_FIRST_MAPPING_CONFLICT,
                "the source identity is already mapped with a different classification; "
                "a first mapping is immutable and a changed digest is an observation",
                context=context,
                blockers=(
                    f"m04e_ledger_status={existing['status']}",
                    f"m04e_incoming_status={decision.status.value}",
                ),
            ))

        # 3. A first mapping for a source identity the ledger has never seen.
        return await self._append_first_mapping(
            session, context=context, checkpoint=checkpoint, decision=decision,
            seen=seen, artifact_uri=artifact_uri, verified=verified,
            current_digest=current_digest,
        )

    async def _conflict_record(
        self, session: AsyncSession, *, context: BackfillContext,
        checkpoint: BackfillCheckpoint, decision: CandidateDecision,
        recorded_source_digest: str, observed_source_digest: str | None,
        observed_artifact_digest: str | None, reason_code: str,
    ) -> tuple[BackfillRecord, bool]:
        """Append or replay one exact observation and return its conflict record."""

        candidate = decision.candidate
        observation = await self._observation_row(
            session, context,
            source_kind=candidate.source_kind, source_id=candidate.source_id,
            source_schema_version=candidate.schema_version,
            observed_source_digest=observed_source_digest,
            observed_artifact_digest=observed_artifact_digest,
        )
        if observation is None:
            values: Mapping[str, Any] = {
                "id": _ledger_id("m04equar"),
                "tenant_id": context.tenant_id,
                "definition_id": context.app_definition_id,
                "phase": candidate.phase,
                "source_kind": candidate.source_kind,
                "source_id": candidate.source_id,
                "source_schema_version": candidate.schema_version,
                "source_digest": recorded_source_digest,
                "observed_source_digest": observed_source_digest,
                "observed_artifact_digest": observed_artifact_digest,
                # The store's own reason: it compared the digests itself and never
                # takes the caller's word for which one changed.
                "reason_code": reason_code,
                "blocker_evidence": list(self._redacted_blockers(decision.blocker_evidence)),
                "checkpoint": str(
                    checkpoint.after_source_id or _start_marker(candidate.phase)
                ),
                "status": "open",
                "correlation_id": context.correlation_id,
            }
            await session.execute(
                sa.insert(EVIDENCE_QUARANTINE_TABLE_MODEL.__table__).values(**dict(values))
            )
            observation = values
        record = _record_from_observation_row(
            observation, checkpoint=checkpoint, reason_code=reason_code,
        )
        if decision.status is EvidenceStatus.REPLAY:
            # The observation was already recorded: the page re-reports it as a replay
            # with the originally recorded evidence, and nothing new is written.
            return replace(
                record, outcome=BackfillOutcome.REPLAY, status=EvidenceStatus.CONFLICT,
            ), False
        return record, True

    async def _append_first_mapping(
        self, session: AsyncSession, *, context: BackfillContext,
        checkpoint: BackfillCheckpoint, decision: CandidateDecision,
        seen: set[tuple[str, ...]], artifact_uri: str | None, verified: str | None,
        current_digest: str | None,
    ) -> tuple[BackfillRecord, bool]:
        """Insert the first mapping row for one source identity."""

        candidate = decision.candidate
        outcome = _FIRST_MAPPING_OUTCOME.get(decision.status, BackfillOutcome.QUARANTINED)
        reason = decision.reason_code
        identity = candidate.canonical_identity
        if outcome is BackfillOutcome.ACCEPTED and identity in seen:
            # Two sources cannot both own one canonical row: the second is a
            # conflict, not an acceptance.
            outcome, reason = BackfillOutcome.CONFLICT, "duplicate_canonical_identity"
        canonical_digest = (
            candidate.canonical_digest if outcome is BackfillOutcome.ACCEPTED else None
        )
        if outcome is BackfillOutcome.ACCEPTED and canonical_digest is None:
            raise M04EPortRefused(_refusal(
                M04EPortReason.STORE_OUTCOME_UNREPRESENTABLE,
                "an accepted classification requires a canonical identity and digest",
                context=context,
            ))
        proof = _proof_evidence(decision) if outcome is BackfillOutcome.ACCEPTED else {}
        artifact_digest = _payload_artifact_digest(candidate)
        await session.execute(sa.insert(EVIDENCE_MAPPING_TABLE_MODEL.__table__).values(
            id=_ledger_id("m04emap"),
            tenant_id=context.tenant_id,
            definition_id=context.app_definition_id,
            source_kind=candidate.source_kind,
            source_id=candidate.source_id,
            source_schema_version=candidate.schema_version,
            phase=candidate.phase,
            status=outcome.value,
            source_digest=candidate.source_digest,
            canonical_digest=canonical_digest,
            canonical_identity=list(identity),
            reason_code=reason,
            proof_evidence=proof,
            artifact_digest=artifact_digest,
            verified_artifact_digest=verified,
            # The cursor the row was committed under. The head row carries the token
            # this page advanced to, which is the join key a reviewer uses.
            checkpoint=str(checkpoint.after_source_id or _start_marker(candidate.phase)),
            correlation_id=context.correlation_id,
        ))
        return BackfillRecord(
            source_kind=candidate.source_kind,
            source_id=candidate.source_id,
            schema_version=candidate.schema_version,
            source_digest=candidate.source_digest,
            canonical_digest=canonical_digest,
            canonical_identity=identity,
            checkpoint=checkpoint,
            status=_status_of(outcome),
            reason_code=reason,
            correlation_id=context.correlation_id,
            tenant_id=context.tenant_id,
            app_definition_id=context.app_definition_id,
            phase=candidate.phase,
            source_snapshot_digest=context.source_snapshot_digest,
            proof_evidence=tuple(sorted(proof.items())),
            artifact_uri=artifact_uri,
            artifact_digest=artifact_digest,
            verified_artifact_digest=verified,
            current_artifact_digest=current_digest,
            transaction_id=context.transaction_id,
            writer_generation=context.writer_generation,
            outcome=outcome,
            blocker_evidence=(
                _quarantine_guidance(reason)
                if outcome is BackfillOutcome.QUARANTINED else ()
            ),
        ), True

    # -- internals: cursor --------------------------------------------------

    def _next_checkpoint(
        self, context: BackfillContext, *, current: BackfillCheckpoint,
        decisions: Sequence[CandidateDecision], consumed: Sequence[str],
        page_exhausted: bool,
    ) -> BackfillCheckpoint:
        """The cursor the backfill contract expects for this page.

        Mirrors ``run_page``'s own derivation exactly: one sequence step per consumed
        source identity; an exhausted ``definition`` stream whose *last* row was
        consumed transitions to ``version`` with an empty cursor and one extra step;
        an exhausted ``version`` stream completes without moving past its last
        consumed row. A page that replays everything moves nothing — the caller
        returns early in that case, so this function is only reached when something
        actually moved.
        """

        phase = current.phase
        after = current.after_source_id
        sequence = current.sequence + len(consumed)
        complete = False
        last_row = decisions[-1].candidate.source_id if decisions else None
        last_consumed = consumed[-1] if consumed else None
        tail_reached = not decisions or (
            last_consumed is not None and last_consumed == last_row
        )
        if page_exhausted:
            if phase == "definition":
                if tail_reached:
                    phase, after = "version", None
                    sequence += 1
            elif tail_reached:
                after = last_consumed if last_consumed is not None else current.after_source_id
                complete = True
        if last_consumed is not None and phase == current.phase:
            after = last_consumed
        return BackfillCheckpoint(
            partition=current.partition,
            phase=phase,
            after_source_id=after,
            sequence=sequence,
            source_snapshot_id=current.source_snapshot_id,
            source_schema_version=current.source_schema_version,
            source_snapshot_digest=current.source_snapshot_digest,
            canonical_schema_version=current.canonical_schema_version,
            writer_generation=current.writer_generation,
            phase_complete=complete,
        )

    def _fresh_checkpoint(
        self, context: BackfillContext, *, phase: str,
    ) -> BackfillCheckpoint:
        """The cursor of a stream that has no durable head yet."""

        return BackfillCheckpoint(
            partition=(context.tenant_id, context.app_definition_id),
            phase=phase,
            after_source_id=None,
            sequence=0,
            source_snapshot_id=context.source_snapshot_id,
            source_schema_version=context.source_schema_version,
            source_snapshot_digest=context.source_snapshot_digest,
            canonical_schema_version=context.canonical_schema_version,
            writer_generation=context.writer_generation,
        )

    def _require_checkpoint_lineage(
        self, context: BackfillContext, checkpoint: BackfillCheckpoint,
    ) -> None:
        if not isinstance(checkpoint, BackfillCheckpoint):
            raise M04EPortRefused(_refusal(
                M04EPortReason.PARTITION_MISMATCH,
                "a BackfillCheckpoint is required",
                context=context,
            ))
        if checkpoint.partition != (context.tenant_id, context.app_definition_id):
            raise M04EPortRefused(_refusal(
                M04EPortReason.PARTITION_MISMATCH,
                "the checkpoint belongs to another partition",
                context=context,
            ))
        if (
            checkpoint.source_snapshot_id != context.source_snapshot_id
            or checkpoint.source_schema_version != context.source_schema_version
            or checkpoint.source_snapshot_digest != context.source_snapshot_digest
            or checkpoint.canonical_schema_version != context.canonical_schema_version
            or checkpoint.writer_generation != context.writer_generation
        ):
            raise M04EPortRefused(_refusal(
                M04EPortReason.PARTITION_MISMATCH,
                "the checkpoint is bound to another snapshot or schema lineage",
                context=context,
            ))

    @staticmethod
    def _require_gates(context: BackfillContext, gates: UpstreamGateEvidence) -> None:
        if not isinstance(gates, UpstreamGateEvidence) or not gates.valid:
            raise M04EPortRefused(_refusal(
                M04EPortReason.GATE_CONTEXT_INVALID,
                "a page commit requires valid upstream gate evidence",
                context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
            ))
        if (
            gates.tenant_id != context.tenant_id
            or gates.app_definition_id != context.app_definition_id
            or gates.source_snapshot_digest != context.source_snapshot_digest
            or gates.correlation_id != context.correlation_id
        ):
            raise M04EPortRefused(_refusal(
                M04EPortReason.GATE_CONTEXT_INVALID,
                "the gate evidence belongs to another partition, snapshot or correlation",
                context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
            ))

    @staticmethod
    def _require_source_key(
        context: BackfillContext, *, source_kind: str, source_id: str,
        source_schema_version: str,
    ) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (source_kind, source_id, source_schema_version)
        ):
            raise M04EPortRefused(_refusal(
                M04EPortReason.STORE_DECISION_INVALID,
                "the source identity and schema version are required",
                context=context,
            ))

    @staticmethod
    def _stored_outcome(row: Mapping[str, Any]) -> BackfillOutcome:
        return _OUTCOME_BY_STATUS.get(str(row["status"]), BackfillOutcome.QUARANTINED)

    @staticmethod
    def _is_exact_replay(
        row: Mapping[str, Any], *, decision: CandidateDecision,
        stored_outcome: BackfillOutcome,
    ) -> bool:
        """Is this decision a replay of exactly what the row records?

        Same source digest (the caller already compared it), same verified artifact
        bytes, same reason code, and either the caller says ``REPLAY`` or its status
        is the one the row already carries. Anything else must not be silently
        accepted as a replay, which is why this predicate is the only path that
        returns a stored row instead of appending one.
        """

        stored_verified = row.get("verified_artifact_digest")
        if (str(stored_verified) if stored_verified is not None else None) != (
            decision.verified_artifact_digest
        ):
            return False
        stored_reason = row.get("reason_code")
        if (str(stored_reason) if stored_reason is not None else None) != decision.reason_code:
            return False
        if decision.status is EvidenceStatus.REPLAY:
            return True
        return stored_outcome == _FIRST_MAPPING_OUTCOME.get(
            decision.status, BackfillOutcome.QUARANTINED
        )

    @staticmethod
    def _counters(records: Sequence[BackfillRecord]) -> dict[str, int]:
        """Audit counters derived from the rows actually written, never from input."""

        counters = {
            "accepted_count": 0, "replay_count": 0,
            "quarantined_count": 0, "conflict_count": 0,
        }
        for record in records:
            if record.outcome is BackfillOutcome.ACCEPTED:
                counters["accepted_count"] += 1
            elif record.outcome is BackfillOutcome.REPLAY:
                counters["replay_count"] += 1
            elif record.outcome is BackfillOutcome.QUARANTINED:
                counters["quarantined_count"] += 1
            elif record.outcome is BackfillOutcome.CONFLICT:
                counters["conflict_count"] += 1
        counters["record_count"] = sum(
            counters[key] for key in (
                "accepted_count", "replay_count", "quarantined_count", "conflict_count",
            )
        )
        return counters

    @staticmethod
    def _redacted_blockers(blocker_evidence: Sequence[str]) -> tuple[str, ...]:
        """Keep only short, single-line guidance strings.

        The backfill contract's guidance and gate names pass; anything that looks
        like a payload (a newline, an over-long body) is dropped rather than stored.
        A quarantine row's ``blocker_evidence`` is evidence a reviewer reads — it must
        never become a body excerpt.
        """

        kept: list[str] = []
        for item in blocker_evidence or ():
            if not isinstance(item, str):
                continue
            text = " ".join(item.split())
            if not text or text != item.strip() or len(text) > _MAX_BLOCKER_LENGTH:
                continue
            kept.append(text)
        return tuple(kept)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

#: Review items a composition root must keep reporting while they are open. They are
#: *not* missing tables: the evidence store below exists and writes the ledger.
M04E_PORT_WIRING_BLOCKERS: Final[tuple[str, ...]] = (
    "m04e_backfill_tenant_context_binding_unreviewed: the adapters apply and read back "
    "the transaction-local tenant GUCs and set app.migration_role='true'; the database "
    "GRANT/append-only privilege review for that role is still open",
    "m04e_positive_promotion_gate_unrepresentable: ck_m04e_promotion_outcome admits only "
    "('blocked','quarantined'), so SqlM04EUpstreamGate refuses MIG-M04E-01 by name until "
    "a reviewed revision can record an accepted M04E foundation promotion; no page "
    "reaches the ledger in production until then",
)


@dataclass(frozen=True, slots=True)
class M04ESqlPorts:
    """The M04E reader/gate/proof ports, ready for the composition root.

    The evidence store is a separate port with its own builder —
    :func:`build_m04e_evidence_store` — because it carries the ledger's own review
    blockers and must never be substituted by an in-memory store.
    """

    reader: CandidateReader
    gates: UpstreamGatePort
    proofs: CandidateProofPort
    page_size_limit: int
    blockers: tuple[str, ...]


def build_m04e_sql_ports(
    *, session_factory: async_sessionmaker[AsyncSession] | None,
    page_size_limit: int = MAX_PAGE_SIZE, require_serializable: bool = False,
) -> tuple[M04ESqlPorts | None, tuple[str, ...]]:
    """Build the reader/gate/proof adapters, or explain why they cannot be built."""

    if session_factory is None:
        return None, (
            "m04e_backfill_session_factory_missing: the M04E ports read PostgreSQL "
            "through an async_sessionmaker; there is no in-memory candidate source",
        )
    if not isinstance(page_size_limit, int) or not 1 <= page_size_limit <= MAX_PAGE_SIZE:
        return None, (
            f"m04e_page_size_invalid: page_size_limit {page_size_limit!r} is outside 1..{MAX_PAGE_SIZE}",
        )
    return (
        M04ESqlPorts(
            reader=SqlM04ECandidateReader(
                session_factory=session_factory,
                page_size_limit=page_size_limit,
                require_serializable=require_serializable,
            ),
            gates=SqlM04EUpstreamGate(
                session_factory=session_factory,
                require_serializable=require_serializable,
            ),
            proofs=SqlM04ECandidateProof(
                session_factory=session_factory,
                require_serializable=require_serializable,
            ),
            page_size_limit=page_size_limit,
            blockers=M04E_PORT_WIRING_BLOCKERS,
        ),
        (),
    )


def build_m04e_evidence_store(
    *, session_factory: async_sessionmaker[AsyncSession] | None,
    require_serializable: bool = False,
) -> tuple[EvidenceStore | None, tuple[str, ...]]:
    """Return the durable evidence store, or ``(None, blockers)``.

    The store **is** built when a session factory exists: the four ledger tables are
    registered in ``Base.metadata`` and created by revision ``2026110315``, so the
    missing-table gaps that used to make this boundary impossible are closed and
    :data:`EVIDENCE_STORE_REQUIRED_DDL` is empty.

    What is still returned alongside the object are the review items in
    :data:`EVIDENCE_STORE_SCHEMA_GAPS` (the per-partition promotion record, its
    two-state CHECK and its NOT NULL gate proofs). They stop the M04E-01 *gate*, not
    the ledger: returning ``None`` here would throw away a working store and pretend
    the ledger did not exist, while returning the store with no blockers would claim a
    page can commit in production today. Both lists are reported so the caller can see
    the object it got and the review it still owes.
    """

    if session_factory is None:
        return None, (
            "m04e_backfill_session_factory_missing: the evidence boundary reads "
            "PostgreSQL; there is no in-memory store on the production path",
            *EVIDENCE_STORE_SCHEMA_GAPS,
        )
    store = SqlM04EEvidenceStore(
        session_factory=session_factory, require_serializable=require_serializable,
    )
    return store, store.blockers


__all__ = [
    "CABINET_ANCHOR_TABLE",
    "CABINET_GRANT_TABLE",
    "CABINET_TABLE",
    "COMPANY_TABLE",
    "CONTEXT_ROLE",
    "CandidateProofDecision",
    "EVIDENCE_AUDIT_LEDGER_TABLE",
    "EVIDENCE_CHECKPOINT_LEDGER_TABLE",
    "EVIDENCE_LEDGER_TABLES",
    "EVIDENCE_MAPPING_LEDGER_TABLE",
    "EVIDENCE_QUARANTINE_LEDGER_TABLE",
    "EVIDENCE_STORE_LEDGER_DDL",
    "EVIDENCE_STORE_REQUIRED_DDL",
    "EVIDENCE_STORE_SCHEMA_GAPS",
    "EvidenceStoreUnavailable",
    "GATE_ACTIVE_TYPED_PARENT_SCOPE",
    "GATE_M04E_01_DEFINITION_FOUNDATION",
    "GATE_M04_01_OWNER_REGISTRY",
    "GATE_M04_02_TYPED_SCOPE",
    "GateDecision",
    "LEGACY_DEFINITION_SOURCE_KIND",
    "LEGACY_DEFINITION_TABLE",
    "LEGACY_VERSION_SOURCE_KIND",
    "LEGACY_VERSION_TABLE",
    "M04EPortReason",
    "M04EPortRefused",
    "M04ESqlPorts",
    "M04E_PORT_WIRING_BLOCKERS",
    "MAPPING_TABLE",
    "MAX_PAGE_SIZE",
    "OwnerEvidence",
    "PROMOTION_EVIDENCE_TABLE",
    "PROJECT_ANCHOR_TABLE",
    "PROJECT_TABLE",
    "PortRefusal",
    "PromotionEvidence",
    "REGISTRY_TABLE",
    "SOURCE_KIND_BY_PHASE",
    "STORE_DECISION_INVALID",
    "STORE_FIRST_MAPPING_CONFLICT",
    "STORE_LEDGER_WRITE_REFUSED",
    "STORE_OUTCOME_UNREPRESENTABLE",
    "SqlM04ECandidateProof",
    "SqlM04ECandidateReader",
    "SqlM04EEvidenceStore",
    "SqlM04EUpstreamGate",
    "TARGET_DEFINITION_TABLE",
    "TARGET_VERSION_TABLE",
    "TENANT_ANCHOR_TABLE",
    "TypedScopeEvidence",
    "bind_m04e_transaction_context",
    "build_m04e_evidence_store",
    "build_m04e_sql_ports",
    "definition_candidate",
    "legacy_status_to_state",
    "read_owner_evidence",
    "read_promotion_evidence",
    "read_typed_scope_evidence",
    "require_partition",
    "require_usable_context",
    "version_candidate",
]
