"""Real-cluster SecretProjection acceptance (lifecycle + capability delivery).

Covers the exact four managed keys, the separate delivery Secret with hashed
keys and SHA-256 value versions, resourceVersion CAS (recorder proof and real
409 on a stale rv) for BOTH Secret classes, same-ref rotation, lifecycle and
delivery drift detection/repair, stale key removal, scoped revoke/suspend
cleanup with sibling preservation, and log hygiene for every secret value and
for the real plugin-deployment-admin tokens of both providers.

No plaintext, base64 value, or token ever appears in assertion output: value
checks compare SHA-256 digests and leak guards report only stable codes.
"""

from __future__ import annotations

import base64
import json
import logging

import pytest

from prodavan.infrastructure.plugins.runtime.k8s_scale import MERGE_PATCH, K8sApiConflict
from prodavan.infrastructure.plugins.runtime.secret_projection import (
    DELIVERY_VERSIONS_ANNOTATION,
    DSN_REF_ANNOTATION,
    INSTANCE_LABEL,
    SECRET_KEYS,
    SecretDriftError,
    SecretOwnershipError,
)

from .cluster import AcceptanceCluster
from .lifecycle import (
    AcceptanceIds,
    LifecyclePair,
    artifact_record,
    assert_no_sensitive_material,
    assert_objects_equal,
    assert_value_digest_matches,
    decoded_secret_value,
    delivery_key,
    instance_label,
    lifecycle_pair,
    provision,
    resource_version,
    value_digest,
)

CLUSTER_TAG = "secret"

pytestmark = pytest.mark.plugin_acceptance


def _secret_name(pair: LifecyclePair, ids: AcceptanceIds) -> str:
    return pair.a.admin.resource_name(ids.plugin_id, ids.plugin_instance_id)


def _delivery_name(pair: LifecyclePair, ids: AcceptanceIds) -> str:
    return pair.a.admin.canonical_delivery_secret_name(ids.plugin_id, ids.plugin_instance_id)


async def _ensure(pair: LifecyclePair, ids: AcceptanceIds, side: str = "a") -> None:
    projection = pair.a.projection if side == "a" else pair.b.projection
    await projection.ensure_instance_secret(
        **ids.scope(), dsn_ref=ids.dsn_ref, webhook_secret_ref=ids.webhook_ref
    )


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _versions(secret) -> dict[str, str]:
    return json.loads(secret["metadata"]["annotations"][DELIVERY_VERSIONS_ANNOTATION])


