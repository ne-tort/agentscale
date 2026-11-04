"""MIG-REVIEW-SEAMS-01 — static seam contract M04 -> M04E -> M05 -> M06 -> cutover.

Authored during the migration **verification freeze**: nothing in this module was executed.
The tests never import the application under test (no settings, no database, no fixtures):
they parse source text and ``ast`` so a seam regression fails on a clean checkout.

Companion review: ``docs/08-migration/integration-seams-review.md``.

What is pinned here:

* every critical seam port is declared where the review says, and is still a ``Protocol``;
* every declared port has an implementation, or an explicitly reviewed gap;
* every implementation has a caller outside its own module, or is a reviewed orphan;
* the M04E -> M05 handoff marker still carries every field M05 reads from it;
* the M05 -> M06 handoff gap is pinned: ``PromotionRecord`` cannot satisfy
  ``AcceptedPartitionEvidence`` without a mapper (no shared field name at all);
* one concept still has two names/domains: ``writer_generation`` vs ``writer_epoch``;
* M04E and M05 agree on the six handoff gate names, M05 status gates and the cutover
  ``GateStage`` vocabulary still differ (documented mismatch, not silent drift);
* the digest canonicalizer is unique except the reviewed cutover deviation;
* duplicate contract names stay exactly as reviewed;
* blocker codes stay classified: every ``blocker=`` literal in ``src/prodavan`` and every
  blocker constant inside the migration zone has a verdict, and no recorded code went stale.

Deliberately out of scope of the last check: blocker constants of ``application/agent_runs/**``,
``core/infra/**`` and ``api/**``. They belong to the agent-run host wiring (review §8.1) and are
churning; the review records their verdicts, and :func:`test_classified_blockers_still_exist`
still fails if one of the recorded codes disappears.

Registry maintenance rule
-------------------------
``SEAM_PORTS`` records *facts about this tree*, so an entry must match the source, in both
directions:

* a port with no implementing class is a ``reviewed_gap`` (the review §3 gaps G1-G7 as first
  written), and the gap text must name the reason;
* a port whose implementation has landed names that ``(module, symbol)`` -- **anchored at the
  module that declares the class**, not at a module that merely imports it. Anchoring at the
  importer is how this registry previously pointed ``FailClosedResolvePluginInstance`` at
  ``instance_persistence.py`` (it is declared in ``runtime_service.py``), which made the
  implementation check look for a class that module does not define;
* an implementation nothing constructs outside its own module needs a ``REVIEWED_ORPHANS``
  note saying whether that is a reviewed boundary or a forgotten wiring.

As of this revision no seam port is a ``reviewed_gap``: G1-G4 closed in
``application/migration/evidence_ports.py``, G6 in ``application/migration/m04e_ports.py`` and
G5 in ``application/migration/m06_cutover/ports.py``. The ``reviewed_gap`` branch stays in the
test for the next port that arrives without an implementation; every remaining implementation
is a module-local orphan with a recorded reason. ``docs/08-migration/integration-seams-review.md``
§3 still describes the gaps as open and needs the same update from its owner.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

# apps/api/tests/migration/integration_seams/test_seam_contract.py -> apps/api
API_ROOT = Path(__file__).resolve().parents[3]
SRC = API_ROOT / "src" / "prodavan"
REVIEW_DOC = API_ROOT.parents[1] / "docs" / "08-migration" / "integration-seams-review.md"


# ---------------------------------------------------------------------------
# static source helpers (no imports of prodavan.*)
# ---------------------------------------------------------------------------


def _read(relative: str) -> str:
    return (SRC / relative).read_text(encoding="utf-8")


def _source_files() -> dict[str, str]:
    return {
        path.relative_to(SRC).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(SRC.rglob("*.py"))
    }


def _parse(relative: str) -> ast.Module:
    return ast.parse(_read(relative), filename=relative)


def _class_node(relative: str, name: str) -> ast.ClassDef | None:
    for node in _parse(relative).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _class_fields(relative: str, name: str) -> tuple[str, ...]:
    node = _class_node(relative, name)
    if node is None:
        raise AssertionError(f"{name} is not declared in {relative}")
    return tuple(
        statement.target.id
        for statement in node.body
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
    )


def _method_return_annotation(relative: str, class_name: str, method_name: str) -> str:
    node = _class_node(relative, class_name)
    if node is None:
        raise AssertionError(f"{class_name} is not declared in {relative}")
    for statement in node.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name == method_name:
            return ast.unparse(statement.returns) if statement.returns is not None else ""
    raise AssertionError(f"{class_name}.{method_name} is not declared in {relative}")


def _references(files: dict[str, str], needle: str, *, excluding: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        relative
        for relative, text in files.items()
        if relative not in excluding and needle in text
    )


def _class_definitions(files: dict[str, str]) -> dict[str, tuple[str, ...]]:
    index: dict[str, list[str]] = {}
    for relative, text in files.items():
        for match in re.finditer(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)", text, flags=re.MULTILINE):
            index.setdefault(match.group(1), []).append(relative)
    return {name: tuple(sorted(paths)) for name, paths in index.items()}


# ---------------------------------------------------------------------------
# seam registry (review §2 / §3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeamPort:
    name: str
    declared_in: str
    implementations: tuple[tuple[str, str], ...]  # (module, symbol)
    reviewed_gap: str | None = None


SEAM_PORTS: tuple[SeamPort, ...] = (
    SeamPort(
        name="ResolveScopePort",
        declared_in="application/migration/m04_scope/scope_migration.py",
        implementations=(
            ("application/migration/scope_registry_adapter/adapter.py", "ScopeRegistryApplicationAdapter"),
            (
                "infrastructure/persistence/migration_scope/adapter.py",
                "SqlAlchemyTrustedScopeTransactionPort",
            ),
        ),
    ),
    SeamPort(
        name="M05HandoffPort",
        declared_in="application/migration/m04e_definition_version/backfill.py",
        implementations=(
            (
                "application/migration/evidence_ports.py",
                "SqlM05HandoffEvidenceStore",
            ),
        ),
    ),
    SeamPort(
        name="M05PromotionRecordPort",
        declared_in="application/migration/m05_instance_runtime/runtime_service.py",
        implementations=(
            (
                "application/migration/evidence_ports.py",
                "SqlM05PromotionEvidenceStore",
            ),
        ),
    ),
    SeamPort(
        name="PartitionHandoffReadPort",
        declared_in="application/migration/m06_grants_gateway/handoff.py",
        implementations=(
            (
                "application/migration/evidence_ports.py",
                "SqlPartitionHandoffReadStore",
            ),
        ),
    ),
    SeamPort(
        name="EvidenceStore",
        declared_in="application/migration/m04e_definition_version/backfill.py",
        implementations=(
            (
                "application/migration/evidence_ports.py",
                "SqlM04EBackfillEvidenceStore",
            ),
        ),
    ),
    SeamPort(
        name="UpstreamGatePort",
        declared_in="application/migration/m04e_definition_version/backfill.py",
        implementations=(
            ("application/migration/m04e_ports.py", "SqlM04EUpstreamGate"),
        ),
    ),
    SeamPort(
        name="CandidateProofPort",
        declared_in="application/migration/m04e_definition_version/backfill.py",
        implementations=(
            ("application/migration/m04e_ports.py", "SqlM04ECandidateProof"),
        ),
    ),
    SeamPort(
        name="CutoverRepository",
        declared_in="application/migration/m06_cutover/cutover.py",
        implementations=(
            ("application/migration/m06_cutover/ports.py", "SqlCutoverRepository"),
        ),
    ),
    SeamPort(
        name="GateVerifier",
        declared_in="application/migration/m06_cutover/cutover.py",
        implementations=(
            ("application/migration/m06_cutover/ports.py", "SqlGateVerifier"),
        ),
    ),
    SeamPort(
        name="LegacyOwnerVerifier",
        declared_in="application/migration/m06_cutover/cutover.py",
        implementations=(
            ("application/migration/m06_cutover/ports.py", "SqlLegacyOwnerVerifier"),
        ),
    ),
    SeamPort(
        name="RollbackVerifier",
        declared_in="application/migration/m06_cutover/cutover.py",
        implementations=(
            ("application/migration/m06_cutover/ports.py", "SqlRollbackVerifier"),
        ),
    ),
    SeamPort(
        name="CanonicalRepairVerifier",
        declared_in="application/migration/m06_cutover/cutover.py",
        implementations=(
            ("application/migration/m06_cutover/ports.py", "SqlCanonicalRepairVerifier"),
        ),
    ),
    SeamPort(
        name="EvidenceReadPort",
        declared_in="application/migration/m06_grants_gateway/adapter.py",
        implementations=(
            (
                "application/migration/m06_grants_gateway/evidence_store.py",
                "SqlCanonicalEvidenceStore",
            ),
        ),
    ),
    SeamPort(
        name="TrustedEvidenceTransactionPort",
        declared_in="application/migration/m06_grants_gateway/adapter.py",
        implementations=(
            (
                "application/migration/m06_grants_gateway/evidence_store.py",
                "SqlCanonicalEvidenceStore",
            ),
        ),
    ),
    SeamPort(
        name="ResolvePluginInstancePort",
        declared_in="application/migration/m05_instance_runtime/runtime_service.py",
        implementations=(
            # The fail-closed gate is *declared* in ``runtime_service.py`` next to the
            # protocol; ``instance_persistence.py`` only imports it to wrap the SQL reader in
            # ``build_sql_resolve_plugin_instance_port``. Anchoring it at the importer made
            # ``_class_node`` look for a class the module does not define.
            (
                "application/migration/m05_instance_runtime/runtime_service.py",
                "FailClosedResolvePluginInstance",
            ),
        ),
    ),
    SeamPort(
        name="RuntimeInstanceLifecycle",
        declared_in="application/migration/m05_instance_runtime/runtime_service.py",
        implementations=(
            (
                "application/migration/m05_instance_runtime/instance_persistence.py",
                "SqlAlchemyRuntimeInstanceLifecycle",
            ),
        ),
    ),
    SeamPort(
        name="M05IdentityEvidencePort",
        declared_in="application/migration/m05_instance_runtime/runtime_service.py",
        implementations=(
            (
                "application/migration/m05_instance_runtime/instance_persistence.py",
                "SqlAlchemyM05IdentityEvidence",
            ),
        ),
    ),
    SeamPort(
        name="InstanceImportStore",
        declared_in="application/migration/m05_instance_runtime/runtime_service.py",
        implementations=(
            (
                "application/migration/m05_instance_runtime/instance_persistence.py",
                "SqlAlchemyInstanceImportStore",
            ),
        ),
    ),
    SeamPort(
        name="InstanceQuarantineRetryPort",
        declared_in="application/migration/m05_instance_runtime/runtime_service.py",
        implementations=(
            (
                "application/migration/m05_instance_runtime/instance_persistence.py",
                "SqlAlchemyInstanceQuarantineStore",
            ),
        ),
    ),
    SeamPort(
        name="RedactedRuntimeAuditPort",
        declared_in="application/migration/m05_instance_runtime/runtime_service.py",
        implementations=(
            (
                "application/migration/m05_instance_runtime/instance_persistence.py",
                "SqlAlchemyRedactedAuditSink",
            ),
        ),
    ),
    SeamPort(
        name="DependencyHandoffPort",
        declared_in="application/migration/m06_grants_gateway/adapter.py",
        implementations=(
            ("application/migration/m06_grants_gateway/handoff.py", "ServerResolvedHandoffResolver"),
        ),
    ),
)


# Implementations whose only instantiator lives in their own module or in an uncalled
# factory (review §4).  Listing them keeps the caller test honest in both directions:
# wiring a symbol removes it from here, and adding a new orphan without a note fails.
REVIEWED_ORPHANS: dict[str, str] = {
    "ScopeRegistryApplicationAdapter": "O1: no composition root instantiates the M04 adapter",
    "SqlAlchemyTrustedScopeTransactionPort": "O2: only referenced by the M04 adapter typing",
    "SqlAlchemyRuntimeInstanceLifecycle": "only built by build_sql_resolve_plugin_instance_port",
    "SqlAlchemyM05IdentityEvidence": "built inside the same module only",
    "SqlAlchemyInstanceImportStore": "built inside the same module only",
    "SqlAlchemyInstanceQuarantineStore": "built inside the same module only",
    "SqlAlchemyRedactedAuditSink": "built inside the same module only",
    # The four durable evidence ports closed review §3 G1-G4, but nothing in ``src`` builds
    # them yet: no composition stage takes them as a dependency, so a host must instantiate
    # them itself. That is a wiring task with an owner, not a missing implementation, so it is
    # recorded here instead of being hidden behind a "no implementation" gap entry.
    "SqlM05HandoffEvidenceStore":
        "G1 closed: durable, but no composition root constructs it yet",
    "SqlM05PromotionEvidenceStore":
        "G2 closed: durable, but no composition root constructs it yet",
    "SqlPartitionHandoffReadStore":
        "G3 closed: the M06 stage takes the port but nothing builds this store yet",
    "SqlM04EBackfillEvidenceStore":
        "G4 closed: durable, but the M04E stage builder does not construct it yet",
    # G5 likewise: ``ports.py`` implements the state store and all four verifiers, but the
    # only constructor is ``build_cutover_ports`` in that same module; no composition root
    # calls it (``build_cutover_stage`` takes the ports as host-supplied arguments).
    "SqlCutoverRepository": "G5 closed: built only by build_cutover_ports in the same module",
    "SqlGateVerifier": "G5 closed: built only by build_cutover_ports in the same module",
    "SqlLegacyOwnerVerifier": "G5 closed: built only by build_cutover_ports in the same module",
    "SqlRollbackVerifier": "G5 closed: built only by build_cutover_ports in the same module",
    "SqlCanonicalRepairVerifier":
        "G5 closed: built only by build_cutover_ports in the same module",
    # G6: implemented in ``m04e_ports.py``, which builds them in ``build_m04e_sql_ports``;
    # no composition root calls that factory, so they stay module-local orphans.
    "SqlM04EUpstreamGate":
        "G6 closed: built only by build_m04e_sql_ports in the same module",
    "SqlM04ECandidateProof":
        "G6 closed: built only by build_m04e_sql_ports in the same module",
}


# Review §7: services of the chain that no process (api/, core/) composition instantiates.
PROCESS_WIRING_GAPS: tuple[str, ...] = (
    "ScopeRegistryApplicationAdapter",
    "DefinitionVersionEvidenceBackfill",
    "ScopeMigrationCoordinator",
    "InstanceImportService",
    "M05PromotionService",
    "BoundedParityRunner",
    "CutoverCommandService",
)


# ---------------------------------------------------------------------------
# cross-seam field contracts (review §5)
# ---------------------------------------------------------------------------

M04E_MARKER = ("application/migration/m04e_definition_version/backfill.py", "M05HandoffMarker")
M05_PROMOTION = ("application/migration/m05_instance_runtime/runtime_service.py", "PromotionRecord")
M06_ACCEPTED = ("application/migration/m06_grants_gateway/handoff.py", "AcceptedPartitionEvidence")

# Fields M05 reads off the M04E marker (runtime_service.py, marker_ready block).
MARKER_FIELDS_REQUIRED_BY_M05: frozenset[str] = frozenset(
    {
        "record_id",
        "task_id",
        "status",
        "partition",
        "source_snapshot_digest",
        "canonical_digest",
        "checkpoint",
        "accepted_count",
        "quarantined_count",
        "gate_evidence",
        "actor",
        "recorded_at",
        "decision_owner",
        "correlation_id",
        "writer_generation",
        "canonical_positive_writes",
    }
)

# Fields M06 needs from upstream evidence that the M05 promotion record cannot supply.
PINNED_HANDOFF_GAP: frozenset[str] = frozenset(
    {
        "accepted_sources",
        "inventory_digest",
        "m05_instance_digest",
        "writer_epoch",
        "m05_instance_runtime_evidence_id",
        "m04_scope_evidence_id",
        "m04_rls_evidence_id",
        "m04e_definition_version_evidence_id",
        "atomic_evidence_id",
        "m06_schema_identity_digest",
    }
)

EXPECTED_HANDOFF_GATES: frozenset[str] = frozenset(
    {
        "MIG-M04-01",
        "MIG-M04-02",
        "MIG-M04E-01",
        "active_typed_parent_scope",
        "same_definition_version",
        "immutable_artifact",
    }
)

EXPECTED_CUTOVER_STAGES: frozenset[str] = frozenset({"M04", "M04E", "M05"})

# M05 status gates are named differently from both M04E gate IDs and cutover stages (I6).
M05_GATE_NAMES: frozenset[str] = frozenset({"M04_scope", "M04E_definition_version", "M05_runtime"})


# ---------------------------------------------------------------------------
# digest canonicalizer (review I8 / D8)
# ---------------------------------------------------------------------------

DIGEST_MODULES: tuple[str, ...] = (
    "application/migration/m04e_definition_version/backfill.py",
    "application/migration/m05_instance_runtime/runtime_service.py",
    "application/migration/m06_grants_gateway/adapter.py",
    "application/migration/m06_cutover/cutover.py",
)

# Reviewed deviation: cutover serializes without ensure_ascii=True/default=, so non-ASCII
# payloads produce different bytes than the other canonicalizers.
PINNED_DIGEST_DEVIATIONS: frozenset[str] = frozenset(
    {"application/migration/m06_cutover/cutover.py"}
)


# ---------------------------------------------------------------------------
# duplicate contract names (review §6)
# ---------------------------------------------------------------------------

EXPECTED_DUPLICATE_COUNTS: dict[str, int] = {
    "CapabilityGatewayService": 2,
    "PromotionRecord": 4,
    "GateEvidence": 2,
    "HandoffStatus": 3,
    "FinalDeltaEvidence": 2,
    "PartitionKey": 2,
    "ParityEvidence": 1,
    "ParityRunReport": 1,
}


# ---------------------------------------------------------------------------
# blocker-code classification (review §8)
# ---------------------------------------------------------------------------

CLASSIFICATION_DEFECT = "defect_forgotten_wiring"
CLASSIFICATION_BOUNDARY = "documented_boundary"
CLASSIFICATION_COMPOSITION = "implemented_but_no_caller"
CLASSIFICATION_FALLBACK = "post_failure_fallback_code"

BLOCKER_CLASSIFICATION: dict[str, str] = {
    # --- agent runs / control plane (verdicts recorded, scope outside the zone scan) ---
    "agent_control_plane_atomic_transaction_adapter_unwired": CLASSIFICATION_BOUNDARY,
    "agent_control_plane_aggregate_cursor_missing": CLASSIFICATION_BOUNDARY,
    "agent_run_provider_attempt_journal_unwired": CLASSIFICATION_BOUNDARY,
    "agent_run_provider_attempt_table_missing": CLASSIFICATION_BOUNDARY,
    "agent_run_unknown_case_journal_unwired": CLASSIFICATION_BOUNDARY,
    "agent_run_control_plane_actor_unresolved": CLASSIFICATION_BOUNDARY,
    "agent_run_retry_due_start_transition_unwired": CLASSIFICATION_BOUNDARY,
    "agent_run_lease_storage_missing": CLASSIFICATION_DEFECT,
    "agent_run_requeue_transition_unwired": CLASSIFICATION_BOUNDARY,
    "agent_run_retry_journal_unwired": CLASSIFICATION_DEFECT,
    "agent_run_retry_schedules_table_missing": CLASSIFICATION_BOUNDARY,
    "agent_run_unknown_outcome_journal_unwired": CLASSIFICATION_DEFECT,
    "agent_run_unknown_outcomes_table_missing": CLASSIFICATION_BOUNDARY,
    "agent_run_durable_claim_and_lease_unwired": CLASSIFICATION_DEFECT,
    "agent_run_provider_outcome_reconciliation_unwired": CLASSIFICATION_DEFECT,
    "agent_run_execution_port_unwired": CLASSIFICATION_DEFECT,
    "agent_run_host_dependencies_unwired": CLASSIFICATION_BOUNDARY,
    "agent_run_run_input_source_unwired": CLASSIFICATION_BOUNDARY,
    "agent_run_turn_runner_unwired": CLASSIFICATION_BOUNDARY,
    "intent_handler_not_wired": CLASSIFICATION_DEFECT,
    # --- web facade / streams ---
    "web_stream_session_resolver_unwired": CLASSIFICATION_BOUNDARY,
    "durable_run_sequence_index_missing": CLASSIFICATION_BOUNDARY,
    "web_chat_stream_facade_unwired": CLASSIFICATION_BOUNDARY,
    "web_stream_run_index_evidence_missing": CLASSIFICATION_BOUNDARY,
    # --- equipment domain ---
    "equipment_domain_transaction_adapter_unwired": CLASSIFICATION_COMPOSITION,
    # --- automation ingress (service never constructed; its repository is an orphan O3) ---
    "subscription_resolver_unwired": CLASSIFICATION_BOUNDARY,
    "subscription_resolution_unavailable": CLASSIFICATION_FALLBACK,
    "signature_verifier_unwired": CLASSIFICATION_BOUNDARY,
    "signature_verification_unavailable": CLASSIFICATION_FALLBACK,
    "project_trigger_resolver_unwired": CLASSIFICATION_BOUNDARY,
    "project_trigger_resolution_unavailable": CLASSIFICATION_FALLBACK,
    "trigger_fence_unwired": CLASSIFICATION_BOUNDARY,
    "trigger_fence_unavailable": CLASSIFICATION_FALLBACK,
    "m04_scope_resolver_unwired": CLASSIFICATION_BOUNDARY,
    "m04_scope_unavailable": CLASSIFICATION_FALLBACK,
    "promotion_gate_verifier_unwired": CLASSIFICATION_BOUNDARY,
    "m04_gate_unavailable": CLASSIFICATION_FALLBACK,
    "m04e_definition_resolver_unwired": CLASSIFICATION_BOUNDARY,
    "m04e_definition_unavailable": CLASSIFICATION_FALLBACK,
    "m04e_gate_unavailable": CLASSIFICATION_FALLBACK,
    "source_claim_marker_wrong_kind": CLASSIFICATION_BOUNDARY,
    "atomic_ingress_transaction_unwired": CLASSIFICATION_BOUNDARY,
    "atomic_claim_outcome_unknown": CLASSIFICATION_FALLBACK,
    "durable_quarantine_unwired": CLASSIFICATION_BOUNDARY,
    "durable_quarantine_outcome_unknown": CLASSIFICATION_FALLBACK,
    "trigger_claim_marker_requires_lookup_only_resume": CLASSIFICATION_BOUNDARY,
    "durable_scope_definition_or_source_fence_unresolved": CLASSIFICATION_BOUNDARY,
    "durable_trigger_claim_marker_not_found": CLASSIFICATION_BOUNDARY,
    "durable_trigger_claim_digest_mismatch": CLASSIFICATION_BOUNDARY,
    # --- resources / content ---
    "resource_persistence_adapter_unwired": CLASSIFICATION_BOUNDARY,
    "resource_persistence_adapter_unavailable": CLASSIFICATION_FALLBACK,
    "content_assets_archive_columns_missing": CLASSIFICATION_BOUNDARY,
}

DEFECT_CODES: frozenset[str] = frozenset(
    code for code, verdict in BLOCKER_CLASSIFICATION.items() if verdict == CLASSIFICATION_DEFECT
)

# Blocker constants are enforced only inside the reviewed migration zone; the rest of the
# tree is covered by the global ``blocker=`` literal scan and by the staleness check.
BLOCKER_ZONE_PREFIXES: tuple[str, ...] = (
    "application/migration/",
    "infrastructure/persistence/",
)

_ASSIGNED_CODE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"([a-z0-9_]+)\"\s*$", flags=re.MULTILINE
)
_BLOCKER_ARGUMENT = re.compile(r"\bblocker\s*=\s*\"([a-z0-9_]+)\"")
_BLOCKER_KEY = re.compile(r"\"blocker\"\s*:\s*\"([a-z0-9_]+)\"")


def _emitted_blocker_codes() -> dict[str, str]:
    """Return ``code -> first module`` for every emitted blocker literal in the tree."""
    emitted: dict[str, str] = {}
    for relative, text in _source_files().items():
        codes = set(_BLOCKER_ARGUMENT.findall(text)) | set(_BLOCKER_KEY.findall(text))
        if relative.startswith(BLOCKER_ZONE_PREFIXES):
            for name, code in _ASSIGNED_CODE.findall(text):
                upper = name.upper()
                if "BLOCKER" in upper or upper.endswith("UNWIRED") or "NOT_WIRED" in upper:
                    codes.add(code)
                elif code.endswith(("_unwired", "_not_wired")):
                    codes.add(code)
        for code in codes:
            emitted.setdefault(code, relative)
    return emitted


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_every_seam_port_is_declared_at_the_reviewed_location() -> None:
    for port in SEAM_PORTS:
        node = _class_node(port.declared_in, port.name)
        assert node is not None, f"{port.name} is no longer declared in {port.declared_in}"
        bases = {ast.unparse(base).split(".")[-1] for base in node.bases}
        assert "Protocol" in bases, f"{port.name} must stay a Protocol contract, got {sorted(bases)}"


def test_declared_ports_have_an_implementation_or_a_reviewed_gap() -> None:
    for port in SEAM_PORTS:
        if not port.implementations:
            assert port.reviewed_gap, (
                f"{port.name} declares no implementation: add one or record a reviewed gap"
            )
            continue
        for module, symbol in port.implementations:
            assert _class_node(module, symbol) is not None, (
                f"{port.name} implementation {symbol} missing from {module}"
            )


def test_implementations_have_a_caller_or_are_reviewed_orphans() -> None:
    files = _source_files()
    for port in SEAM_PORTS:
        for module, symbol in port.implementations:
            call_sites = _references(files, f"{symbol}(", excluding=(module,))
            if call_sites:
                continue
            assert symbol in REVIEWED_ORPHANS, (
                f"{symbol} ({port.name}) has no caller outside {module}; "
                "wire it or add it to REVIEWED_ORPHANS with a review note"
            )


def test_chain_is_not_wired_into_the_process() -> None:
    files = _source_files()
    process_files = {
        relative: text
        for relative, text in files.items()
        if relative.startswith(("api/", "core/"))
    }
    for service in PROCESS_WIRING_GAPS:
        assert any(f"class {service}" in text for text in files.values()), (
            f"{service} disappeared; update PROCESS_WIRING_GAPS and the review"
        )
        hits = _references(process_files, f"{service}(", excluding=())
        assert not hits, (
            f"{service} is now wired into the process layer ({hits}); "
            "re-review docs/08-migration/integration-seams-review.md §7 and shrink the gap list"
        )
    # Witness of the reviewed state. Before MIG-COMPOSITION-WIRING-01 this test asserted
    # that ``core/wiring.py`` never mentions migration at all — that was true, and it was
    # exactly the defect: the chain could be neither assembled nor diagnosed in a
    # delivered process. The task makes the chain reachable *without* making it run, so
    # the witness now pins the three properties that keep it safe, each of which fails if
    # the old defect comes back in a new form:
    #
    # 1. wiring registers the diagnostic resource (and nothing else of the chain);
    # 2. wiring never assembles the chain itself — that would run at registration time,
    #    before the DB resource starts, and regardless of the flag;
    # 3. the resource instantiates no chain service: it only calls the reviewed
    #    composition root, which builds adapters and starts nothing.
    wiring = _read("core/wiring.py")
    assert "migration_chain_resource_from_settings" in wiring
    assert "assemble_migration_chain" not in wiring, (
        "core/wiring.py must not assemble the chain directly: registration happens before "
        "the engine starts and ignores MIGRATION_CHAIN_ENABLED"
    )
    resource_module = _read("core/infra/migration_chain_resource.py")
    assert "assemble_migration_chain(" in resource_module, (
        "the resource must compose through the reviewed composition root"
    )
    for service in PROCESS_WIRING_GAPS:
        assert f"{service}(" not in resource_module, (
            f"{service} is instantiated in the lifespan resource; the resource may only "
            "assemble through the composition root, never execute a stage"
        )


def test_m04_application_adapter_still_violates_the_resolve_scope_contract() -> None:
    """I1: the only app-level M04 adapter returns a type M06 rejects with ``isinstance``."""
    annotation = _method_return_annotation(
        "application/migration/scope_registry_adapter/adapter.py",
        "ScopeRegistryApplicationAdapter",
        "resolve_active",
    )
    assert "ScopeAdapterResult" in annotation, f"adapter return type changed: {annotation!r}"
    assert "ScopeResolutionEvidence" not in annotation, (
        "I1 appears fixed: make the adapter satisfy ResolveScopePort, then delete this test "
        "and review §5 I1"
    )


def test_m04e_handoff_marker_still_carries_every_field_m05_reads() -> None:
    marker_fields = set(_class_fields(*M04E_MARKER))
    missing = MARKER_FIELDS_REQUIRED_BY_M05 - marker_fields
    assert not missing, f"M05 reads marker fields that M04E no longer declares: {sorted(missing)}"


def test_m05_promotion_record_still_cannot_satisfy_m06_partition_evidence() -> None:
    accepted_fields = set(_class_fields(*M06_ACCEPTED))
    promotion_fields = set(_class_fields(*M05_PROMOTION))

    # Not a single field name is shared: every hop needs an explicit mapper (review §5 I2).
    assert accepted_fields & promotion_fields == set(), (
        "M05 PromotionRecord and M06 AcceptedPartitionEvidence now share field names; "
        "re-review the handoff mapper gap (review §5 I2)"
    )
    assert not (PINNED_HANDOFF_GAP - accepted_fields), (
        "AcceptedPartitionEvidence no longer requires part of PINNED_HANDOFF_GAP; update §5 I2"
    )
    assert not (PINNED_HANDOFF_GAP & promotion_fields), (
        "M05 PromotionRecord now supplies part of the M06 handoff; update §5 I2"
    )


def test_writer_epoch_and_writer_generation_stayed_different() -> None:
    """I3: one concept, two names and two domains — pinned until a mapper normalizes it."""
    m05_fields = _class_fields(*M05_PROMOTION)
    m06_fields = _class_fields(*M06_ACCEPTED)
    assert "writer_generation" in m05_fields
    assert "writer_epoch" in m06_fields
    assert "writer_epoch" not in m05_fields and "writer_generation" not in m06_fields


def test_gate_name_vocabularies_match_upstream_and_differ_downstream() -> None:
    m04e_text = _read("application/migration/m04e_definition_version/backfill.py")
    m05_text = _read("application/migration/m05_instance_runtime/runtime_service.py")
    for gate in EXPECTED_HANDOFF_GATES:
        assert f'"{gate}"' in m04e_text, f"M04E export lost required gate {gate}"
        assert f'"{gate}"' in m05_text, f"M05 no longer requires gate {gate}"
    for name in M05_GATE_NAMES:
        assert f'"{name}"' in m05_text, f"M05 status gate {name} disappeared"

    stage_node = _class_node("application/migration/m06_cutover/cutover.py", "GateStage")
    assert stage_node is not None, "cutover GateStage disappeared"
    stages = {
        statement.value.value
        for statement in stage_node.body
        if isinstance(statement, ast.Assign)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    }
    assert stages == set(EXPECTED_CUTOVER_STAGES), (
        f"cutover stage vocabulary changed: {sorted(stages)}; "
        "M05/M06 gate names must be mapped explicitly (review §5 I6)"
    )
    assert not (M05_GATE_NAMES & stages), "M05 and cutover gate vocabularies merged; update §5 I6"


def test_digest_canonicalizer_is_unique_except_for_the_reviewed_deviation() -> None:
    for relative in DIGEST_MODULES:
        text = _read(relative)
        defines_canonicalizer = any(
            token in text
            for token in ("def digest", "def _digest", "def _canonical_json", "def _lineage_payload_digest")
        )
        if not defines_canonicalizer:
            continue
        has_ascii_guard = "ensure_ascii=True" in text
        if relative in PINNED_DIGEST_DEVIATIONS:
            assert not has_ascii_guard, (
                f"{relative} now guards ensure_ascii; remove it from PINNED_DIGEST_DEVIATIONS "
                "and update review §5 I8"
            )
            continue
        assert has_ascii_guard, (
            f"{relative} defines a digest canonicalizer without ensure_ascii=True; "
            "cross-seam digests will diverge on non-ASCII payloads"
        )


def test_duplicate_contract_names_match_the_review() -> None:
    definitions = _class_definitions(_source_files())
    for name, expected in EXPECTED_DUPLICATE_COUNTS.items():
        actual = len(definitions.get(name, ()))
        assert actual == expected, (
            f"{name} is defined in {actual} modules, review says {expected}: {definitions.get(name)}"
        )


def test_every_emitted_blocker_code_is_classified() -> None:
    emitted = _emitted_blocker_codes()
    unclassified = sorted(set(emitted) - set(BLOCKER_CLASSIFICATION))
    assert not unclassified, (
        "blocker codes without a verdict (documented boundary vs forgotten wiring): "
        f"{[(code, emitted[code]) for code in unclassified]}"
    )


def test_classified_blockers_still_exist() -> None:
    files = _source_files()
    stale = sorted(
        code
        for code in BLOCKER_CLASSIFICATION
        if not any(f'"{code}"' in text for text in files.values())
    )
    assert not stale, f"classification entries reference nonexistent codes: {stale}"


def test_defect_verdicts_are_recorded_in_the_review() -> None:
    """The review and this test must not drift: every defect verdict is written down."""
    review = REVIEW_DOC.read_text(encoding="utf-8")
    missing = sorted(code for code in DEFECT_CODES if code not in review)
    assert not missing, (
        f"defect verdicts {missing} are absent from {REVIEW_DOC.as_posix()}; "
        "record them in review §8.1 or drop the verdict"
    )
