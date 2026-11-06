"""Partition-evidence assembly: the M05 -> M06 seam (review findings I2, I3, I6).

``AcceptedPartitionEvidence`` demands fields the M05 promotion record does not carry
(``accepted_sources``, ``inventory_digest``, per-stage evidence ids, ``writer_epoch > 0``).
:func:`assemble_partition_evidence` fills them only from stored artefacts and refuses with
named blockers when one is absent or inconsistent. These cases pin every refusal, so a
future mapper cannot quietly substitute a placeholder.
"""

from __future__ import annotations

import pytest

from prodavan.application.migration.evidence_ports import (
    M04_OWNER_TABLES,
    MigrationEvidenceBlocked,
    PartitionParts,
    ReviewedM04RlsEvidence,
    assemble_partition_evidence,
    m05_partition_id,
    m06_partition_id,
    policy_digest,
    policy_shape_is_restrictive,
)
from prodavan.application.migration.m06_grants_gateway.adapter import schema_identity_digest
from prodavan.application.migration.m06_grants_gateway.handoff import (
    AcceptedSourceKey,
    inventory_digest,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    HandoffGate,
    HandoffStatus as M04EHandoffStatus,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    HandoffStatus as M05HandoffStatus,
    PromotionGateEvidence,
)

from .acceptance_fixtures import (
    CANONICAL_DIGEST,
    DEFINITION,
    INSTANCE,
    M04_SCOPE_DIGEST,
    M05_RUNTIME_DIGEST,
    TENANT,
    VERSION,
    marker,
    promotion_record,
)

M06_SCHEMA_DIGEST = schema_identity_digest()
M04_OWNER_TABLES_REF = M04_OWNER_TABLES


def reviewed_rls() -> ReviewedM04RlsEvidence:
    return ReviewedM04RlsEvidence(
        review_id="m04-rls-review-1",
        database_role="prodavan_service",
        policy_digests=tuple(
            (table, "sha256:" + format(index, "x") * 64)
            for index, table in enumerate(M04_OWNER_TABLES_REF, start=1)
        ),
    )


def _accepted_sources() -> tuple[AcceptedSourceKey, ...]:
    return (AcceptedSourceKey("legacy_app_grant", "grant-a", "grantset-v1", CANONICAL_DIGEST),)


def parts(**overrides) -> PartitionParts:
    values = {
        "m05_record": promotion_record(),
        "m04e_marker": marker(),
        "m04e_marker_row_id": "sha256:" + "1" * 64,
        "instance_row_id": INSTANCE,
        "scope_registry_id": "scope-registry-1",
        "instance_version_id": VERSION,
        "accepted_sources": _accepted_sources(),
        "m06_row_id": "m06promo-evidence-1",
        "m06_schema_identity_digest": M06_SCHEMA_DIGEST,
        "m04_rls_evidence": reviewed_rls(),
        # Stored M04-02 scope-evidence row (m04_scope_promotion_evidence); its digest is
        # the same value the M05 record cites as its M04_scope gate.
        "m04_scope_evidence_row_id": "m04scope-evidence-1",
        "m04_scope_evidence_digest": M04_SCOPE_DIGEST,
    }
    values.update(overrides)
    return PartitionParts(**values)


def build(**overrides):
    arguments = {
        "tenant_id": TENANT,
        "app_definition_id": DEFINITION,
        "app_version_id": VERSION,
        "plugin_instance_id": INSTANCE,
        "writer_generation": 3,
        "parts": parts(**overrides),
    }
    return assemble_partition_evidence(**arguments)


def test_a_complete_partition_produces_exactly_one_accepted_evidence_object():
    evidence = build()
    assert evidence.tenant_id == TENANT
    assert evidence.app_definition_id == DEFINITION
    assert evidence.app_version_id == VERSION
    assert evidence.plugin_instance_id == INSTANCE
    assert evidence.writer_epoch == 3
    assert evidence.m04_scope_digest == M04_SCOPE_DIGEST
    assert evidence.m04_rls_digest == reviewed_rls().digest
    assert evidence.m04e_definition_version_digest == CANONICAL_DIGEST
    assert evidence.m05_instance_digest == M05_RUNTIME_DIGEST
    assert evidence.m06_schema_identity_digest == M06_SCHEMA_DIGEST
    assert evidence.m04_scope_evidence_id == "scope-registry-1"
    assert evidence.m04_rls_evidence_id == "m04-rls-review-1"
    assert evidence.m04e_definition_version_evidence_id == "sha256:" + "1" * 64
    assert evidence.m05_instance_runtime_evidence_id == INSTANCE
    assert evidence.atomic_evidence_id == promotion_record().record_id
    assert evidence.m06_schema_evidence_id == "m06promo-evidence-1"
    assert evidence.final_delta_checkpoint == promotion_record().final_delta_checkpoint_digest
    assert evidence.inventory_digest == inventory_digest(_accepted_sources())
    assert evidence.accepted_sources == _accepted_sources()
    # The M04-02 scope link is attested from the stored row, not from the M05 gate value.
    assert evidence.m04_scope_evidence_id == "scope-registry-1"
    assert parts().m04_scope_evidence_row_id == "m04scope-evidence-1"
    assert parts().m04_scope_evidence_digest == evidence.m04_scope_digest


def test_the_accepted_inventory_is_canonically_ordered():
    shuffled = (
        AcceptedSourceKey("legacy_app_grant", "grant-b", "grantset-v1", CANONICAL_DIGEST),
        AcceptedSourceKey("legacy_app_grant", "grant-a", "grantset-v1", CANONICAL_DIGEST),
    )
    evidence = build(accepted_sources=shuffled)
    assert [key.source_id for key in evidence.accepted_sources] == ["grant-a", "grant-b"]