async def test_exact_keys_hashed_delivery_and_no_plaintext_metadata(
    cluster: AcceptanceCluster, make_ids
) -> None:
    ids = make_ids("keys")
    async with lifecycle_pair(cluster, ids) as pair:
        await _ensure(pair, ids)
        secret = await cluster.read("secrets", _secret_name(pair, ids))
        assert secret is not None
        assert secret["type"] == "Opaque"
        assert set(secret["data"]) == set(SECRET_KEYS) == {
            "TENANT_DB_DSN",
            "GATEWAY_URL",
            "PLUGIN_ID",
            "WEBHOOK_SECRET",
        }
        if "stringData" in secret:
            raise AssertionError("lifecycle Secret must not use stringData")
        assert secret["metadata"]["annotations"][DSN_REF_ANNOTATION] == value_digest(ids.dsn_ref).replace(
            "sha256:", ""
        )
        first_rv = resource_version(secret)
        await _ensure(pair, ids, side="b")
        assert resource_version(await cluster.read("secrets", _secret_name(pair, ids))) == first_rv, (
            "an unchanged projection must not write (CAS no-op)"
        )
        assert_value_digest_matches(
            decoded_secret_value(secret, "TENANT_DB_DSN"), ids.dsn_value_v1, label="initial DSN"
        )
        assert_value_digest_matches(
            decoded_secret_value(secret, "WEBHOOK_SECRET"), ids.webhook_value, label="initial webhook"
        )

        references = await pair.a.projection.reconcile_secret_values(
            **ids.scope(), values=ids.delivery_values()
        )
        assert len(references) == 2
        for reference in references:
            assert reference.delivered is True
            assert reference.namespace == cluster.namespace
            assert reference.version == reference.value_digest
        delivery = await cluster.read("secrets", _delivery_name(pair, ids))
        assert delivery is not None
        assert delivery["metadata"]["name"] != secret["metadata"]["name"]
        keys = {delivery_key(ids.delivery_ref_a), delivery_key(ids.delivery_ref_b)}
        assert set(delivery["data"]) == keys
        versions = _versions(delivery)
        assert set(versions) == keys
        assert versions[delivery_key(ids.delivery_ref_a)] == value_digest(ids.delivery_value_a1)
        assert versions[delivery_key(ids.delivery_ref_b)] == value_digest(ids.delivery_value_b1)
        assert all(len(key) == len("SECRET_") + 64 for key in keys)

        # Neither source refs nor any plaintext value may appear in metadata.
        metadata_text = json.dumps(delivery["metadata"]) + json.dumps(secret["metadata"])
        for index, forbidden in enumerate(
            (
                ids.delivery_ref_a,
                ids.delivery_ref_b,
                ids.delivery_value_a1,
                ids.delivery_value_b1,
                ids.dsn_value_v1,
                ids.webhook_value,
            )
        ):
            assert_no_sensitive_material(metadata_text, forbidden, code=f"metadata-rule-{index}")


