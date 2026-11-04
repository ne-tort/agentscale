"""The operator entry point: dry run writes nothing, a real run needs every proof.

Four properties are pinned here, in the order an operator meets them:

1. a dry run is the default, and it never opens a session (so it cannot write anything);
2. a ``dry_run=False`` call whose evidence is incomplete refuses **before** reaching the
   stage service -- the service's call counter stays at zero;
3. a real call that *is* fully evidenced reaches exactly one service, once;
4. a stage service refusal is reported as data (``partition_stage_refused``), and an
   unexpected error never hides behind a success (``partition_stage_failed``).
"""

from __future__ import annotations

import pytest

from prodavan.application.migration.composition import (
    MigrationStage,
    assemble_migration_chain,
)
from prodavan.application.migration.evidence_ports import MigrationEvidenceBlocked
from prodavan.application.migration.m04e_definition_version.backfill import (
    HandoffStatus as M04EHandoffStatus,
)
from prodavan.application.migration.partition_chain import (
    PARTITION_M04E_ARTIFACTS_UNAVAILABLE,
    PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE,
    build_partition_chain,
)
from prodavan.application.migration.partition_runner import (
    PARTITION_EVIDENCE_MISSING,
    PARTITION_EXECUTION_REFUSED,
    PARTITION_STAGE_FAILED,
    PARTITION_STAGE_PRECONDITION_UNMET,
    PARTITION_STAGE_REFUSED,
    PARTITION_UNRESOLVED_COUNTS,
    PARTITION_WRITER_GENERATION_STALE,
    STAGE_EXECUTION,
    ScopedEvidenceReadiness,
    run_partition_stage,
    stage_execution_digest,
)
from .partition_fixtures import (
    GENERATION,
    MarkerStub,
    RecordingEvidenceSource,
    RecordingSessionFactory,
    RecordingStageService,
    complete_overrides,
    key,
    promotion_record,
    ready_marker,
    replace_stage_service,
)

BATCH = object()


def _install(monkeypatch: pytest.MonkeyPatch, *, stage: MigrationStage) -> RecordingStageService:
    service = RecordingStageService(result=BATCH)
    symbol = STAGE_EXECUTION[stage][0]
    replace_stage_service(
        monkeypatch,
        assemble_migration_chain,
        stage=stage,
        symbol=symbol,
        service=service,
    )
    return service


# ---------------------------------------------------------------------------
# 1. dry run is the default and writes nothing
# ---------------------------------------------------------------------------


async def test_dry_run_is_the_default() -> None:
    outcome = await run_partition_stage(
        key=key(),
        stage=MigrationStage.M04E_DEFINITION_VERSION,
        session_factory=object(),
        writer_generation=GENERATION,
        overrides=complete_overrides(),
        evidence_source=RecordingEvidenceSource(),
    )

    assert outcome.dry_run is True
    assert outcome.executed is False
    assert outcome.execution is None


