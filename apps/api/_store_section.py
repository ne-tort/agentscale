# ---------------------------------------------------------------------------
# EvidenceStore — the durable per-source ledger (revision ``2026110315``)
# ---------------------------------------------------------------------------
#
# The four tables this adapter writes exist as registered models
# (``infrastructure/persistence/models/m04e_ledger.py``) and were created by
# revision ``2026110315_m04e_ledger.py``:
#
# * ``m04e_definition_version_mappings`` — the *first* mapping ever recorded for
#   one exact source identity ``(tenant_id, definition_id, source_kind,
#   source_id, source_schema_version)``, unique on that key, so a second row for
#   an already-mapped source cannot exist even if a writer asked for one;
# * ``m04e_definition_version_quarantine`` — one changed-digest *observation*,
#   unique on the source identity plus both observed digests with
#   ``NULLS NOT DISTINCT``, so ``find_observation`` is an exact lookup;
# * ``m04e_definition_version_checkpoints`` — the durable cursor of one
#   ``(tenant_id, definition_id, phase)`` stream: a single head row, a globally
#   unique ``checkpoint_token`` and the monotonicity trigger;
# * ``m04e_definition_version_audit`` — the append-only, redacted per-page event
#   with balanced counters.
#
# What the store guarantees, and how
# ---------------------------------
# * **One transaction per call.** ``classify_and_commit_page`` opens exactly one
#   transaction: the classification rows, the quarantine observations, the
#   checkpoint advance and the audit event commit or roll back together. There is
#   no "write evidence, then advance the cursor" path.
# * **Exactly-one-head checkpoint.** The head row is locked ``FOR UPDATE`` (a
#   no-op on SQLite, real on PostgreSQL), the observed ``sequence`` and
#   ``checkpoint_token`` are re-read, and the advance is a CAS: the ``UPDATE``
#   carries ``AND sequence = :observed AND checkpoint_token = :observed``.
#   ``rowcount == 0`` is not an exception on the CAS path — it is the reviewed
#   "stale cursor" answer, returned as the *durable* head so the caller sees where
#   the stream really is; nothing is written in that transaction.
# * **Replay does not move the cursor.** A page whose classifications are all
#   ``REPLAY`` performs no insert and no CAS: it returns the stored rows and the
#   stored checkpoint (``run_page`` then validates monotonicity itself).
# * **A changed digest is never a rewrite.** A different ``source_digest`` (or
#   verified artifact bytes) for an already-mapped source appends a quarantine
#   observation and returns the *first* mapping's identity with
#   ``status=conflict``/``outcome=conflict`` — the original mapping row is never
#   ``UPDATE``d. The same observation replayed returns the recorded row with
#   ``outcome=replay``.
# * **Append-only.** No statement in this module is an ``UPDATE`` or ``DELETE``
#   against ``..._mappings``, ``..._quarantine`` or ``..._audit``. The only
#   ``UPDATE`` is the checkpoint CAS, which is the one mutable head; the ORM
#   guards and the ``trg_m04e_ledger_append_only_*`` triggers back that up.
# * **Redaction.** Only identifiers, reason codes, sha256 digests, cursor tokens
#   and counters are written. ``proof_evidence`` receives *named digests*, never
#   a payload; ``blocker_evidence`` receives the caller's guidance strings
#   (reason-code guidance and gate names), never a source body.
#
# Determinism of the audit trail
# ------------------------------
# The audit counters are derived from the classifications actually written, not
# from the caller's input: ``accepted_count`` counts ACCEPTED outcomes,
# ``quarantine`` counts QUARANTINED, ``conflict`` counts CONFLICT (including the
# changed-digest conflict, which is a consumed row), ``replay`` counts REPLAY, and
# ``record_count`` is their sum. ``ck_m04e_audit_counters_balance`` then holds by
# construction for a committed page; a blocked page writes zero counters and a
# non-empty reason code.

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

#: Closed reason vocabulary for the store's own refusals. They extend
#: :class:`M04EPortReason` so a log line is greppable across the whole port.
STORE_STALE_CHECKPOINT: Final = "m04e_backfill_checkpoint_stale"
STORE_FIRST_MAPPING_CONFLICT: Final = "m04e_backfill_first_mapping_conflict"
STORE_LEDGER_WRITE_REFUSED: Final = "m04e_backfill_ledger_write_refused"
STORE_OUTCOME_UNREPRESENTABLE: Final = "m04e_backfill_outcome_unrepresentable"
STORE_AUDIT_UNBALANCED: Final = "m04e_backfill_audit_unbalanced"
STORE_PARTITION_LINEAGE_MISMATCH: Final = "m04e_backfill_partition_lineage_mismatch"
STORE_DECISION_INVALID: Final = "m04e_backfill_decision_invalid"

#: ``BackfillOutcome`` values a mapping row can carry. ``blocked`` is *not* here:
#: a blocked page writes an audit event and no classification, so a mapping row is
#: never a refusal.
_MAPPING_OUTCOMES: Final[frozenset[BackfillOutcome]] = frozenset({
    BackfillOutcome.ACCEPTED, BackfillOutcome.REPLAY,
    BackfillOutcome.QUARANTINED, BackfillOutcome.CONFLICT,
})