async def test_same_ref_rotation_uses_cas_for_lifecycle_and_delivery(
    cluster: AcceptanceCluster, make_ids
) -> None:
    ids = make_ids("rotate")
    async with lifecycle_pair(cluster, ids) as pair:
        await _ensure(pair, ids)
        name = _secret_name(pair, ids)
        before = await cluster.read("secrets", name)
        before_rv = resource_version(before)
        source_binding = before["metadata"]["annotations"][DSN_REF_ANNOTATION]

        # Rotate the DSN value under an UNCHANGED dsn_ref, via the second process.
        pair.tenant_secrets.seed(key=ids.dsn_key, secret_ref=ids.dsn_ref, value=ids.dsn_value_v2)
        await _ensure(pair, ids, side="b")
        rotated = await cluster.read("secrets", name)
        rotated_rv = resource_version(rotated)
        assert rotated_rv != before_rv
        assert rotated["metadata"]["annotations"][DSN_REF_ANNOTATION] == source_binding
        assert_value_digest_matches(
            decoded_secret_value(rotated, "TENANT_DB_DSN"), ids.dsn_value_v2, label="rotated DSN"
        )

        patches = [
            record
            for record in pair.b.recorder.records
            if record["method"] == "PATCH" and record["path"].endswith(f"/secrets/{name}")
        ]
        assert patches, "rotation must PATCH the existing Secret"
        assert patches[-1]["resource_version"] == before_rv, "rotation must use resourceVersion CAS"
        assert patches[-1]["content_type"] == MERGE_PATCH

        # The API server really enforces CAS: a stale resourceVersion is a 409.
        async with cluster.bootstrap() as api:
            with pytest.raises(K8sApiConflict):
                await api.request(
                    "PATCH",
                    cluster.path("secrets", name),
                    body={
                        "metadata": {"resourceVersion": before_rv},
                        "data": {"PLUGIN_ID": _b64(ids.plugin_id)},
                    },
                    content_type=MERGE_PATCH,
                )
        assert_value_digest_matches(
            decoded_secret_value(await cluster.read("secrets", name), "TENANT_DB_DSN"),
            ids.dsn_value_v2,
            label="DSN after rejected stale patch",
        )

        # ---- Delivery rotation: hashed key stays, CAS is enforced and proven.
        await pair.a.projection.reconcile_secret_values(**ids.scope(), values=ids.delivery_values())
        delivery_name = _delivery_name(pair, ids)
        delivery_before = await cluster.read("secrets", delivery_name)
        delivery_before_rv = resource_version(delivery_before)
        await pair.a.projection.deliver_secret(
            **ids.scope(), secret_ref=ids.delivery_ref_a, value=ids.delivery_value_a2
        )
        delivery_after = await cluster.read("secrets", delivery_name)
        assert resource_version(delivery_after) != delivery_before_rv

        # The rotating delivery MERGE_PATCH carried the pre-rotation rv (CAS),
        # not merely a changed annotation/resourceVersion afterwards.
        delivery_patches = [
            record
            for record in pair.a.recorder.records
            if record["method"] == "PATCH" and record["path"].endswith(f"/secrets/{delivery_name}")
        ]
        assert delivery_patches, "delivery rotation must PATCH the existing Secret"
        assert delivery_patches[-1]["content_type"] == MERGE_PATCH
        assert delivery_patches[-1]["resource_version"] == delivery_before_rv

        versions = _versions(delivery_after)
        assert versions[delivery_key(ids.delivery_ref_a)] == value_digest(ids.delivery_value_a2)
        assert versions[delivery_key(ids.delivery_ref_b)] == value_digest(ids.delivery_value_b1)
        assert_value_digest_matches(
            decoded_secret_value(delivery_after, delivery_key(ids.delivery_ref_a)),
            ids.delivery_value_a2,
            label="rotated delivery A value",
        )
        assert_value_digest_matches(
            decoded_secret_value(delivery_after, delivery_key(ids.delivery_ref_b)),
            ids.delivery_value_b1,
            label="untouched delivery B value",
        )

        # A stale-rv PATCH against the delivery Secret is a real 409 and does
        # not change the rotated value either.
        async with cluster.bootstrap() as api:
            with pytest.raises(K8sApiConflict):
                await api.request(
                    "PATCH",
                    cluster.path("secrets", delivery_name),
                    body={
                        "metadata": {"resourceVersion": delivery_before_rv},
                        "data": {delivery_key(ids.delivery_ref_b): _b64("stale-attempt")},
                    },
                    content_type=MERGE_PATCH,
                )
        after_stale = await cluster.read("secrets", delivery_name)
        assert_value_digest_matches(
            decoded_secret_value(after_stale, delivery_key(ids.delivery_ref_b)),
            ids.delivery_value_b1,
            label="delivery B after rejected stale patch",
        )
        assert_value_digest_matches(
            decoded_secret_value(after_stale, delivery_key(ids.delivery_ref_a)),
            ids.delivery_value_a2,
            label="delivery A after rejected stale patch",
        )

        # Re-delivering the rotated value is a no-op.
        unchanged_rv = resource_version(after_stale)
        await pair.b.projection.deliver_secret(
            **ids.scope(), secret_ref=ids.delivery_ref_a, value=ids.delivery_value_a2
        )
        assert resource_version(await cluster.read("secrets", delivery_name)) == unchanged_rv