async def test_a_dry_run_never_opens_a_session_and_never_calls_a_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two ways a dry run could write are both excluded, not merely unobserved."""

    factory = RecordingSessionFactory()
    service = _install(monkeypatch, stage=MigrationStage.M05_INSTANCE_RUNTIME)

    outcome = await run_partition_stage(
        key=key(),
        stage=MigrationStage.M05_INSTANCE_RUNTIME,
        session_factory=factory,
        writer_generation=GENERATION,
        overrides=complete_overrides(),
        evidence_source=RecordingEvidenceSource(marker=ready_marker()),
    )

    assert outcome.executable is True
    assert outcome.executed is False
    assert service.calls == []
    # The factory raises on first use: reaching it would be an exception, not a count.
    assert factory.calls == 0


async def test_a_dry_run_reports_the_plan_not_a_stage_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _install(monkeypatch, stage=MigrationStage.M04E_DEFINITION_VERSION)

    outcome = await run_partition_stage(
        key=key(),
        stage=MigrationStage.M04E_DEFINITION_VERSION,
        session_factory=object(),
        writer_generation=GENERATION,
        overrides=complete_overrides(),
        evidence_source=RecordingEvidenceSource(),
    )

    snapshot = outcome.snapshot()
    assert snapshot["dry_run"] is True
    assert snapshot["execution"] is None
    # The diagnostic still says what *would* run, so a dry run is actionable.
    assert snapshot["services"]
    assert snapshot["built"]
    assert service.calls == []


async def test_a_dry_run_against_an_incomplete_partition_does_not_read_evidence() -> None:
    """No host collaborators -> the plan is refused before any evidence read."""

    source = RecordingEvidenceSource()
    outcome = await run_partition_stage(
        key=key(),
        stage=MigrationStage.M05_INSTANCE_RUNTIME,
        session_factory=object(),
        writer_generation=GENERATION,
        evidence_source=source,
    )

    assert outcome.executable is False
    assert outcome.blocked(PARTITION_M04E_ARTIFACTS_UNAVAILABLE)
    assert outcome.blocked(PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE)
    # Nothing was read: the refusal is about the object graph, not about stored evidence.
    assert source.reads == []


# ---------------------------------------------------------------------------
# 2. dry_run=False refuses without reaching the stage service
# ---------------------------------------------------------------------------


async def test_a_real_run_without_a_complete_chain_refuses_and_executes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _install(monkeypatch, stage=MigrationStage.CUTOVER)
    factory = RecordingSessionFactory()

    outcome = await run_partition_stage(
        key=key(),
        stage=MigrationStage.CUTOVER,
        session_factory=factory,
        writer_generation=GENERATION,
        dry_run=False,
        evidence_source=RecordingEvidenceSource(),
    )

    assert outcome.executed is False
    assert outcome.executable is False
    assert outcome.blocker_codes[0] == PARTITION_EXECUTION_REFUSED
    assert PARTITION_M04E_ARTIFACTS_UNAVAILABLE in outcome.blocker_codes
    assert outcome.details[0].startswith("nothing was executed")
    assert service.calls == []
    assert factory.calls == 0


@pytest.mark.parametrize(
    ("stage", "source", "expected_code"),
    (
        # M04E: the earlier M04 stage never recorded accepted scope evidence.
        (
            MigrationStage.M04E_DEFINITION_VERSION,
            RecordingEvidenceSource(m04_scope_state=ScopedEvidenceReadiness()),
            PARTITION_STAGE_PRECONDITION_UNMET,
        ),
        # M04E: the recorded scope evidence is a refusal, not an acceptance.
        (
            MigrationStage.M04E_DEFINITION_VERSION,
            RecordingEvidenceSource(
                m04_scope_state=ScopedEvidenceReadiness(
                    present=True, accepted=False, outcome="quarantined",
                    writer_generation=GENERATION,
                )
            ),
            PARTITION_EVIDENCE_MISSING,
        ),
        # M04E: the scope evidence belongs to another writer generation.
        (
            MigrationStage.M04E_DEFINITION_VERSION,
            RecordingEvidenceSource(
                m04_scope_state=ScopedEvidenceReadiness(
                    present=True, accepted=True, outcome="ready",
                    writer_generation=GENERATION - 1,
                )
            ),
            PARTITION_WRITER_GENERATION_STALE,
        ),
        # M05: no M04E handoff marker for this partition.
        (
            MigrationStage.M05_INSTANCE_RUNTIME,
            RecordingEvidenceSource(marker=None),
            PARTITION_STAGE_PRECONDITION_UNMET,
        ),
        # M05: the marker exists but records a refusal.
        (
            MigrationStage.M05_INSTANCE_RUNTIME,
            RecordingEvidenceSource(
                marker=MarkerStub(status=M04EHandoffStatus.BLOCKED)
            ),
            PARTITION_EVIDENCE_MISSING,
        ),
        # M05: a ready marker with quarantined rows is not a clean partition.
        (
            MigrationStage.M05_INSTANCE_RUNTIME,
            RecordingEvidenceSource(marker=ready_marker(quarantined_count=2)),
            PARTITION_UNRESOLVED_COUNTS,
        ),
        # M05: a ready marker from another generation is stale.
        (
            MigrationStage.M05_INSTANCE_RUNTIME,
            RecordingEvidenceSource(marker=ready_marker(writer_generation=GENERATION + 1)),
            PARTITION_WRITER_GENERATION_STALE,
        ),
        # M06: no M05 promotion record yet.
        (
            MigrationStage.M06_GRANTS_GATEWAY,
            RecordingEvidenceSource(promotion=None),
            PARTITION_STAGE_PRECONDITION_UNMET,
        ),
        # M06: the promotion record still carries quarantined instances.
        (
            MigrationStage.M06_GRANTS_GATEWAY,
            RecordingEvidenceSource(promotion=promotion_record(quarantined=1)),
            PARTITION_UNRESOLVED_COUNTS,
        ),
        # Cutover: M06 has not recorded accepted evidence.
        (
            MigrationStage.CUTOVER,
            RecordingEvidenceSource(m06_state=ScopedEvidenceReadiness()),
            PARTITION_STAGE_PRECONDITION_UNMET,
        ),
        # Cutover: M06 recorded a refusal.
        (
            MigrationStage.CUTOVER,
            RecordingEvidenceSource(
                m06_state=ScopedEvidenceReadiness(
                    present=True, accepted=False, outcome="blocked",
                    writer_generation=GENERATION,
                )
            ),
            PARTITION_EVIDENCE_MISSING,
        ),
        # Cutover: M06 evidence is at another epoch.
        (
            MigrationStage.CUTOVER,
            RecordingEvidenceSource(
                m06_state=ScopedEvidenceReadiness(
                    present=True, accepted=True, outcome="promotable",
                    writer_generation=GENERATION + 1,
                )
            ),
            PARTITION_WRITER_GENERATION_STALE,
        ),
    ),
)
async def test_incomplete_evidence_refuses_a_real_run_with_the_exact_code(
    monkeypatch: pytest.MonkeyPatch,
    stage: MigrationStage,
    source: RecordingEvidenceSource,
    expected_code: str,
) -> None:
    service = _install(monkeypatch, stage=stage)

    outcome = await run_partition_stage(
        key=key(),
        stage=stage,
        session_factory=object(),
        writer_generation=GENERATION,
        dry_run=False,
        overrides=complete_overrides(),
        evidence_source=source,
    )

    assert outcome.executed is False
    assert expected_code in outcome.blocker_codes
    # The exact code is checked *and* the class code, so a caller may test either.
    assert outcome.blocker_codes[0] == PARTITION_EXECUTION_REFUSED
    assert service.calls == []


# ---------------------------------------------------------------------------
# 3. a fully evidenced real run reaches exactly one service, once
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "source"),
    (
        (MigrationStage.M04_SCOPE, RecordingEvidenceSource()),
        (MigrationStage.M04E_DEFINITION_VERSION, RecordingEvidenceSource()),
        (
            MigrationStage.M05_INSTANCE_RUNTIME,
            RecordingEvidenceSource(marker=ready_marker()),
        ),
        (
            MigrationStage.M06_GRANTS_GATEWAY,
            RecordingEvidenceSource(promotion=promotion_record()),
        ),
        (MigrationStage.CUTOVER, RecordingEvidenceSource()),
    ),
)
async def test_a_fully_evidenced_real_run_calls_its_stage_once(
    monkeypatch: pytest.MonkeyPatch,
    stage: MigrationStage,
    source: RecordingEvidenceSource,
) -> None:
    service = _install(monkeypatch, stage=stage)
    kwargs = {"checkpoint": "cursor-of-this-partition"}

    outcome = await run_partition_stage(
        key=key(),
        stage=stage,
        session_factory=object(),
        writer_generation=GENERATION,
        dry_run=False,
        overrides=complete_overrides(),
        evidence_source=source,
        stage_kwargs=kwargs,
    )

    assert outcome.executed is True, outcome.details
    assert outcome.executable is True
    assert outcome.blocker_codes == ()
    assert len(service.calls) == 1
    # The runner forwards exactly what it was given: it does not invent stage arguments.
    assert service.calls[0] == kwargs
    assert outcome.execution is not None
    assert outcome.execution.result is BATCH
    assert outcome.execution.service == STAGE_EXECUTION[stage][0]
    assert outcome.execution.method == STAGE_EXECUTION[stage][1]


async def test_the_same_real_call_twice_is_idempotent_for_the_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stage replays; the verdict and its digest must not drift between calls."""

    service = _install(monkeypatch, stage=MigrationStage.M06_GRANTS_GATEWAY)
    args = dict(
        key=key(),
        stage=MigrationStage.M06_GRANTS_GATEWAY,
        session_factory=object(),
        writer_generation=GENERATION,
        dry_run=False,
        overrides=complete_overrides(),
        evidence_source=RecordingEvidenceSource(promotion=promotion_record()),
    )

    first = await run_partition_stage(**args)  # type: ignore[arg-type]
    second = await run_partition_stage(**args)  # type: ignore[arg-type]

    assert first.digest == second.digest
    assert stage_execution_digest(first) == stage_execution_digest(second)
    # Two explicit calls are two stage calls: the runner never batches, retries or schedules.
    assert len(service.calls) == 2