#: Outcomes that consume a cursor position (everything except an exact replay).
_CONSUMING_OUTCOMES: Final[frozenset[BackfillOutcome]] = frozenset({
    BackfillOutcome.ACCEPTED, BackfillOutcome.QUARANTINED, BackfillOutcome.CONFLICT,
})

_CONFLICT_REASONS: Final[frozenset[str]] = frozenset({
    "source_digest_changed", "artifact_digest_changed",
})

#: ``sha256:`` digest shape, reused from the backfill contract's own helper.
_SHA256_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")

PHASE_ORDER: Final[Mapping[str, int]] = {"definition": 0, "version": 1}


def _ledger_id(prefix: str) -> str:
    """``prefix_`` plus 24 hex characters: the models' ``String(80)`` id shape."""

    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _checkpoint_token() -> str:
    """A fresh, globally unique CAS token for one cursor advance."""

    return f"m04e-cp:{uuid.uuid4().hex}"


def _binding_digest(
    context: BackfillContext, *, phase: str, after_source_id: str | None,
    sequence: int, page_exhausted: bool,
) -> str:
    """Digest of what this page asked the ledger to do.

    It identifies the *request* (partition, lineage, cursor it started from, page
    bound), never the candidate set: the same request replayed must produce the
    same evidence, and a different request must be visibly different.
    """

    return _digest({
        "tenant_id": context.tenant_id,
        "app_definition_id": context.app_definition_id,
        "source_snapshot_id": context.source_snapshot_id,
        "source_snapshot_digest": context.source_snapshot_digest,
        "source_schema_version": context.source_schema_version,
        "canonical_schema_version": context.canonical_schema_version,
        "writer_generation": context.writer_generation,
        "actor": context.actor,
        "phase": phase,
        "after_source_id": after_source_id,
        "sequence": sequence,
        "page_exhausted": page_exhausted,
    })


def _proof_evidence(decision: CandidateDecision) -> dict[str, str]:
    """Named sha256 proof digests for one accepted classification.

    Mirrors ``run_page``'s own expectation exactly (``owner``/``active_scope``,
    plus ``same_definition``/``immutable_artifact``/``artifact_content`` for a
    version, plus the four current-version proofs when one is linked). Only
    digests reach the column: no payload, manifest body, description or URI.
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


def _mapping_status(outcome: BackfillOutcome) -> EvidenceStatus:
    """The ``EvidenceStatus`` a stored row reports for one outcome.

    A stored ``conflict`` reports ``EvidenceStatus.CONFLICT``; a stored
    ``accepted``/``quarantined`` report their own status. ``replay`` never reaches
    this function: a replayed classification reports the status of the row it
    replayed, not ``replay``.
    """

    return {
        BackfillOutcome.ACCEPTED: EvidenceStatus.ACCEPTED,
        BackfillOutcome.QUARANTINED: EvidenceStatus.QUARANTINED,
        BackfillOutcome.CONFLICT: EvidenceStatus.CONFLICT,
    }[outcome]


def _checkpoint_from_row(row: Mapping[str, Any]) -> BackfillCheckpoint:
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


def _record_from_mapping_row(
    row: Mapping[str, Any], *, checkpoint: BackfillCheckpoint,
    outcome: BackfillOutcome,
) -> BackfillRecord:
    """Rebuild a ``BackfillRecord`` from one mapping ledger row, exactly.

    Everything the backfill contract compares is carried verbatim: the source
    identity and digest the row was written with, the canonical identity/digest it
    recorded (``None`` for a non-accepted row, which is what the contract
    demands), the artifact identity and the named proof digests.
    """

    identity = tuple(str(part) for part in (row.get("canonical_identity") or ()))
    artifact_digest = row.get("artifact_digest")
    status = _mapping_status(outcome)
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
        reason_code=(
            str(row["reason_code"]) if row.get("reason_code") is not None else None
        ),
        correlation_id=str(row["correlation_id"]),
        tenant_id=str(row["tenant_id"]),
        app_definition_id=str(row["definition_id"]),
        phase=str(row["phase"]),
        source_snapshot_digest=checkpoint.source_snapshot_digest,
        proof_evidence=tuple(
            sorted((str(name), str(value)) for name, value in (row.get("proof_evidence") or {}).items())
        ),
        artifact_uri=None,
        artifact_digest=str(artifact_digest) if artifact_digest is not None else None,
        verified_artifact_digest=(
            str(row["verified_artifact_digest"])
            if row.get("verified_artifact_digest") is not None else None
        ),
        current_artifact_digest=None,
        transaction_id=checkpoint.transaction_id if hasattr(checkpoint, "transaction_id") else None,
        writer_generation=checkpoint.writer_generation,
        outcome=outcome,
    )


def _record_from_quarantine_row(
    row: Mapping[str, Any], *, checkpoint: BackfillCheckpoint,
    reason_code: str,
) -> BackfillRecord:
    """Rebuild the conflict record one quarantine observation stands for.

    The *original* source digest is the one recorded on the first mapping (the
    quarantine row carries it), the observed digest is the one this observation
    recorded, and the canonical identity/digest stay empty: a conflicted source
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