async def test_drift_is_detected_and_managed_values_reconciled(
    cluster: AcceptanceCluster, make_ids
) -> None:
    ids = make_ids("drift")
    async with lifecycle_pair(cluster, ids) as pair:
        await _ensure(pair, ids)
        name = _secret_name(pair, ids)
        async with cluster.bootstrap() as api:
            # Unexpected key: structural drift is refused, never adopted.
            await api.request(
                "PATCH",
                cluster.path("secrets", name),
                body={"data": {"UNEXPECTED": _b64("drift")}},
                content_type=MERGE_PATCH,
            )
            with pytest.raises(SecretDriftError):
                await _ensure(pair, ids)

            # Tampered managed value (with the key set restored) is reconciled.
            await api.request(
                "PATCH",
                cluster.path("secrets", name),
                body={"data": {"UNEXPECTED": None, "TENANT_DB_DSN": _b64("tampered")}},
                content_type=MERGE_PATCH,
            )
        await _ensure(pair, ids)
        repaired = await cluster.read("secrets", name)
        assert set(repaired["data"]) == set(SECRET_KEYS)
        assert_value_digest_matches(
            decoded_secret_value(repaired, "TENANT_DB_DSN"), ids.dsn_value_v1, label="repaired DSN"
        )

        # Ownership drift: a foreign/unlabelled Secret is never adopted.
        async with cluster.bootstrap() as api:
            await api.request(
                "PATCH",
                cluster.path("secrets", name),
                body={"metadata": {"labels": {INSTANCE_LABEL: None}}},
                content_type=MERGE_PATCH,
            )
            try:
                with pytest.raises(SecretOwnershipError):
                    await _ensure(pair, ids)
            finally:
                await api.request(
                    "PATCH",
                    cluster.path("secrets", name),
                    body={"metadata": {"labels": {INSTANCE_LABEL: instance_label(ids)}}},
                    content_type=MERGE_PATCH,
                )
        await _ensure(pair, ids)
        assert set((await cluster.read("secrets", name))["data"]) == set(SECRET_KEYS)


async def test_delivery_drift_denied_and_repaired_with_sibling_preservation(
    cluster: AcceptanceCluster, make_ids
) -> None:
    """Delivery Secret drift: structural deny, value/version repair, siblings safe."""
    ids = make_ids("ddrift")
    sibling = ids.sibling()
    async with lifecycle_pair(cluster, ids) as pair:
        await pair.a.projection.reconcile_secret_values(**ids.scope(), values=ids.delivery_values())
        await pair.b.projection.reconcile_secret_values(
            **sibling.scope(), values=sibling.delivery_values()
        )
        name = _delivery_name(pair, ids)
        key_a = delivery_key(ids.delivery_ref_a)
        key_b = delivery_key(ids.delivery_ref_b)
        sibling_before = await cluster.read("secrets", _delivery_name(pair, sibling))

        # 1) Unexpected data key: delivery structure is denied, never adopted.
        async with cluster.bootstrap() as api:
            await api.request(
                "PATCH",
                cluster.path("secrets", name),
                body={"data": {"UNEXPECTED": _b64("drift")}},
                content_type=MERGE_PATCH,
            )
        with pytest.raises(SecretDriftError):
            await pair.a.projection.deliver_secret(
                **ids.scope(), secret_ref=ids.delivery_ref_a, value=ids.delivery_value_a1
            )

        # 2) Managed-value tamper (key set restored): deliver repairs the value.
        async with cluster.bootstrap() as api:
            await api.request(
                "PATCH",
                cluster.path("secrets", name),
                body={"data": {"UNEXPECTED": None, key_a: _b64("tampered")}},
                content_type=MERGE_PATCH,
            )
        await pair.a.projection.deliver_secret(
            **ids.scope(), secret_ref=ids.delivery_ref_a, value=ids.delivery_value_a1
        )
        repaired = await cluster.read("secrets", name)
        assert set(repaired["data"]) == {key_a, key_b}
        assert_value_digest_matches(
            decoded_secret_value(repaired, key_a), ids.delivery_value_a1, label="repaired delivery A"
        )
        assert_value_digest_matches(
            decoded_secret_value(repaired, key_b), ids.delivery_value_b1, label="untouched delivery B"
        )
        repaired_versions = _versions(repaired)
        assert repaired_versions[key_a] == value_digest(ids.delivery_value_a1)
        assert repaired_versions[key_b] == value_digest(ids.delivery_value_b1)

        # 3) Malformed version metadata: denied (no silent adoption of drift).
        async with cluster.bootstrap() as api:
            await api.request(
                "PATCH",
                cluster.path("secrets", name),
                body={"metadata": {"annotations": {DELIVERY_VERSIONS_ANNOTATION: "{invalid"}}},
                content_type=MERGE_PATCH,
            )
        with pytest.raises(SecretDriftError):
            await pair.a.projection.deliver_secret(
                **ids.scope(), secret_ref=ids.delivery_ref_a, value=ids.delivery_value_a1
            )

        # Restore valid metadata, then bind a WRONG (well-formed) version
        # digest: production rewrites it to the digest of the actual value.
        async with cluster.bootstrap() as api:
            wrong_versions = json.dumps({key_a: "sha256:" + "0" * 64, key_b: value_digest(ids.delivery_value_b1)})
            await api.request(
                "PATCH",
                cluster.path("secrets", name),
                body={"metadata": {"annotations": {DELIVERY_VERSIONS_ANNOTATION: wrong_versions}}},
                content_type=MERGE_PATCH,
            )
        await pair.a.projection.deliver_secret(
            **ids.scope(), secret_ref=ids.delivery_ref_a, value=ids.delivery_value_a1
        )
        rebound = await cluster.read("secrets", name)
        rebound_versions = _versions(rebound)
        assert rebound_versions[key_a] == value_digest(ids.delivery_value_a1)
        assert rebound_versions[key_b] == value_digest(ids.delivery_value_b1)
        assert_value_digest_matches(
            decoded_secret_value(rebound, key_a), ids.delivery_value_a1, label="delivery A after rebind"
        )

        # The sibling delivery Secret survived every drift/deny/repair step.
        sibling_after = await cluster.read("secrets", _delivery_name(pair, sibling))
        assert_objects_equal(sibling_after, sibling_before, label="sibling delivery Secret")