# ---------------------------------------------------------------------------
# 4. stage failures are data, never a silent success
# ---------------------------------------------------------------------------


class _RefusingService(RecordingStageService):
    async def run(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        raise MigrationEvidenceBlocked(("m05_promotion_concurrent_append_conflict",))


class _ExplodingService(RecordingStageService):
    async def run(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        raise RuntimeError("postgresql+asyncpg://user:secret@host/db is unreachable")


async def test_a_stage_refusal_is_reported_as_data_without_leaking_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RefusingService()
    replace_stage_service(
        monkeypatch,
        assemble_migration_chain,
        stage=MigrationStage.M06_GRANTS_GATEWAY,
        symbol=STAGE_EXECUTION[MigrationStage.M06_GRANTS_GATEWAY][0],
        service=service,
    )

    outcome = await run_partition_stage(
        key=key(),
        stage=MigrationStage.M06_GRANTS_GATEWAY,
        session_factory=object(),
        writer_generation=GENERATION,
        dry_run=False,
        overrides=complete_overrides(),
        evidence_source=RecordingEvidenceSource(promotion=promotion_record()),
    )

    assert outcome.blocked(PARTITION_STAGE_REFUSED)
    assert outcome.executed is False
    assert "m05_promotion_concurrent_append_conflict" in outcome.details[0]


async def test_an_unexpected_error_reports_its_class_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ExplodingService()
    replace_stage_service(
        monkeypatch,
        assemble_migration_chain,
        stage=MigrationStage.M06_GRANTS_GATEWAY,
        symbol=STAGE_EXECUTION[MigrationStage.M06_GRANTS_GATEWAY][0],
        service=service,
    )

    outcome = await run_partition_stage(
        key=key(),
        stage=MigrationStage.M06_GRANTS_GATEWAY,
        session_factory=object(),
        writer_generation=GENERATION,
        dry_run=False,
        overrides=complete_overrides(),
        evidence_source=RecordingEvidenceSource(promotion=promotion_record()),
    )

    assert outcome.blocked(PARTITION_STAGE_FAILED)
    assert outcome.executed is False
    # A message can carry a DSN; only the exception class may leave the runner.
    assert "RuntimeError" in outcome.details[0]
    assert "asyncpg" not in " ".join(outcome.details)
    assert "secret" not in " ".join(outcome.details)


# ---------------------------------------------------------------------------
# Caller errors: a bad argument is not a refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    (
        ({"dry_run": "yes"}, "truthy dry_run"),
        ({"stage": "M05_runtime"}, "a stage name is not a stage"),
        ({"key": ("tenant", "definition")}, "a bare pair is not a key"),
    ),
)
async def test_a_caller_error_raises_instead_of_reading_evidence(
    kwargs: dict[str, object], reason: str
) -> None:
    source = RecordingEvidenceSource()
    base: dict[str, object] = {
        "key": key(),
        "stage": MigrationStage.M05_INSTANCE_RUNTIME,
        "session_factory": object(),
        "writer_generation": GENERATION,
        "overrides": complete_overrides(),
        "evidence_source": source,
    }
    base.update(kwargs)

    with pytest.raises(ValueError):
        await run_partition_stage(**base)  # type: ignore[arg-type]
    assert source.reads == [], reason


async def test_the_plan_and_the_runner_agree_on_the_partition() -> None:
    """The runner never builds a plan for a different partition than the caller stated."""

    outcome = await run_partition_stage(
        key=key(),
        stage=MigrationStage.M04_SCOPE,
        session_factory=object(),
        writer_generation=GENERATION,
        overrides=complete_overrides(),
        evidence_source=RecordingEvidenceSource(),
    )
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    assert outcome.key == plan.key
    assert outcome.writer_generation == plan.writer_generation
    assert set(outcome.built) == set(plan.built)
