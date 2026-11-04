"""Fail-closed acceptance: foreign partitions, absent evidence and named refusals.

Every case here is expected to refuse *before* the first SQL statement. The
``ExplodingSessionFactory`` makes that observable: if a port opened a transaction the test
would fail with ``AssertionError`` instead of the expected typed blocker.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from prodavan.application.migration.evidence_ports import (
    DECLARED_READ_BLOCKERS,
    PartitionHandoffRead,
    SqlM04EBackfillEvidenceStore,
    SqlM05HandoffEvidenceStore,
    SqlM05PromotionEvidenceStore,
    SqlPartitionHandoffReadStore,
    TrustedEvidenceScope,
    decode_marker_row,
    decode_promotion_row,
)
from prodavan.application.migration.evidence_ports import MigrationEvidenceBlocked

from .acceptance_fixtures import (
    DEFINITION,
    INSTANCE,
    TENANT,
    VERSION,
    ExplodingSessionFactory,
    backfill_context,
    marker,
    promotion_record,
)

SCOPE = TrustedEvidenceScope(tenant_id=TENANT, writer_generation=3)


def _handoff_store(factory=None):
    return SqlM05HandoffEvidenceStore(
        session_factory=factory or ExplodingSessionFactory(), scope=SCOPE
    )


def _promotion_store(factory=None):
    return SqlM05PromotionEvidenceStore(
        session_factory=factory or ExplodingSessionFactory(), scope=SCOPE
    )


def _read_store(factory=None, reviewed=None):
    return SqlPartitionHandoffReadStore(
        session_factory=factory or ExplodingSessionFactory(),
        scope=SCOPE,
        reviewed_m04_rls=reviewed,
    )


def test_absent_row_decodes_to_none():
    assert decode_marker_row(None) is None
    assert decode_promotion_row(None) is None


def test_scope_refuses_a_blank_tenant():
    with pytest.raises(ValueError, match="resolved tenant"):
        TrustedEvidenceScope(tenant_id="  ")
    with pytest.raises(ValueError, match="non-negative"):
        TrustedEvidenceScope(tenant_id=TENANT, writer_generation=-1)
    scope = TrustedEvidenceScope(tenant_id=TENANT)
    assert scope.admits_partition((TENANT, DEFINITION)) is True
    assert scope.admits_partition(("tenant-other", DEFINITION)) is False
    assert scope.admits_partition((TENANT,)) is False


async def test_foreign_tenant_is_refused_before_sql():
    factory = ExplodingSessionFactory()
    with pytest.raises(MigrationEvidenceBlocked) as marker_refusal:
        await _handoff_store(factory).append_marker(
            marker(partition=("tenant-other", DEFINITION))
        )
    assert marker_refusal.value.blockers == (
        "handoff_marker_partition_outside_trusted_tenant",
    )

    with pytest.raises(MigrationEvidenceBlocked) as promotion_refusal:
        await _promotion_store(factory).append_immutable(
            promotion_record(partition=("tenant-other", DEFINITION))
        )
    assert promotion_refusal.value.blockers == (
        "m05_promotion_partition_outside_trusted_tenant",
    )

    assert await _read_store(factory).load_partition(
        tenant_id="tenant-other",
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        plugin_instance_id=INSTANCE,
        writer_generation=3,
    ) is None
    assert factory.calls == 0


async def test_foreign_tenant_read_is_a_named_refusal_not_an_exception():
    factory = ExplodingSessionFactory()
    result = await _read_store(factory).read_partition(
        tenant_id="tenant-other",
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        plugin_instance_id=INSTANCE,
        writer_generation=3,
    )
    assert result.accepted is False
    assert result.evidence is None
    assert result.blockers == ("partition_tenant_outside_trusted_scope",)
    assert factory.calls == 0


async def test_partition_read_uses_every_request_key():
    factory = ExplodingSessionFactory()
    store = _read_store(factory)

    missing = await store.read_partition(
        tenant_id=TENANT,
        app_definition_id=" ",
        app_version_id=VERSION,
        plugin_instance_id=INSTANCE,
        writer_generation=3,
    )
    assert missing.blockers == ("partition_app_definition_id_missing",)

    zero_epoch = await store.read_partition(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        plugin_instance_id=INSTANCE,
        writer_generation=0,
    )
    assert zero_epoch.blockers == ("writer_epoch_not_positive",)

    no_instance = await store.read_partition(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        plugin_instance_id="",
        writer_generation=3,
    )
    assert no_instance.blockers == ("partition_plugin_instance_id_missing",)
    assert factory.calls == 0

    # The five keys are all part of the port signature and all used by the read.
    signature = inspect.signature(store.read_partition)
    assert list(signature.parameters) == [
        "tenant_id", "app_definition_id", "app_version_id", "plugin_instance_id",
        "writer_generation",
    ]
    source = inspect.getsource(type(store))
    for column in (
        "PluginInstanceRegistryRow.id ==",
        "PluginInstanceRegistryRow.tenant_id ==",
        "PluginInstanceRegistryRow.definition_id ==",
        "PluginInstanceRegistryRow.version_id ==",
        "writer_epoch",
    ):
        assert column in source, column


def test_read_refuses_without_a_reviewed_m04_rls_manifest():
    """No approved M04 policy manifest exists in the repository, so the read refuses."""
    assert _read_store()._reviewed is None  # noqa: SLF001 - fail-closed default is the point
    source = inspect.getsource(SqlPartitionHandoffReadStore)
    assert "m04_rls_review_manifest_missing" in source
    assert "m04_rls_database_role_mismatch" in source
    assert "m04_rls_review_mismatch" in source


def test_declared_read_blockers_name_the_remaining_m04_gap():
    assert any(
        "m04_scope_promotion_evidence_missing" in blocker for blocker in DECLARED_READ_BLOCKERS
    )
    assert all(blocker.strip() for blocker in DECLARED_READ_BLOCKERS)


async def test_backfill_store_refuses_an_untrusted_context():
    factory = ExplodingSessionFactory()
    store = SqlM04EBackfillEvidenceStore(session_factory=factory, scope=SCOPE)

    with pytest.raises(MigrationEvidenceBlocked) as foreign:
        await store.find_existing(
            context=backfill_context(app_definition_id="definition-other"),
            source_kind="legacy_app_version",
            source_id=VERSION,
            source_schema_version="v1",
        )
    assert foreign.value.blockers == ("backfill_context_outside_trusted_tenant",)

    with pytest.raises(MigrationEvidenceBlocked) as generation:
        await store.find_existing(
            context=backfill_context(writer_generation=9),
            source_kind="legacy_app_version",
            source_id=VERSION,
            source_schema_version="v1",
        )
    assert generation.value.blockers == ("backfill_writer_generation_not_trusted",)
    assert factory.calls == 0


async def test_backfill_store_refuses_a_checkpoint_outside_its_lineage():
    factory = ExplodingSessionFactory()
    store = SqlM04EBackfillEvidenceStore(session_factory=factory, scope=SCOPE)
    from .acceptance_fixtures import checkpoint

    with pytest.raises(MigrationEvidenceBlocked) as mismatch:
        await store.record_blocked(
            context=backfill_context(),
            checkpoint=checkpoint(writer_generation=99),
            reason_code="upstream_owner_scope_gate_unproven",
        )
    assert mismatch.value.blockers == ("checkpoint_outside_source_snapshot_lineage",)
    assert factory.calls == 0


def test_partition_read_rejects_a_half_filled_result():
    """A read is either accepted evidence or named refusals — never an empty object."""
    with pytest.raises(ValueError, match="either accepted evidence or named refusals"):
        PartitionHandoffRead(None, ())


def test_module_source_has_one_read_and_one_write_boundary_helper():
    source_path = inspect.getsourcefile(SqlPartitionHandoffReadStore)
    with open(source_path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), source_path)
    helpers = sorted(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"read", "write"}
    )
    assert helpers == ["read", "write"]
