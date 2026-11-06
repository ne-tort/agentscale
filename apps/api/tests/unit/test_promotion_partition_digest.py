"""Source acceptance contracts for the exclusive promotion partition digest zone.

Migration verification is intentionally frozen; this file is not executed here.
"""

import hashlib
import json

import pytest

from prodavan.application.promotion_partition_digest import (
    CANONICAL_FIELDS,
    PromotionPartition,
    PromotionPartitionDigestService,
    PromotionPartitionError,
    ScopeKind,
    canonical_promotion_partition_digest,
    decide_promotion_partition,
)


def partition(**changes):
    values = {
        "tenant_id": "tenant-1",
        "scope_kind": ScopeKind.PROJECT,
        "scope_id": "project-1",
        "plugin_id": "plugin-1",
        "supplier_id": "supplier-1",
        "project_id": "project-1",
        "owner_id": "owner-1",
    }
    values.update(changes)
    return PromotionPartition(**values)


def test_digest_uses_explicit_order_compact_ascii_json_and_lowercase_sha256():
    value = partition()
    expected_json = (
        '{"tenant_id":"tenant-1","scope_kind":"project",'
        '"scope_id":"project-1","plugin_id":"plugin-1",'
        '"supplier_id":"supplier-1","project_id":"project-1",'
        '"owner_id":"owner-1"}'
    )
    assert PromotionPartitionDigestService.canonical_json(value) == expected_json
    expected = "sha256:" + hashlib.sha256(expected_json.encode("utf-8")).hexdigest()
    assert canonical_promotion_partition_digest(value) == expected
    assert expected == expected.lower()


def test_mapping_order_does_not_change_semantic_digest():
    values = partition().canonical_payload()
    reversed_values = dict(reversed(tuple(values.items())))
    assert decide_promotion_partition(values).digest == decide_promotion_partition(reversed_values).digest
    assert set(CANONICAL_FIELDS) == set(values)


def test_supplied_digest_is_verified_and_conflicts_fail_closed():
    value = partition()
    digest = canonical_promotion_partition_digest(value)
    assert decide_promotion_partition(value, supplied_digest=digest).digest == digest
    with pytest.raises(PromotionPartitionError, match="conflicts"):
        decide_promotion_partition(value, supplied_digest="sha256:" + "a" * 64)
    with pytest.raises(PromotionPartitionError, match="lowercase"):
        decide_promotion_partition(value, supplied_digest="sha256:" + "A" * 64)


def test_mapping_digest_is_verified_and_duplicate_digest_sources_must_agree():
    value = partition().canonical_payload()
    digest = canonical_promotion_partition_digest(partition())
    value["digest"] = digest
    assert decide_promotion_partition(value).accepted
    with pytest.raises(PromotionPartitionError, match="conflicting"):
        decide_promotion_partition(value, supplied_digest="sha256:" + "b" * 64)
    value["digest"] = None
    with pytest.raises(PromotionPartitionError, match="lowercase"):
        decide_promotion_partition(value)


def test_unsupported_missing_and_forbidden_fields_are_rejected():
    values = partition().canonical_payload()
    with pytest.raises(PromotionPartitionError, match="unsupported"):
        decide_promotion_partition({**values, "unexpected": "x"})
    with pytest.raises(PromotionPartitionError, match="missing"):
        decide_promotion_partition({key: values[key] for key in values if key != "owner_id"})
    with pytest.raises(PromotionPartitionError, match="forbidden"):
        decide_promotion_partition({**values, "lease_id": "lease-1"})
    with pytest.raises(PromotionPartitionError, match="forbidden"):
        decide_promotion_partition({**values, "access_token": "secret"})


def test_typed_fields_scope_constraints_and_malformed_values_fail_closed():
    assert partition(scope_kind="project").scope_kind is ScopeKind.PROJECT
    with pytest.raises(PromotionPartitionError, match="unsupported scope_kind"):
        partition(scope_kind="global")
    with pytest.raises(PromotionPartitionError, match="tenant scope"):
        partition(scope_kind=ScopeKind.TENANT, scope_id="other-tenant")
    with pytest.raises(PromotionPartitionError, match="project scope"):
        partition(scope_id="other-project")
    with pytest.raises(PromotionPartitionError, match="plugin_instance scope"):
        partition(scope_kind=ScopeKind.PLUGIN_INSTANCE, scope_id="other-plugin")
    with pytest.raises(PromotionPartitionError, match="non-empty string"):
        partition(owner_id=" ")
    with pytest.raises(PromotionPartitionError, match="non-empty string"):
        partition(plugin_id=123)


def test_canonical_json_contains_only_identity_fields_and_no_secret_or_lease_data():
    payload = json.loads(PromotionPartitionDigestService.canonical_json(partition()))
    assert tuple(payload) == CANONICAL_FIELDS
    assert not any("lease" in key or "secret" in key or "token" in key for key in payload)