async def test_reconcile_drops_stale_keys_and_revoke_is_scoped(
    cluster: AcceptanceCluster, make_ids
) -> None:
    ids = make_ids("revoke")
    sibling = ids.sibling()
    async with lifecycle_pair(cluster, ids) as pair:
        await pair.a.projection.reconcile_secret_values(**ids.scope(), values=ids.delivery_values())
        await pair.b.projection.reconcile_secret_values(
            **sibling.scope(), values=sibling.delivery_values()
        )
        # Exact-set reconcile removes refs that disappeared from the new pin.
        await pair.a.projection.reconcile_secret_values(
            **ids.scope(), values={ids.delivery_ref_a: ids.delivery_value_a1}
        )
        delivery = await cluster.read("secrets", _delivery_name(pair, ids))
        assert set(delivery["data"]) == {delivery_key(ids.delivery_ref_a)}
        assert set(_versions(delivery)) == {delivery_key(ids.delivery_ref_a)}

        sibling_name = _delivery_name(pair, sibling)
        sibling_before = await cluster.read("secrets", sibling_name)
        revoked = await pair.a.projection.revoke_secret(**ids.scope(), secret_ref=ids.delivery_ref_a)
        assert revoked.delivered is False
        assert revoked.key == delivery_key(ids.delivery_ref_a)
        main_after = await cluster.read("secrets", _delivery_name(pair, ids))
        if main_after.get("data"):
            raise AssertionError("a fully revoked delivery Secret must have zero data keys")
        assert _versions(main_after) == {}
        sibling_after = await cluster.read("secrets", sibling_name)
        assert_objects_equal(sibling_after, sibling_before, label="sibling delivery after revoke")

        # Idempotent revoke and an untouched source SecretPort record.
        again_rv = resource_version(main_after)
        again = await pair.a.projection.revoke_secret(**ids.scope(), secret_ref=ids.delivery_ref_a)
        assert again.delivered is False
        assert resource_version(await cluster.read("secrets", _delivery_name(pair, ids))) == again_rv
        assert await pair.tenant_secrets.get(key=ids.dsn_key) is not None


