"""MIG-REVIEW-SEAMS-01 — static seam contract M04 -> M04E -> M05 -> M06 -> cutover.

Authored during the migration **verification freeze**: nothing in this module was executed.
The tests never import the application under test (no settings, no database, no fixtures):
they parse source text and ``ast`` so a seam regression fails on a clean checkout.

Companion review: ``docs/08-migration/integration-seams-review.md``.

Contract covered here:

* every critical seam port is declared exactly where the review says it is;
* every declared port has an implementation, or an explicitly reviewed gap;
* every implementation has a caller, or is an explicitly reviewed orphan;
* the M04E handoff marker still carries every field M05 reads from it;
* M05's ``PromotionRecord`` still cannot satisfy M06's ``AcceptedPartitionEvidence``
  (the incompatibility is pinned; fixing it must remove the pinned expectation);
* the M04E/M05 gate-name vocabularies still agree, and the cutover stage vocabulary
  still differs (documented mismatch, not a silent drift);
* the digest canonicalizer is unique except for the reviewed cutover deviation;
* duplicate contract names stay exactly as reviewed;
* every ``*_unwired`` / ``*_not_wired`` / ``*_missing`` blocker code is classified in
  :data:`BLOCKER_CLASSIFICATION` — a new blocker without a verdict fails the suite.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

# apps/api/tests/migration/integration_seams/test_seam_contract.py -> apps/api
API_ROOT = Path(__file__).resolve().parents[3]
SRC = API_ROOT / "src" / "prodavan"


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
    fields: list[str] = []
    for statement in node.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            fields.append(statement.target.id)
    return tuple(fields)


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
    implementations: tuple[tuple[str, str], ...]  # (module, symbol) or ("", symbol) when absent
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
        implementations=(),
        reviewed_gap="G1: no durable M04E->M05 handoff store exists in src or infrastructure",
    ),
    SeamPort(
        name="M05PromotionRecordPort",
        declared_in="application/migration/m05_instance_runtime/runtime_service.py",
        implementations=(),
        reviewed_gap="G2: no append-only M05 promotion/handoff store exists",
    ),
    SeamPort(
        name="PartitionHandoffReadPort",
        declared_in="application/migration/m06_grants_gateway/handoff.py",
        implementations=(),
        reviewed_gap="G3: no reader over accepted partition evidence exists",
    ),
    SeamPort(
        name="EvidenceStore",
        declared_in="application/migration/m04e_definition_version/backfill.py",
        implementations=(),
        reviewed_gap="G4: bounded M04E page-commit/checkpoint CAS store is absent",
    ),
    SeamPort(
        name="CutoverRepository",
        declared_in="application/migration/m06_cutover/cutover.py",
        implementations=(),
        reviewed_gap="G5: cutover state/evidence repository is absent",
    ),
    SeamPort(
        name="GateVerifier",
        declared_in="application/migration/m06_cutover/cutover.py",
        implementations=(),
        reviewed_gap="G5: full-lineage gate verifier is absent; cutover refuses by design",
    ),
    SeamPort(
        name="LegacyOwnerVerifier",
        declared_in="application/migration/m06_cutover/cutover.py",
        implementations=(),
        reviewed_gap="G5",
    ),
    SeamPort(
        name="RollbackVerifier",
        declared_in="application/migration/m06_cutover/cutover.py",
        implementations=(),
        reviewed_gap="G5",
    ),
    SeamPort(
        name="CanonicalRepairVerifier",
        declared_in="application/migration/m06_cutover/cutover.py",
        implementations=(),
        reviewed_gap="G5",
    ),
    SeamPort(
        name="EvidenceReadPort",
        declared_in="application/migration/m06_grants_gateway/adapter.py",
        implementations=(),
        reviewed_gap="G6: shadow evidence reader is absent (composition field only)",
    ),
    SeamPort(
        name="TrustedEvidenceTransactionPort",
        declared_in="application/migration/m06_grants_gateway/adapter.py",
        implementations=(),
        reviewed_gap="G6: trusted evidence transaction is absent (composition field only)",
    ),
    SeamPort(
        name="ResolvePluginInstancePort",
        declared_in="application/migration/m05_instance_runtime/runtime_service.py",
        implementations=(
            (
                "application/migration/m05_instance_runtime/instance_persistence.py",
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


# Implementations whose only instantiator is their own module or a factory without an
# external caller (review §4).  A symbol listed here must NOT gain a caller silently:
# the assertion below fails as soon as wiring appears, so this list stays honest.
REVIEWED_ORPHANS: dict[str, str] = {
    "SqlAlchemyRuntimeInstanceLifecycle": "O-factory: only built by build_sql_resolve_plugin_instance_port",
    "SqlAlchemyM05IdentityEvidence": "O-factory: built inside the same module only",
    "SqlAlchemyInstanceImportStore": "O-factory: built inside the same module only",
    "SqlAlchemyInstanceQuarantineStore": "O-factory: built inside the same module only",
    "SqlAlchemyRedactedAuditSink": "O-factory: built inside the same module only",
    "FailClosedResolvePluginInstance": "used by m06 identity adapter and the m05 factory",
    "ServerResolvedHandoffResolver": "used by build_shadow_gateway (itself uncalled)",
}


# Review §7: services of the chain that no production composition root instantiates.
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
M05_EXPORT = ("application/migration/m05_instance_runtime/runtime_service.py", "M05EvidenceExport")
M05_PROMOTION = ("application/migration/m05_instance_runtime/runtime_service.py", "PromotionRecord")
M06_ACCEPTED = ("application/migration/m06_grants_gateway/handoff.py", "AcceptedPartitionEvidence")

# Fields M05 reads off the marker (runtime_service.py, marker_ready block).
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

# Fields M06 needs from upstream evidence that the M05 promotion record does not carry.
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

# m05 names its status gates differently from both m04e and cutover stages (review I6).
M05_GATE_NAMES: frozenset[str] = frozenset(
    {"M04_scope", "M04E_definition_version", "M05_runtime"}
)


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
# payloads produce different bytes than the other three canonicalizers.
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
    "HandoffStatus": 2,
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
    # --- agent runs / control plane ---
    "agent_control_plane_atomic_transaction_adapter_unwired": CLASSIFICATION_BOUNDARY,
    "agent_control_plane_aggregate_cursor_missing": CLASSIFICATION_BOUNDARY,
    "agent_run_provider_attempt_journal_unwired": CLASSIFICATION_BOUNDARY,
    "agent_run_provider_attempt_table_missing": CLASSIFICATION_BOUNDARY,
    "agent_run_unknown_case_journal_unwired": CLASSIFICATION_BOUNDARY,
    "agent_run_control_plane_actor_unresolved": CLASSIFICATION_BOUNDARY,
    "agent_run_retry_due_start_transition_unwired": CLASSIFICATION_BOUNDARY,
    "agent_run_lease_storage_missing": CLASSIFICATION_DEFECT,
    "agent_run_requeue_transition_unwired": CLASSIFICATION_DEFECT,
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
    # --- automation ingress (service never constructed; its repository is an orphan) ---
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
    "atomic_ingress_transaction_unwired": CLASSIFICATION_DEFECT,
    "atomic_claim_outcome_unknown": CLASSIFICATION_FALLBACK,
    "durable_quarantine_unwired": CLASSIFICATION_DEFECT,
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

_ASSIGNED_CODE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"([a-z0-9_]+)\"\s*$", flags=re.MULTILINE
)
_BLOCKER_ARGUMENT = re.compile(r"\bblocker\s*=\s*\"([a-z0-9_]+)\"")
_BLOCKER_KEY = re.compile(r"\"blocker\"\s*:\s*\"([a-z0-9_]+)\"")


def _discovered_blocker_codes() -> frozenset[str]:
    codes: set[str] = set()
    for text in _source_files().values():
        for name, code in _ASSIGNED_CODE.findall(text):
            upper = name.upper()
            if "BLOCKER" in upper or upper.endswith("UNWIRED") or "NOT_WIRED" in upper:
                codes.add(code)
            elif code.endswith(("_unwired", "_not_wired")):
                codes.add(code)
        codes.update(_BLOCKER_ARGUMENT.findall(text))
        codes.update(_BLOCKER_KEY.findall(text))
    return frozenset(codes)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_every_seam_port_is_declared_at_the_reviewed_location() -> None:
    for port in SEAM_PORTS:
        node = _class_node(port.declared_in, port.name)
        assert node is not None, f"{port.name} is no longer declared in {port.declared_in}"
        bases = {ast.unparse(base) for base in node.bases}
        assert "Protocol" in bases or "Protocol" in {b.split(".")[-1] for b in bases}, (
            f"{port.name} must stay a Protocol contract, got bases={sorted(bases)}"
        )


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


def test_process_chain_has_no_undocumented_composition_root() -> None:
    files = _source_files()
    wiring = _read("core/wiring.py")
    for service in PROCESS_WIRING_GAPS:
        defining = tuple(relative for relative, text in files.items() if f"class {service}" in text)
        assert defining, f"{service} disappeared; update PROCESS_WIRING_GAPS"
        call_sites = _references(files, f"{service}(", excluding=defining)
        assert not call_sites, (
            f"{service} now has a production caller ({call_sites}); "
            "remove it from PROCESS_WIRING_GAPS and re-review the seam map"
        )
    # Witness of the reviewed state: the chain is a library, not a process.
    assert "migration" not in wiring.lower(), (
        "core/wiring.py now mentions migration; re-review docs/08-migration/"
        "integration-seams-review.md §7 before deleting this witness"
    )


def test_m04e_handoff_marker_still_carries_every_field_m05_reads() -> None:
    marker_fields = set(_class_fields(*M04E_MARKER))
    missing = MARKER_FIELDS_REQUIRED_BY_M05 - marker_fields
    assert not missing, f"M05 reads marker fields that M04E no longer declares: {sorted(missing)}"


def test_m05_promotion_record_still_cannot_satisfy_m06_partition_evidence() -> None:
    accepted_fields = set(_class_fields(*M06_ACCEPTED))
    promotion_fields = set(_class_fields(*M05_PROMOTION))

    # No name is shared between the two contracts: every hop needs an explicit mapper.
    assert accepted_fields & promotion_fields == set(), (
        "M05 PromotionRecord and M06 AcceptedPartitionEvidence now share field names; "
        "re-review the handoff mapper gap (review §5, I2)"
    )
    missing = PINNED_HANDOFF_GAP - accepted_fields
    assert not missing, (
        f"AcceptedPartitionEvidence no longer requires {sorted(missing)}; "
        "update PINNED_HANDOFF_GAP and the review"
    )
    unavailable = PINNED_HANDOFF_GAP - promotion_fields
    assert unavailable, "the M05/M06 handoff gap appears closed; re-review before relaxing"
    assert not (PINNED_HANDOFF_GAP & promotion_fields), (
        "M05 PromotionRecord now supplies part of the M06 handoff; update the review"
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
    assert stage_node is not None
    stages = {
        statement.value.value
        for statement in stage_node.body
        if isinstance(statement, ast.Assign)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    }
    assert stages == set(EXPECTED_CUTOVER_STAGES), (
        f"cutover stage vocabulary changed: {sorted(stages)}; "
        "M05/M06 gate names must be mapped explicitly (review §5, I6)"
    )
    assert not (M05_GATE_NAMES & stages), "M05 and cutover gate vocabularies merged; update the review"


def test_digest_canonicalizer_is_unique_except_for_the_reviewed_deviation() -> None:
    for relative in DIGEST_MODULES:
        text = _read(relative)
        defines_canonicalizer = any(
            token in text
            for token in ("def digest", "def _digest", "def _canonical_json", "def _lineage_payload_digest")
        )
        if not defines_canonicalizer:
            continue
        has_ascii_guard = "ensure_ascii=True" in text or "ensure_ascii=True," in text
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
            f"{name} is defined in {actual} modules, review says {expected}: "
            f"{definitions.get(name)}"
        )


def test_every_blocker_code_is_classified() -> None:
    discovered = _discovered_blocker_codes()
    unclassified = discovered - set(BLOCKER_CLASSIFICATION)
    assert not unclassified, (
        "blocker codes without a verdict (documented boundary vs forgotten wiring): "
        f"{sorted(unclassified)}"
    )
    missing = DEFECT_CODES - discovered
    assert not missing, (
        f"classified defects {sorted(missing)} no longer exist in src; "
        "the defect list is stale and must be re-reviewed"
    )
