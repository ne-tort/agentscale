"""Operator entry point for the migration chain.

Why this exists
===============

The whole chain -- M04 scope, M04E definition/version, M05 instance/runtime, M06
grants/gateway and the cutover command -- is assembled by
``application/migration/composition.py`` and reachable from a delivered process through
``MigrationChainResource``.  But until this module there was no way for an *operator* to
run it: the machinery was only reachable from tests, which means "cutover" was a claim
about code rather than something a person could perform.

This is deliberately a small, boring command with three properties that matter more than
features:

1. **Dry run is the default and the only mode that runs without an explicit flag.**
   ``plan`` builds the partition, evaluates every precondition and prints what it found.
   Nothing is written.  A real run needs ``--execute`` and, for cutover, a second flag;
   the chain's own gates still refuse independently of anything typed here.
2. **Output is codes, never payloads.**  Every line is a name, a code or a count.  A
   blocker can say ``partition_shadow_gateway_ports_unavailable``; it must never print a
   DSN, a manifest or a tenant's data.  ``PartitionStageOutcome.snapshot()`` already
   guarantees this, and the printer below only reads named fields.
3. **Refusal is a first-class result.**  Exit code 0 means "the command completed", not
   "the migration succeeded".  An executable dry run exits 0; a run that the chain
   refuses also exits 0 and prints the refusal, because a correctly refused cutover is a
   successful command.  ``--strict`` turns a refusal into exit 1 for CI use.

Usage
=====

    python -m prodavan.migration_cli partitions
    python -m prodavan.migration_cli plan    --tenant <id> --definition <id>
    python -m prodavan.migration_cli plan    --tenant <id> --definition <id> --stage M05_runtime
    python -m prodavan.migration_cli run     --tenant <id> --definition <id> --stage M04_scope --execute

``--stage`` accepts the reviewed stage names (``M04_scope``, ``M04E_definition_version``,
``M05_runtime``, ``M06_grants_gateway``, ``cutover``); ``all`` plans every stage in
order, which is the only sensible thing to do without ``--execute``.

What this command will not do
=============================

It does not open a partition's gates, synthesize evidence, fabricate a promotion record,
or supply a collaborator the partition cannot derive.  Missing collaborators are printed
as blockers with the code the library assigns them.  A cutover attempted while a gate is
closed is refused by ``CutoverCommandService`` even with every flag set.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from prodavan.application.migration.composition import MigrationStage
from prodavan.application.migration.partition_chain import PartitionChainKey
from prodavan.application.migration.partition_runner import (
    PartitionStageOutcome,
    run_partition_stage,
)

#: Stage names in chain order.  ``cutover`` last because it depends on all of them.
STAGE_ORDER: tuple[MigrationStage, ...] = (
    MigrationStage.M04_SCOPE,
    MigrationStage.M04E_DEFINITION_VERSION,
    MigrationStage.M05_INSTANCE_RUNTIME,
    MigrationStage.M06_GRANTS_GATEWAY,
    MigrationStage.CUTOVER,
)

_BY_NAME = {stage.value: stage for stage in STAGE_ORDER}
_BY_NAME["all"] = MigrationStage.M04_SCOPE  # only a placeholder; ``all`` is handled apart


def _stages_for(name: str) -> tuple[MigrationStage, ...]:
    """Resolve ``--stage`` to the stages to touch.

    ``all`` means every stage in chain order.  Anything else must be an exact reviewed
    stage name: a typo is refused rather than silently defaulting to something.
    """

    if name == "all":
        return STAGE_ORDER
    stage = _BY_NAME.get(name)
    if stage is None:
        known = ", ".join(stage.value for stage in STAGE_ORDER)
        raise SystemExit(f"unknown stage {name!r}; known: all, {known}")
    return (stage,)


def _print_outcome(outcome: PartitionStageOutcome, *, as_json: bool) -> None:
    """One outcome.  Names, codes and counts only -- see the module docstring."""

    snapshot = outcome.snapshot()
    if as_json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
        return

    mode = "dry-run" if outcome.dry_run else "execute"
    head = (
        f"[{outcome.stage.value}] {mode} "
        f"partition=({outcome.key.tenant_id}, {outcome.key.app_definition_id}) "
        f"generation={outcome.writer_generation}"
    )
    print(head)
    print(f"  executable: {outcome.executable}")
    print(f"  executed:   {outcome.executed}")
    if outcome.services:
        print(f"  services:   {', '.join(outcome.services)}")
    if outcome.built:
        print(f"  built:      {', '.join(outcome.built)}")
    for code, detail in zip(outcome.blocker_codes, outcome.details, strict=False):
        print(f"  REFUSED {code}: {detail}")
    if outcome.stage_blocker_codes:
        for code in outcome.stage_blocker_codes:
            print(f"  stage blocker: {code}")


def _print_verdict(outcomes: Sequence[PartitionStageOutcome], *, as_json: bool) -> bool:
    """True when every stage was executable.  A refusal is reported, not hidden.

    In ``--json`` mode nothing is printed: the per-stage snapshots are already on stdout
    and adding prose would make the stream unparseable to the caller that asked for JSON.
    """

    refused = [o for o in outcomes if not o.executable]
    if as_json:
        return not refused
    if not refused:
        print("\nall stages executable")
        return True
    print(f"\n{len(refused)} of {len(outcomes)} stage(s) refused")
    for outcome in refused:
        first = outcome.blocker_codes[0] if outcome.blocker_codes else "unknown"
        print(f"  {outcome.stage.value}: {first}")
    return False


async def _cmd_partitions(*, as_json: bool) -> int:
    """List the partitions that exist in the database.

    A partition is ``(tenant_id, app_definition_id)`` over the *existing* legacy
    definitions, so this is a read of what the database says, not a guess about what
    should be migrated.  Definitions without a tenant cannot be addressed as a partition
    and are counted apart rather than silently dropped.

    Note on the source table.  ``dynamic_app_definitions`` is a *retained* legacy table:
    no Alembic revision creates it, because in production it predates the migration.  On a
    database built from revisions alone the table is therefore absent, and that is not an
    error -- it means there is no legacy data to migrate.  Reporting it as absent is the
    honest answer; creating it here would invent source rows.
    """

    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import select

    from prodavan.infrastructure.persistence.database import get_session_factory
    from prodavan.infrastructure.persistence.models.dynamic_apps import AppDefinitionRow

    factory = get_session_factory()
    async with factory() as session:
        present = await session.run_sync(
            lambda sync_session: sa_inspect(sync_session.connection()).has_table(
                "dynamic_app_definitions"
            )
        )
        if not present:
            message = (
                "legacy source table dynamic_app_definitions is absent, so there is "
                "nothing to migrate from it: this database was built from Alembic "
                "revisions alone and has no legacy definitions"
            )
            if as_json:
                print(
                    json.dumps(
                        {
                            "partitions": [],
                            "definitions_without_tenant": 0,
                            "source_table_present": False,
                            "note": message,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                print(message)
            return 0
        rows = (
            await session.execute(
                select(
                    AppDefinitionRow.tenant_id,
                    AppDefinitionRow.id,
                ).order_by(
                    AppDefinitionRow.tenant_id,
                    AppDefinitionRow.id,
                )
            )
        ).all()

    addressable = [(tenant, definition) for tenant, definition in rows if tenant]
    unaddressable = [row for row in rows if not row[0]]

    if as_json:
        print(
            json.dumps(
                {
                    "partitions": [
                        {"tenant_id": tenant, "app_definition_id": definition}
                        for tenant, definition in addressable
                    ],
                    "definitions_without_tenant": len(unaddressable),
                    "source_table_present": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(f"{len(addressable)} addressable partition(s)")
    for tenant, definition in addressable:
        print(f"  tenant={tenant} definition={definition}")
    if unaddressable:
        # Named, not hidden: a definition with no tenant cannot be a partition, and
        # pretending otherwise would make the inventory look complete when it is not.
        print(
            f"\n{len(unaddressable)} definition(s) have no tenant_id and cannot form a "
            "partition; they need a tenant before they can be migrated"
        )
    return 0


async def _cmd_plan_or_run(
    *,
    tenant: str,
    definition: str,
    stage_name: str,
    execute: bool,
    generation: int,
    as_json: bool,
) -> int:
    """Plan (default) or execute one stage of one partition."""

    key = PartitionChainKey(tenant_id=tenant, app_definition_id=definition)
    stages = _stages_for(stage_name)

    if execute and len(stages) > 1:
        # Running every stage for real in one command would make a partial failure
        # indistinguishable from a refusal, and the stages have strict dependencies.
        raise SystemExit("--execute needs one --stage; 'all' is plan-only")

    from prodavan.infrastructure.persistence.database import get_session_factory

    factory = get_session_factory()
    outcomes = [
        await run_partition_stage(
            key=key,
            stage=stage,
            session_factory=factory,
            writer_generation=generation,
            dry_run=not execute,
        )
        for stage in stages
    ]

    for outcome in outcomes:
        _print_outcome(outcome, as_json=as_json)

    return 0 if _print_verdict(outcomes, as_json=as_json) else 2


async def _cmd_cutover_status(*, tenant: str, definition: str, as_json: bool) -> int:
    """Report the stored cutover lineage of a partition, if the table can be read.

    Absence is reported as absence.  This command never reports "no rows" as "not
    migrated": a partition with no lineage row may simply never have been attempted, and
    the two are different facts.
    """

    from sqlalchemy import text

    from prodavan.infrastructure.persistence.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # The GUC is what the RLS policies read; without it FORCE RLS returns nothing and
        # that empty result would be indistinguishable from "never migrated".
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": tenant},
        )
        rows = (
            await session.execute(
                text(
                    "SELECT revision, action, writer_epoch, canonical_positive_writes, "
                    "occurred_at, resulting_state FROM m06_partition_cutover_evidence "
                    "WHERE tenant_id = :tenant AND app_definition_id = :definition "
                    "ORDER BY revision DESC LIMIT 20"
                ),
                {"tenant": tenant, "definition": definition},
            )
        ).all()

    records = []
    for revision, action, writer_epoch, positive_writes, occurred_at, state in rows:
        # Owners live in the stored state snapshot, not in their own columns.  A row whose
        # state cannot be read is reported as unreadable rather than assumed to be safe.
        owners = {"decision_owner": None, "canonical_read_owner": None}
        try:
            parsed = json.loads(state) if state else None
            if isinstance(parsed, dict):
                for key in owners:
                    value = parsed.get(key)
                    owners[key] = value if isinstance(value, str) else None
        except (TypeError, ValueError):
            owners = {"decision_owner": "unreadable", "canonical_read_owner": "unreadable"}
        records.append(
            {
                "revision": revision,
                "action": action,
                "writer_epoch": writer_epoch,
                "canonical_positive_writes": bool(positive_writes),
                "occurred_at": str(occurred_at),
                "decision_owner": owners["decision_owner"],
                "canonical_read_owner": owners["canonical_read_owner"],
            }
        )

    if as_json:
        print(
            json.dumps(
                {
                    "partition": {
                        "tenant_id": tenant,
                        "app_definition_id": definition,
                    },
                    "lineage": records,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if not records:
        print(
            "no cutover lineage recorded for this partition "
            "(this means no cutover row exists, not that cutover happened)"
        )
        return 0
    print(f"{len(records)} cutover row(s), newest first")
    for record in records:
        print(
            f"  revision={record['revision']} action={record['action']} "
            f"writer_epoch={record['writer_epoch']} "
            f"decision_owner={record['decision_owner']} "
            f"canonical_read_owner={record['canonical_read_owner']} "
            f"canonical_positive_writes={record['canonical_positive_writes']}"
        )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prodavan.migration_cli",
        description=(
            "Plan or run the migration chain for one partition. Dry run is the default; "
            "a real run needs --execute and the chain's own gates."
        ),
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("partitions", help="list addressable partitions in the database")

    def add_partition_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--tenant", required=True, help="tenant_id (company id)")
        target.add_argument(
            "--definition", required=True, help="app_definition_id"
        )
        target.add_argument(
            "--generation",
            type=int,
            default=0,
            help="writer generation the caller observed (0 means 'not yet published')",
        )

    plan = sub.add_parser("plan", help="evaluate a stage's preconditions, write nothing")
    add_partition_args(plan)
    plan.add_argument(
        "--stage", default="all", help="stage name or 'all' (default: all)"
    )

    run = sub.add_parser("run", help="execute one stage (requires --execute)")
    add_partition_args(run)
    run.add_argument("--stage", required=True, help="exactly one stage name")
    run.add_argument(
        "--execute",
        action="store_true",
        help="actually run; without it this is the same as plan",
    )
    run.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when the chain refuses (for CI)",
    )

    status = sub.add_parser("cutover-status", help="report stored cutover lineage")
    add_partition_args(status)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "partitions":
        return asyncio.run(_cmd_partitions(as_json=args.json))

    if args.command == "plan":
        return asyncio.run(
            _cmd_plan_or_run(
                tenant=args.tenant,
                definition=args.definition,
                stage_name=args.stage,
                execute=False,
                generation=args.generation,
                as_json=args.json,
            )
        )

    if args.command == "run":
        code = asyncio.run(
            _cmd_plan_or_run(
                tenant=args.tenant,
                definition=args.definition,
                stage_name=args.stage,
                execute=args.execute,
                generation=args.generation,
                as_json=args.json,
            )
        )
        # Refusal (2) becomes a hard failure only when asked for.
        if code == 2 and not args.strict:
            return 0
        return code

    if args.command == "cutover-status":
        return asyncio.run(
            _cmd_cutover_status(
                tenant=args.tenant, definition=args.definition, as_json=args.json
            )
        )

    return 2


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