async def test_suspend_revoke_cleanup_is_scoped(cluster: AcceptanceCluster, make_ids) -> None:
    ids = make_ids("cleanup")
    sibling = ids.sibling()
    async with lifecycle_pair(cluster, ids) as pair:
        for bundle_digest, backend_digest in sibling.versions():
            pair.artifacts.add(artifact_record(sibling, bundle_digest, backend_digest))
        pair.instances.add(sibling, sibling.bundle_v1)
        pair.tenant_secrets.seed(
            key=sibling.dsn_key, secret_ref=sibling.dsn_ref, value=sibling.dsn_value_v1
        )
        pair.webhook.add(sibling.webhook_ref, sibling.webhook_value)
        await provision(pair)
        await pair.b.projection.reconcile_secret_values(
            **sibling.scope(), values=sibling.delivery_values()
        )
        await pair.b.admin.ensure_deployment(
            **sibling.scope(),
            artifact_digest=sibling.bundle_v1,
            dsn_ref=sibling.dsn_ref,
            webhook_secret_ref=sibling.webhook_ref,
        )
        sibling_before = {
            "delivery": await cluster.read("secrets", _delivery_name(pair, sibling)),
            "secret": await cluster.read("secrets", _secret_name(pair, sibling)),
        }

        # Security revoke: scale-to-zero + scoped delivery revoke/delete only.
        assert await pair.a.admin.suspend(
            company_id=ids.company_id, plugin_id=ids.plugin_id, cleanup_delivery=True
        )
        if await cluster.read("secrets", _delivery_name(pair, ids)) is not None:
            raise AssertionError("security revoke must delete only this instance's delivery Secret")
        lifecycle_secret = await cluster.read("secrets", _secret_name(pair, ids))
        assert lifecycle_secret is not None, "suspend must not delete the lifecycle Secret"
        deployment = await cluster.read("deployments", _secret_name(pair, ids), group="apps")
        assert deployment is not None
        assert deployment["spec"]["replicas"] == 0
        assert_objects_equal(
            await cluster.read("secrets", _delivery_name(pair, sibling)),
            sibling_before["delivery"],
            label="sibling delivery after suspend",
        )
        assert_objects_equal(
            await cluster.read("secrets", _secret_name(pair, sibling)),
            sibling_before["secret"],
            label="sibling lifecycle Secret after suspend",
        )


async def test_no_secret_values_or_tokens_in_logs(
    cluster: AcceptanceCluster, make_ids, caplog: pytest.LogCaptureFixture
) -> None:
    ids = make_ids("logs")
    caplog.set_level(logging.DEBUG)
    admin_tokens: dict[str, str] = {}
    async with lifecycle_pair(cluster, ids) as pair:
        await _ensure(pair, ids)
        pair.tenant_secrets.seed(key=ids.dsn_key, secret_ref=ids.dsn_ref, value=ids.dsn_value_v2)
        await _ensure(pair, ids, side="b")
        await pair.a.projection.reconcile_secret_values(**ids.scope(), values=ids.delivery_values())
        await pair.a.projection.deliver_secret(
            **ids.scope(), secret_ref=ids.delivery_ref_a, value=ids.delivery_value_a2
        )
        await pair.a.projection.revoke_secret(**ids.scope(), secret_ref=ids.delivery_ref_b)
        # Capture the REAL plugin-deployment-admin identities of BOTH lifecycle
        # providers (two processes), not only the bootstrap control-plane ones.
        admin_tokens = {
            "lifecycle-admin-a": await pair.a.transport.provider.get_token(),
            "lifecycle-admin-b": await pair.b.transport.provider.get_token(),
        }
    text = caplog.text
    sentinels: dict[str, str] = {
        "dsn-v1": ids.dsn_value_v1,
        "dsn-v2": ids.dsn_value_v2,
        "webhook": ids.webhook_value,
        "delivery-a1": ids.delivery_value_a1,
        "delivery-a2": ids.delivery_value_a2,
        "delivery-b1": ids.delivery_value_b1,
        **admin_tokens,
        **{f"bootstrap-token-{index}": token for index, token in enumerate(cluster.tokens.values())},
    }
    for code, value in sentinels.items():
        assert_no_sensitive_material(text, value, code=f"{code}-raw")
        assert_no_sensitive_material(text, _b64(value), code=f"{code}-base64")