@pytest.mark.parametrize(
    "overrides,blocker",
    [
        ({"m05_record": promotion_record(status=M05HandoffStatus.BLOCKED)},
         "m05_promotion_not_ready"),
        ({"m05_record": promotion_record(m06_handoff=M05HandoffStatus.BLOCKED)},
         "m05_m06_handoff_not_ready"),
        ({"m05_record": promotion_record(canonical_digest="not-a-digest")},
         "m05_promotion_canonical_digest_invalid"),
        ({"m05_record": promotion_record(final_delta_checkpoint_digest=None)},
         "final_delta_checkpoint_evidence_missing"),
        ({"m04e_marker": marker(status=M04EHandoffStatus.BLOCKED)},
         "m04e_handoff_marker_not_ready"),
        ({"m04e_marker": marker(partition=("tenant-other", DEFINITION))},
         "m04e_handoff_marker_partition_mismatch"),
        ({"m04e_marker": marker(canonical_digest="sha256:" + "0" * 64)},
         "m04e_gate_digest_differs_from_stored_marker"),
        ({"scope_registry_id": ""}, "m04_scope_evidence_id_missing"),
        ({"instance_row_id": " "}, "m05_instance_runtime_evidence_id_missing"),
        ({"m06_row_id": ""}, "m06_schema_evidence_id_missing"),
        ({"m06_schema_identity_digest": "sha256:" + "0" * 64},
         "m06_schema_identity_digest_mismatch"),
        ({"m06_schema_identity_digest": ""}, "m06_schema_identity_digest_missing"),
        ({"instance_version_id": "version-other"}, "plugin_instance_version_mismatch"),
        ({"accepted_sources": ()}, "m06_accepted_source_inventory_missing"),
        ({"m04_scope_evidence_row_id": ""}, "m04_scope_promotion_evidence_missing"),
        ({"m04_scope_evidence_digest": ""}, "m04_scope_evidence_digest_mismatch"),
        ({"m04_scope_evidence_digest": "sha256:" + "0" * 64},
         "m04_scope_evidence_digest_mismatch"),
        ({"m05_record": promotion_record(gate_evidence=())}, "m04_scope_evidence_digest_missing"),
        (
            {"m05_record": promotion_record(gate_evidence=(
                PromotionGateEvidence("M04_scope", True, M04_SCOPE_DIGEST),
            ))},
            "m05_runtime_evidence_digest_missing",
        ),
    ],
)
def test_each_missing_or_inconsistent_artefact_is_a_named_refusal(overrides, blocker):
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        build(**overrides)
    assert blocker in raised.value.blockers


def test_writer_epoch_zero_is_refused_rather_than_normalised():
    """M05 allows writer_generation=0; M06 requires writer_epoch>0 (review finding I3)."""
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        assemble_partition_evidence(
            tenant_id=TENANT,
            app_definition_id=DEFINITION,
            app_version_id=VERSION,
            plugin_instance_id=INSTANCE,
            writer_generation=0,
            parts=parts(),
        )
    assert raised.value.blockers == ("writer_epoch_not_positive",)


def test_a_gate_digest_that_is_not_sha256_is_not_accepted_as_evidence():
    record = promotion_record(gate_evidence=(
        PromotionGateEvidence("M04_scope", True, "evidence"),
        *promotion_record().gate_evidence[1:],
    ))
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        build(m05_record=record)
    assert "m04_scope_evidence_digest_missing" in raised.value.blockers


def test_partition_keys_are_stable_and_width_bounded():
    assert m05_partition_id(TENANT, DEFINITION) == f"{TENANT}:{DEFINITION}"
    assert m06_partition_id(TENANT, DEFINITION, INSTANCE) == (
        f"{TENANT}:{DEFINITION}:plugin_instance:{INSTANCE}"
    )
    with pytest.raises(MigrationEvidenceBlocked, match="m05_partition_id_exceeds_column_width"):
        m05_partition_id("t" * 200, DEFINITION)


def test_reviewed_rls_manifest_binds_every_owner_table_exactly_once():
    with pytest.raises(ValueError, match="every owner-registry table exactly once"):
        ReviewedM04RlsEvidence(
            review_id="r", database_role="role",
            policy_digests=(("scope_registry", "sha256:" + "1" * 64),),
        )
    with pytest.raises(ValueError, match="sha256"):
        ReviewedM04RlsEvidence(
            review_id="r", database_role="role",
            policy_digests=tuple((table, "sha256:nope") for table in M04_OWNER_TABLES_REF),
        )


def test_policy_digest_and_restrictive_shape_match_the_review_adapter():
    from prodavan.infrastructure.persistence.migration_scope.adapter import (
        _policy_digest as reference_digest,
    )

    rows = [
        {
            "policyname": "scope_registry_tenant_isolation",
            "permissive": "RESTRICTIVE",
            "roles": ["public"],
            "cmd": "ALL",
            "qual": "(tenant_id = current_setting('app.tenant_id', true))",
            "with_check": "(tenant_id = current_setting('app.tenant_id', true))",
        }
    ]
    assert policy_digest(rows) == reference_digest(rows)
    assert policy_shape_is_restrictive(rows[0]) is True
    assert policy_shape_is_restrictive({**rows[0], "permissive": "PERMISSIVE"}) is False
    assert policy_shape_is_restrictive({**rows[0], "with_check": None}) is False


def test_handoff_gate_type_is_the_m04e_contract_not_a_parallel_shape():
    assert HandoffGate.__module__.endswith("m04e_definition_version.backfill")
