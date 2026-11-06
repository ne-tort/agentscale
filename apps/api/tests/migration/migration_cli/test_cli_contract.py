"""The operator CLI: it must report, and it must never pretend.

These are not tests of the migration chain -- that lives in ``tests/migration``.  They
test the properties that make the CLI safe to hand to a person:

* dry run is the default and writes nothing;
* a refusal is reported with the library's own code, not swallowed and not invented;
* ``--execute`` is required for a real run, and ``all`` refuses to execute;
* output never carries a payload, a DSN or a tenant's data;
* exit codes distinguish "command completed" from "chain refused" only when asked to.
"""

from __future__ import annotations

import json

import pytest

from prodavan import migration_cli
from prodavan.application.migration.composition import MigrationStage
from prodavan.application.migration.partition_chain import PartitionChainKey
from prodavan.application.migration.partition_runner import PartitionStageOutcome


def test_every_reviewed_stage_is_reachable_by_its_own_name() -> None:
    """`--stage` accepts exactly the reviewed names, and `all` is the whole chain."""

    for stage in migration_cli.STAGE_ORDER:
        assert migration_cli._stages_for(stage.value) == (stage,)

    assert migration_cli._stages_for("all") == migration_cli.STAGE_ORDER


def test_stage_order_matches_the_chain_dependency_order() -> None:
    """A stage may never be offered before the stage it depends on."""

    assert migration_cli.STAGE_ORDER == (
        MigrationStage.M04_SCOPE,
        MigrationStage.M04E_DEFINITION_VERSION,
        MigrationStage.M05_INSTANCE_RUNTIME,
        MigrationStage.M06_GRANTS_GATEWAY,
        MigrationStage.CUTOVER,
    )


def test_an_unknown_stage_name_is_refused_rather_than_defaulted() -> None:
    """A typo must not silently run a different stage."""

    with pytest.raises(SystemExit) as excinfo:
        migration_cli._stages_for("M05_runtme")

    assert "unknown stage" in str(excinfo.value)


def _outcome(
    *,
    executable: bool,
    dry_run: bool = True,
    blocker_codes: tuple[str, ...] = (),
    details: tuple[str, ...] = (),
) -> PartitionStageOutcome:
    return PartitionStageOutcome(
        key=PartitionChainKey(tenant_id="t-1", app_definition_id="d-1"),
        stage=MigrationStage.M04_SCOPE,
        writer_generation=3,
        dry_run=dry_run,
        executable=executable,
        blocker_codes=blocker_codes,
        details=details,
    )


def test_a_refusal_prints_the_code_and_the_reason(capsys: pytest.CaptureFixture[str]) -> None:
    outcome = _outcome(
        executable=False,
        blocker_codes=("partition_upstream_evidence_unavailable",),
        details=("M05UpstreamEvidence is the exact snapshot of one partition",),
    )

    migration_cli._print_outcome(outcome, as_json=False)

    printed = capsys.readouterr().out
    assert "partition_upstream_evidence_unavailable" in printed
    assert "M05UpstreamEvidence" in printed
    # A refusal must be visible as a refusal, not as an ordinary log line.
    assert "REFUSED" in printed


def test_the_json_form_is_the_snapshot_verbatim(capsys: pytest.CaptureFixture[str]) -> None:
    """``--json`` is machine-readable and is the library's own snapshot."""

    outcome = _outcome(executable=True)

    migration_cli._print_outcome(outcome, as_json=True)

    parsed = json.loads(capsys.readouterr().out)
    assert parsed == outcome.snapshot()
    assert parsed["stage"] == "M04_scope"
    assert parsed["dry_run"] is True


def test_the_printed_outcome_carries_no_dsn_and_no_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The only things printed are names, codes and counts."""

    outcome = _outcome(
        executable=False,
        blocker_codes=("partition_shadow_gateway_ports_unavailable",),
        details=("needs the live legacy owners",),
    )

    migration_cli._print_outcome(outcome, as_json=False)
    printed = capsys.readouterr().out

    for forbidden in ("postgresql", "asyncpg", "@", "password", "SELECT"):
        assert forbidden not in printed


def test_verdict_reports_success_only_when_every_stage_is_executable() -> None:
    assert migration_cli._print_verdict([_outcome(executable=True)], as_json=False) is True


def test_verdict_names_the_first_blocker_of_each_refused_stage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict = migration_cli._print_verdict(
        [
            _outcome(executable=True),
            _outcome(
                executable=False,
                blocker_codes=("partition_writer_generation_invalid", "other"),
            ),
        ],
        as_json=False,
    )

    assert verdict is False
    printed = capsys.readouterr().out
    assert "1 of 2 stage(s) refused" in printed
    assert "partition_writer_generation_invalid" in printed


@pytest.mark.parametrize("command", ["plan", "run", "cutover-status"])
def test_every_subcommand_requires_a_partition(command: str) -> None:
    """No command may run against "whatever partition the first row is"."""

    with pytest.raises(SystemExit):
        migration_cli.main([command])


def test_execute_is_refused_for_all_stages_at_once() -> None:
    """`all` is plan-only: a partial real run is not distinguishable from a refusal."""

    with pytest.raises(SystemExit) as excinfo:
        migration_cli.main(
            [
                "run",
                "--tenant",
                "t-1",
                "--definition",
                "d-1",
                "--stage",
                "all",
                "--execute",
            ]
        )

    assert "one --stage" in str(excinfo.value)


def test_run_without_execute_is_a_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Forgetting ``--execute`` must produce a plan, never a write."""

    observed: list[bool] = []

    async def fake_run(**kwargs: object) -> PartitionStageOutcome:
        observed.append(bool(kwargs["dry_run"]))
        return _outcome(executable=True, dry_run=bool(kwargs["dry_run"]))

    monkeypatch.setattr(migration_cli, "run_partition_stage", fake_run)

    code = migration_cli.main(
        ["run", "--tenant", "t-1", "--definition", "d-1", "--stage", "M04_scope"]
    )

    assert code == 0
    assert observed == [True]


def test_a_refusal_only_fails_the_exit_code_when_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A correctly refused cutover is a successful *command*; CI opts into strictness."""

    async def fake_run(**kwargs: object) -> PartitionStageOutcome:
        return _outcome(
            executable=False,
            dry_run=bool(kwargs["dry_run"]),
            blocker_codes=("partition_stage_precondition_unmet",),
        )

    monkeypatch.setattr(migration_cli, "run_partition_stage", fake_run)
    base = ["run", "--tenant", "t-1", "--definition", "d-1", "--stage", "M04_scope"]

    assert migration_cli.main(base) == 0
    assert migration_cli.main([*base, "--strict"]) == 2


def test_the_generation_flag_is_passed_through_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI must not clamp, default or repair a writer generation."""

    seen: list[int] = []

    async def fake_run(**kwargs: object) -> PartitionStageOutcome:
        seen.append(int(kwargs["writer_generation"]))  # type: ignore[arg-type]
        return _outcome(executable=True)

    monkeypatch.setattr(migration_cli, "run_partition_stage", fake_run)

    migration_cli.main(
        [
            "plan",
            "--tenant",
            "t-1",
            "--definition",
            "d-1",
            "--stage",
            "M04_scope",
            "--generation",
            "7",
        ]
    )

    assert seen == [7]