def _source_predicate(
    table: Any, *, tenant_id: str, definition_id: str, source_kind: str,
    source_id: str, source_schema_version: str,
) -> Any:
    """The exact five-column source key, as a SQL predicate."""

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

    ``NULLS NOT DISTINCT`` on the constraint means an absent digest is a *value*
    for uniqueness: the predicate says so explicitly (``IS NULL``) instead of
    relying on ``= NULL``, which never matches.
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


class SqlM04EEvidenceStore(_SessionReads):
    """Durable ``EvidenceStore`` over the four registered M04E ledger tables.

    See the section comment above for the invariants. This class holds no state
    between calls and no session: every method opens its own transaction through
    the injected ``async_sessionmaker`` and the transaction-local tenant context is
    applied and proven before any row is read or written.

    The tables are addressed through their registered ORM models
    (``M04EDefinitionVersion*Row``), not through raw SQL text, so a column rename
    breaks the type checker and the tests rather than silently emitting a statement
    the database rejects; the SQL semantics that matter (exact-key lookups, the
    ``FOR UPDATE`` head read, the CAS ``UPDATE``, ``rowcount``) are expressed in
    Core so each one is testable on the SQLite mirror as well as on PostgreSQL.
    """

    #: Kept for callers that ask "is this a refusal-only boundary?"; it is not.
    IS_FAIL_CLOSED_BOUNDARY: Final = False

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        require_serializable: bool = False,
    ) -> None:
        super().__init__(
            session_factory=session_factory, require_serializable=require_serializable,
        )

    @property
    def blockers(self) -> tuple[str, ...]:
        """Named schema gaps that still block a *production* page commit."""

        return EVIDENCE_STORE_SCHEMA_GAPS

    @property
    def required_ddl(self) -> tuple[str, ...]:
        return EVIDENCE_STORE_REQUIRED_DDL

    @property
    def ledger_tables(self) -> tuple[str, ...]:
        return EVIDENCE_LEDGER_TABLES

    # -- reads -------------------------------------------------------------

    async def find_existing(
        self, *, context: BackfillContext, source_kind: str, source_id: str,
        source_schema_version: str,
    ) -> BackfillRecord | None:
        """The first mapping ever recorded for this exact source identity.

        ``None`` means the identity is genuinely unmapped: the lookup is the exact
        five-column key of ``uq_m04e_mapping_source`` inside this partition, so a
        row for the same source under another tenant/definition — or under another
        source schema version — cannot be mistaken for it.
        """

        self._require_source_key(
            context, source_kind=source_kind, source_id=source_id,
            source_schema_version=source_schema_version,
        )
        async with self._read(context) as session:
            row = await self._first_mapping(
                session, context,
                source_kind=source_kind, source_id=source_id,
                source_schema_version=source_schema_version,
            )
            if row is None:
                return None
            stored = await self._checkpoint_row(session, context, phase=str(row["phase"]))
            checkpoint = (
                _checkpoint_from_row(stored)
                if stored is not None
                else self._fresh_checkpoint(context, phase=str(row["phase"]))
            )
        outcome = self._stored_outcome(row)
        return _record_from_mapping_row(row, checkpoint=checkpoint, outcome=outcome)

    async def find_observation(
        self, *, context: BackfillContext, source_kind: str, source_id: str,
        source_schema_version: str, observed_source_digest: str | None,
        observed_artifact_digest: str | None,
    ) -> BackfillRecord | None:
        """The exact changed-digest observation previously recorded, if any.

        Exactly one of the two observed digests must be named — the table's
        ``ck_m04e_quarantine_single_observation`` admits nothing else, and a caller
        naming both (or neither) is asking an unanswerable question, so it is
        refused rather than answered with ``None``.
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
        async with self._read(context) as session:
            rows = _rows(await session.execute(
                sa.select(EVIDENCE_QUARANTINE_TABLE_MODEL.__table__).where(
                    _observation_predicate(
                        EVIDENCE_QUARANTINE_TABLE_MODEL.__table__,
                        tenant_id=context.tenant_id,
                        definition_id=context.app_definition_id,
                        source_kind=source_kind, source_id=source_id,
                        source_schema_version=source_schema_version,
                        observed_source_digest=observed_source_digest,
                        observed_artifact_digest=observed_artifact_digest,
                    )
                )
            ))
            if not rows:
                return None
            row = rows[0]
            stored = await self._checkpoint_row(session, context, phase=str(row["phase"]))
            checkpoint = (
                _checkpoint_from_row(stored)
                if stored is not None
                else self._fresh_checkpoint(context, phase=str(row["phase"]))
            )
        reason_code = str(row["reason_code"])
        if reason_code not in _CONFLICT_REASONS:
            # ``run_page`` compares the replay's reason with the decision's own, and
            # only these two reasons may be replayed as a conflict. Anything else in
            # the ledger is a quarantine that is not an observation of a changed
            # digest; answering it here would fake a conflict replay.
            raise M04EPortRefused(_refusal(
                M04EPortReason.STORE_OUTCOME_UNREPRESENTABLE,
                "the recorded observation is not a changed source/artifact digest",
                context=context,
            ))
        return _record_from_quarantine_row(row, checkpoint=checkpoint, reason_code=reason_code)

    # -- blocked evidence ---------------------------------------------------

    async def record_blocked(
        self, *, context: BackfillContext, checkpoint: BackfillCheckpoint,
        reason_code: str, blocker_evidence: Sequence[str] = (),
    ) -> BackfillRecord:
        """Append one redacted ``page_blocked`` audit event; never move the cursor.

        A blocked page has no classification to write, so no mapping and no
        quarantine row is created: the event *is* the evidence. The checkpoint the
        caller holds is echoed back untouched, which is what ``run_page`` compares
        (``blocked.checkpoint != checkpoint`` aborts).
        """

        require_usable_context(context)
        self._require_checkpoint_lineage(context, checkpoint)
        if not isinstance(reason_code, str) or not reason_code.strip():
            raise M04EPortRefused(_refusal(
                M04EPortReason.STORE_LEDGER_WRITE_REFUSED,
                "a blocked page must name a redacted reason code",
                context=context,
            ))
        evidence = self._redacted_blockers(blocker_evidence)
        async with self._read(context) as session:
            stored = await self._checkpoint_row(session, context, phase=checkpoint.phase)
            sequence = int(stored["sequence"]) if stored is not None else checkpoint.sequence
            await session.execute(sa.insert(EVIDENCE_AUDIT_TABLE_MODEL.__table__).values(
                id=_ledger_id("m04eaudit"),
                event_id=self._event_id(
                    context, kind="page_blocked", phase=checkpoint.phase,
                    sequence=sequence, reason_code=reason_code,
                    binding=_binding_digest(
                        context, phase=checkpoint.phase,
                        after_source_id=checkpoint.after_source_id,
                        sequence=checkpoint.sequence, page_exhausted=False,
                    ),
                ),
                tenant_id=context.tenant_id,
                definition_id=context.app_definition_id,
                phase=checkpoint.phase,
                event_kind="page_blocked",
                reason_code=reason_code,
                correlation_id=context.correlation_id,
                checkpoint=checkpoint.after_source_id or "",
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

        Order of operations inside the transaction is what makes the invariants
        hold:

        1. the head checkpoint row is locked and re-read; a head that is not the
           cursor the caller expected is a *stale cursor*, answered with the
           durable head and **no write at all**;
        2. each decision is classified against the ledger (first mapping, exact
           observation, canonical-identity duplicate) and the classification rows
           are appended — never updated;
        3. the audit event is appended with counters derived from those rows;
        4. the checkpoint is advanced by CAS on the observed ``sequence`` and
           ``checkpoint_token``; a zero-row CAS rolls the whole transaction back
           and returns the durable head.

        A page where every classification replayed writes nothing and returns the
        stored rows plus the stored checkpoint.
        """

        require_usable_context(context)
        self._require_checkpoint_lineage(context, expected_checkpoint)
        if not isinstance(gates, UpstreamGateEvidence) or not gates.valid:
            raise M04EPortRefused(_refusal(
                M04EPortReason.GATE_CONTEXT_INVALID,
                "a page commit requires valid upstream gate evidence",
                context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
            ))
        if (gates.tenant_id, gates.app_definition_id) != (
            context.tenant_id, context.app_definition_id,
        ) or gates.source_snapshot_digest != context.source_snapshot_digest:
            raise M04EPortRefused(_refusal(
                M04EPortReason.GATE_CONTEXT_INVALID,
                "the gate evidence belongs to another partition or snapshot lineage",
                context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
            ))
        ordered = self._ordered_decisions(context, expected_checkpoint, decisions)

        async with self._read(context) as session:
            stored = await self._lock_checkpoint_row(
                session, context, phase=expected_checkpoint.phase,
            )
            if stored is None:
                if expected_checkpoint.sequence != 0 or expected_checkpoint.after_source_id:
                    # The caller believes a cursor exists that the ledger never
                    # recorded: answer with the cursor that would be created, and
                    # write nothing. Silently inserting a head at the caller's
                    # sequence would import an unverifiable cursor.
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
                observed_sequence = int(stored["sequence"])
                observed_token = str(stored["checkpoint_token"])
                durable = _checkpoint_from_row(stored)

            if (
                observed_sequence != expected_checkpoint.sequence
                or durable.after_source_id != expected_checkpoint.after_source_id
                or durable.phase_complete != expected_checkpoint.phase_complete
            ):
                # Stale cursor: the reviewed answer is the durable head, not an
                # exception and not a partial write. ``rowcount == 0`` below is the
                # same verdict reached without a lock.
                return BackfillBatch((), durable, durable.phase_complete)

            records, consumed, writes_quarantine = await self._classify(
                session, context=context, checkpoint=durable, decisions=ordered,
            )

            if not consumed and not writes_quarantine:
                # An all-replay page moves nothing: the stored rows and the stored
                # cursor are the answer, and no audit row claims otherwise.
                return BackfillBatch(tuple(records), durable, durable.phase_complete)

            next_checkpoint = self._next_checkpoint(
                context, current=durable, consumed=consumed, page_exhausted=page_exhausted,
            )
            token = _checkpoint_token()
            checkpoint_changed = next_checkpoint != durable

            counters = self._counters(records)
            event_id = self._event_id(
                context, kind="page_committed", phase=durable.phase,
                sequence=observed_sequence,
                binding=_binding_digest(
                    context, phase=expected_checkpoint.phase,
                    after_source_id=expected_checkpoint.after_source_id,
                    sequence=expected_checkpoint.sequence, page_exhausted=page_exhausted,
                ),
            )
            await session.execute(sa.insert(EVIDENCE_AUDIT_TABLE_MODEL.__table__).values(
                id=_ledger_id("m04eaudit"),
                event_id=event_id,
                tenant_id=context.tenant_id,
                definition_id=context.app_definition_id,
                phase=durable.phase,
                event_kind="page_committed",
                reason_code=None,
                correlation_id=context.correlation_id,
                checkpoint=token if checkpoint_changed else str(observed_token or ""),
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

            if checkpoint_changed:
                if stored is None:
                    inserted = await session.execute(sa.insert(
                        EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__
                    ).values(
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
                    if int(inserted.rowcount or 0) != 1:
                        raise M04EPortRefused(_refusal(
                            M04EPortReason.STORE_LEDGER_WRITE_REFUSED,
                            "the checkpoint head could not be created",
                            context=context,
                        ))
                else:
                    advanced = await session.execute(
                        sa.update(EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__)
                        .where(
                            sa.and_(
                                EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__.c.tenant_id
                                == context.tenant_id,
                                EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__.c.definition_id
                                == context.app_definition_id,
                                EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__.c.phase
                                == durable.phase,
                                EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__.c.sequence
                                == observed_sequence,
                                EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__.c.checkpoint_token
                                == observed_token,
                            )
                        )
                        .values(
                            after_source_id=next_checkpoint.after_source_id,
                            sequence=next_checkpoint.sequence,
                            phase_complete=next_checkpoint.phase_complete,
                            checkpoint_token=token,
                        )
                    )
                    if int(advanced.rowcount or 0) != 1:
                        # Lost the CAS without holding the row: roll the whole page
                        # back and report the durable head. Raising here would leave
                        # the caller without the authoritative cursor.
                        await session.rollback()
                        async with self._session_factory() as reader:
                            current = await self._checkpoint_row(
                                reader, context, phase=durable.phase,
                                use_phase_switch_row=False,
                            )
                        head = (
                            _checkpoint_from_row(current) if current is not None else durable
                        )
                        return BackfillBatch((), head, head.phase_complete)

            committed = next_checkpoint if checkpoint_changed else durable
            stamped = tuple(
                self._with_checkpoint(record, durable, token, observed_token)
                for record in records
            )
        return BackfillBatch(
            stamped, committed, committed.phase_complete,
        )

    # -- internals: rows ----------------------------------------------------

    async def _first_mapping(
        self, session: AsyncSession, context: BackfillContext, *,
        source_kind: str, source_id: str, source_schema_version: str,
    ) -> Mapping[str, Any] | None:
        rows = _rows(await session.execute(
            sa.select(EVIDENCE_MAPPING_TABLE_MODEL.__table__).where(
                _source_predicate(
                    EVIDENCE_MAPPING_TABLE_MODEL.__table__,
                    tenant_id=context.tenant_id,
                    definition_id=context.app_definition_id,
                    source_kind=source_kind, source_id=source_id,
                    source_schema_version=source_schema_version,
                )
            ).limit(1)
        ))
        return rows[0] if rows else None

    async def _checkpoint_row(
        self, session: AsyncSession, context: BackfillContext, *, phase: str,
        use_phase_switch_row: bool = True,
    ) -> Mapping[str, Any] | None:
        """The head row of one ``(tenant, definition, phase)`` stream, if any.

        With ``use_phase_switch_row`` the *definition* phase falls back to the
        version head when it has already been drained: the backfill transitions
        definition -> version by advancing the same head row, so after that
        transition a definition-phase read would otherwise find nothing.
        """

        rows = _rows(await session.execute(
            sa.select(EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__).where(
                sa.and_(
                    EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__.c.tenant_id == context.tenant_id,
                    EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__.c.definition_id
                    == context.app_definition_id,
                    EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__.c.phase == phase,
                )
            ).limit(1)
        ))
        if rows:
            return rows[0]
        if use_phase_switch_row and phase == "definition":
            return await self._checkpoint_row(
                session, context, phase="version", use_phase_switch_row=False,
            )
        return None

    async def _lock_checkpoint_row(
        self, session: AsyncSession, context: BackfillContext, *, phase: str,
    ) -> Mapping[str, Any] | None:
        """``SELECT ... FOR UPDATE`` of the head row (a no-op on SQLite)."""

        statement = (
            sa.select(EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__)
            .where(
                sa.and_(
                    EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__.c.tenant_id == context.tenant_id,
                    EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__.c.definition_id
                    == context.app_definition_id,
                    EVIDENCE_CHECKPOINT_TABLE_MODEL.__table__.c.phase == phase,
                )
            )
            .with_for_update()
        )
        rows = _rows(await session.execute(statement))
        return rows[0] if rows else None

    # -- internals: classification -----------------------------------------

    def _ordered_decisions(
        self, context: BackfillContext, checkpoint: BackfillCheckpoint,
        decisions: Sequence[CandidateDecision],
    ) -> tuple[CandidateDecision, ...]:
        """Validate and order one page's classifications.

        The store refuses a page that is not about its own partition, whose
        candidates are outside the checkpoint lineage, that repeats a source
        identity, that carries a status the ledger cannot store (``BLOCKED`` is a
        page event, not a classification) or that is not strictly ascending after
        the cursor. A page that cannot be ordered correctly must abort, not commit
        a cursor that skips or re-reads rows.
        """

        if not isinstance(decisions, Sequence):
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
                    M04EPortReason.STORE_PARTITION_LINEAGE_MISMATCH,
                    "a candidate belongs to another tenant/definition partition",
                    context=context,
                ))
            if candidate.phase != checkpoint.phase:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_PARTITION_LINEAGE_MISMATCH,
                    "a candidate belongs to another backfill phase than the cursor",
                    context=context,
                ))
            if (
                candidate.schema_version != context.source_schema_version
                or candidate.canonical_schema_version != context.canonical_schema_version
            ):
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_PARTITION_LINEAGE_MISMATCH,
                    "a candidate is outside the checkpoint schema lineage",
                    context=context,
                ))
            if decision.status is EvidenceStatus.BLOCKED:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_OUTCOME_UNREPRESENTABLE,
                    "a blocked page is audit evidence, not a per-source classification",
                    context=context,
                ))
            if decision.status not in {
                EvidenceStatus.ACCEPTED, EvidenceStatus.REPLAY,
                EvidenceStatus.QUARANTINED, EvidenceStatus.CONFLICT,
            }:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.STORE_OUTCOME_UNREPRESENTABLE,
                    "the classification is outside the ledger's outcome vocabulary",
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
            if checkpoint.after_source_id is not None and candidate.source_id <= checkpoint.after_source_id:
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
    ) -> tuple[list[BackfillRecord], list[str], bool]:
        """Turn classifications into ledger rows; append-only, idempotent per key.

        Returns the records to return, the source identities that consumed a cursor
        position, and whether any quarantine observation was appended (an
        observation advances the cursor even when the first mapping already
        existed, because the *observation* is new evidence about a consumed row).
        """

        records: list[BackfillRecord] = []
        consumed: list[str] = []
        writes_quarantine = False
        seen: set[tuple[str, ...]] = set()
        for decision in decisions:
            candidate = decision.candidate
            row, wrote_observation, consumes = await self._classify_one(
                session, context=context, checkpoint=checkpoint,
                decision=decision, seen=seen,
            )
            if candidate.canonical_identity:
                seen.add(candidate.canonical_identity)
            if wrote_observation:
                writes_quarantine = True
            if consumes:
                consumed.append(candidate.source_id)
            records.append(row)
        return records, consumed, writes_quarantine

    async def _classify_one(
        self, session: AsyncSession, *, context: BackfillContext,
        checkpoint: BackfillCheckpoint, decision: CandidateDecision,
        seen: set[tuple[str, ...]],
    ) -> tuple[BackfillRecord, bool, bool]:
        candidate = decision.candidate
        existing = await self._first_mapping(
            session, context,
            source_kind=candidate.source_kind, source_id=candidate.source_id,
            source_schema_version=candidate.schema_version,
        )
        status = decision.status
        reason = decision.reason_code
        verified = decision.verified_artifact_digest

        # 1. A changed source digest: append (or replay) an exact observation. The
        #    first mapping stays byte-identical — that is the whole point of the
        #    conflict branch.
        if (
            existing is not None
            and existing["source_digest"] != candidate.source_digest
        ):
            observation = await self._find_observation_row(
                session, context, candidate=candidate,
                observed_source_digest=candidate.source_digest,
                observed_artifact_digest=None,
            )
            appended = observation is None
            if appended:
                observation = await self._append_observation(
                    session, context=context, candidate=candidate,
                    recorded_source_digest=str(existing["source_digest"]),
                    observed_source_digest=candidate.source_digest,
                    observed_artifact_digest=None,
                    reason_code="source_digest_changed",
                    blocker_evidence=decision.blocker_evidence,
                )
            replaying = decision.status is EvidenceStatus.REPLAY
            record = _record_from_quarantine_row(
                observation, checkpoint=checkpoint,
                reason_code="source_digest_changed",
            )
            if replaying:
                record = replace(record, outcome=BackfillOutcome.REPLAY, status=EvidenceStatus.CONFLICT)
            return record, appended, not replaying

        # 2. Same source digest, different verified artifact bytes: an immutable
        #    artifact conflict, recorded as an artifact observation.
        if (
            existing is not None
            and existing.get("verified_artifact_digest") is not None
            and verified is not None
            and existing["verified_artifact_digest"] != verified
        ):
            observation = await self._find_observation_row(
                session, context, candidate=candidate,
                observed_source_digest=None, observed_artifact_digest=verified,
            )
            appended = observation is None
            if appended:
                observation = await self._append_observation(
                    session, context=context, candidate=candidate,
                    recorded_source_digest=str(existing["source_digest"]),
                    observed_source_digest=None,
                    observed_artifact_digest=verified,
                    reason_code="artifact_digest_changed",
                    blocker_evidence=decision.blocker_evidence,
                )
            replaying = decision.status is EvidenceStatus.REPLAY
            record = _record_from_quarantine_row(
                observation, checkpoint=checkpoint,
                reason_code="artifact_digest_changed",
            )
            if replaying:
                record = replace(record, outcome=BackfillOutcome.REPLAY, status=EvidenceStatus.CONFLICT)
            return record, appended, not replaying

        # 3. An exact replay of the first mapping: same source digest, same
        #    verified artifact bytes and the same classification. Nothing is
        #    written and the cursor does not move.
        if existing is not None:
            stored = self._stored_outcome(existing)
            if (
                existing["source_digest"] == candidate.source_digest
                and existing.get("verified_artifact_digest") == verified
                and stored is _mapping_status_aligned(decision.status)
                and _reason_matches(existing, decision)
            ):
                return _record_from_mapping_row(
                    existing, checkpoint=checkpoint, outcome=BackfillOutcome.REPLAY,
                ), False, False
            if stored is BackfillOutcome.CONFLICT:
                # A conflicted first mapping (canonical-identity conflict) replays
                # as itself: same source digest, same reason, no new row.
                if _reason_matches(existing, decision):
                    return _record_from_mapping_row(
                        existing, checkpoint=checkpoint, outcome=BackfillOutcome.REPLAY,
                    ), False, False

        # 4. A new first mapping for a source identity the ledger has never seen.
        if existing is None:
            outcome = BackfillOutcome.CONFLICT if status is EvidenceStatus.CONFLICT else (
                BackfillOutcome.ACCEPTED if status is EvidenceStatus.ACCEPTED
                else BackfillOutcome.QUARANTINED
            )
            identity = candidate.canonical_identity
            if status is EvidenceStatus.ACCEPTED and identity in seen:
                # A duplicate canonical identity inside one page is a conflict, not
                # an acceptance: two sources cannot both own one canonical row.
                outcome, reason = BackfillOutcome.CONFLICT, "duplicate_canonical_identity"
            canonical_digest = (
                candidate.canonical_digest if outcome is BackfillOutcome.ACCEPTED else None
            )
            proof = _proof_evidence(decision) if outcome is BackfillOutcome.ACCEPTED else {}
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
                canonical_identity=list(identity) if identity else [],
                reason_code=reason,
                proof_evidence=proof,
                artifact_digest=(
                    str(candidate.payload.get("artifact_digest"))
                    if candidate.phase == "version" and candidate.payload.get("artifact_digest")
                    else None
                ),
                verified_artifact_digest=verified,
                checkpoint=str(checkpoint.after_source_id or ""),
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
                status=_mapping_status(outcome),
                reason_code=reason,
                correlation_id=context.correlation_id,
                tenant_id=context.tenant_id,
                app_definition_id=context.app_definition_id,
                phase=candidate.phase,
                source_snapshot_digest=context.source_snapshot_digest,
                proof_evidence=tuple(sorted(proof.items())),
                artifact_uri=(
                    str(candidate.payload.get("artifact_uri"))
                    if candidate.phase == "version" and isinstance(
                        candidate.payload.get("artifact_uri"), str
                    ) else None
                ),
                artifact_digest=(
                    str(candidate.payload.get("artifact_digest"))
                    if candidate.phase == "version" and candidate.payload.get("artifact_digest")
                    else None
                ),
                verified_artifact_digest=verified,
                current_artifact_digest=decision.verified_current_artifact_digest,
                transaction_id=context.transaction_id,
                writer_generation=context.writer_generation,
                outcome=outcome,
                blocker_evidence=(
                    tuple(decision.blocker_evidence)
                    if outcome is BackfillOutcome.QUARANTINED else ()
                ),
            ), False, True

        # 5. A first mapping exists whose source digest matches but whose stored
        #    classification differs from the incoming one, and no conflict branch
        #    above applied. Re-recording it would rewrite history, so it is refused.
        raise M04EPortRefused(_refusal(
            M04EPortReason.STORE_FIRST_MAPPING_CONFLICT,
            "the source identity is already mapped with a different classification; "
            "a first mapping is immutable and a changed digest is an observation",
            context=context,
        ))

    async def _find_observation_row(
        self, session: AsyncSession, context: BackfillContext, *,
        candidate: EvidenceCandidate, observed_source_digest: str | None,
        observed_artifact_digest: str | None,
    ) -> Mapping[str, Any] | None:
        rows = _rows(await session.execute(
            sa.select(EVIDENCE_QUARANTINE_TABLE_MODEL.__table__).where(
                _observation_predicate(
                    EVIDENCE_QUARANTINE_TABLE_MODEL.__table__,
                    tenant_id=context.tenant_id,
                    definition_id=context.app_definition_id,
                    source_kind=candidate.source_kind, source_id=candidate.source_id,
                    source_schema_version=candidate.schema_version,
                    observed_source_digest=observed_source_digest,
                    observed_artifact_digest=observed_artifact_digest,
                )
            ).limit(1)
        ))
        return rows[0] if rows else None

    async def _append_observation(
        self, session: AsyncSession, *, context: BackfillContext,
        candidate: EvidenceCandidate, recorded_source_digest: str,
        observed_source_digest: str | None, observed_artifact_digest: str | None,
        reason_code: str, blocker_evidence: Sequence[str],
    ) -> Mapping[str, Any]:
        """Append one redacted quarantine observation and return it.

        The observation is *derived* from the caller's decision but never trusts
        it: the reason code is the store's own (it saw the digests itself), the
        recorded digest is the first mapping's, and the guidance is filtered to the
        strings the backfill contract already produces for this reason.
        """

        evidence = self._redacted_blockers(blocker_evidence)
        values = {
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
            "reason_code": reason_code,
            "blocker_evidence": list(evidence),
            "checkpoint": str((candidate.source_id or "")),
            "status": "open",
            "correlation_id": context.correlation_id,
        }
        await session.execute(
            sa.insert(EVIDENCE_QUARANTINE_TABLE_MODEL.__table__).values(**values)
        )
        return values

    # -- internals: cursor --------------------------------------------------

    def _next_checkpoint(
        self, context: BackfillContext, *, current: BackfillCheckpoint,
        consumed: Sequence[str], page_exhausted: bool,
    ) -> BackfillCheckpoint:
        """The cursor the backfill contract expects for this page.

        Mirrors ``run_page``'s own derivation exactly: one sequence step per
        consumed source identity; an exhausted ``definition`` stream transitions to
        ``version`` with an empty cursor and one extra step; an exhausted
        ``version`` stream completes without moving past its last consumed row.
        """

        phase = current.phase
        after = current.after_source_id
        sequence = current.sequence + len(consumed)
        complete = False
        if page_exhausted:
            if phase == "definition":
                phase, after = "version", None
                sequence += 1
            else:
                if consumed:
                    after = consumed[-1]
                complete = True
        if consumed and phase == current.phase:
            after = consumed[-1]
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

    @staticmethod
    def _with_checkpoint(
        record: BackfillRecord, durable: BackfillCheckpoint, token: str,
        observed_token: str | None,
    ) -> BackfillRecord:
        """Stamp a returned record with the cursor the page committed under.

        A record returned by ``run_page`` must carry the *page's* cursor, not the
        row's own; the row's token is only a join key in the ledger.
        """

        return replace(record, checkpoint=durable)

    def _require_checkpoint_lineage(
        self, context: BackfillContext, checkpoint: BackfillCheckpoint,
    ) -> None:
        if not isinstance(checkpoint, BackfillCheckpoint):
            raise M04EPortRefused(_refusal(
                M04EPortReason.STORE_PARTITION_LINEAGE_MISMATCH,
                "a BackfillCheckpoint is required",
                context=context,
            ))
        if checkpoint.partition != (context.tenant_id, context.app_definition_id):
            raise M04EPortRefused(_refusal(
                M04EPortReason.STORE_PARTITION_LINEAGE_MISMATCH,
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
                M04EPortReason.STORE_PARTITION_LINEAGE_MISMATCH,
                "the checkpoint is bound to another snapshot or schema lineage",
                context=context,
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
    def _counters(records: Sequence[BackfillRecord]) -> dict[str, int]:
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
    def _event_id(
        context: BackfillContext, *, kind: str, phase: str, sequence: int,
        reason_code: str | None = None, binding: str = "",
    ) -> str:
        """Stable id for one page event.

        Derived from the page's binding (partition, lineage, cursor it read and the
        page bound) plus the event kind, so a retried page under the same cursor
        names the same event instead of inventing a second one; the row id stays
        unique because the audit table is append-only and never deletes.
        """

        return _digest({
            "tenant_id": context.tenant_id,
            "app_definition_id": context.app_definition_id,
            "correlation_id": context.correlation_id,
            "transaction_id": context.transaction_id,
            "actor": context.actor,
            "event_kind": kind,
            "phase": phase,
            "sequence": sequence,
            "reason_code": reason_code,
            "binding": binding,
        })

    @staticmethod
    def _redacted_blockers(blocker_evidence: Sequence[str]) -> tuple[str, ...]:
        """Keep only short, single-line, digest-free guidance strings.

        The backfill contract's guidance strings and gate names pass; anything that
        looks like a payload (newlines, very long text, a raw secret marker) is
        dropped rather than stored. A quarantine row's ``blocker_evidence`` is
        evidence a reviewer reads — it must never be a body excerpt.
        """

        kept: list[str] = []
        for item in blocker_evidence or ():
            if not isinstance(item, str):
                continue
            text = " ".join(item.split())
            if not text or "\n" in item or len(text) > _MAX_BLOCKER_LENGTH:
                continue
            kept.append(text)
        return tuple(kept)


#: ``status`` -> the ``BackfillOutcome`` that status records.
_OUTCOME_BY_STATUS: Final[Mapping[str, BackfillOutcome]] = {
    "accepted": BackfillOutcome.ACCEPTED,
    "replay": BackfillOutcome.REPLAY,
    "quarantined": BackfillOutcome.QUARANTINED,
    "conflict": BackfillOutcome.CONFLICT,
    "blocked": BackfillOutcome.BLOCKED,
}

#: Longest guidance string a redacted ``blocker_evidence`` entry may carry.
_MAX_BLOCKER_LENGTH: Final = 400


def _mapping_status_aligned(status: EvidenceStatus) -> BackfillOutcome:
    """The outcome a stored row must carry for ``status`` to be an exact replay."""

    return {
        EvidenceStatus.ACCEPTED: BackfillOutcome.ACCEPTED,
        EvidenceStatus.QUARANTINED: BackfillOutcome.QUARANTINED,
        EvidenceStatus.CONFLICT: BackfillOutcome.CONFLICT,
    }.get(status, BackfillOutcome.QUARANTINED)


def _reason_matches(row: Mapping[str, Any], decision: CandidateDecision) -> bool:
    stored = row.get("reason_code")
    return (str(stored) if stored is not None else None) == decision.reason_code
