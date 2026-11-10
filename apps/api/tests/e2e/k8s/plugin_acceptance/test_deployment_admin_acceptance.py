"""Real-cluster DeploymentAdmin acceptance over two lifecycle processes.

Covers ensure, downwardAPI identity projection, readOnly 0400 capability
delivery mount, owner-token update, same-owner retry, foreign owner/digest and
ownership denial BEFORE any mutation, rollback to the old digest+owner including
update-owner annotation removal with a post-rollback runtime_binding, and
scoped uninstall with sibling preservation.

Backend Deployments intentionally stay at replicas=0: the synthetic verified
artifacts have no pullable images, so this harness validates real API state and
projections instead of starting plugin pods. No Postgres/SqlPluginUpdateLock.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from prodavan.infrastructure.plugins.runtime.deployment_admin import (
    ARTIFACT_ANNOTATION,
    BACKEND_ANNOTATION,
    DELIVERY_DEFAULT_MODE,
    DELIVERY_MOUNT_PATH,
    DELIVERY_VOLUME_PREFIX,
    INSTANCE_LABEL,
    MANAGED_BY,
    MANAGED_BY_VALUE,
    PLUGIN_INSTANCE_ANNOTATION,
    RUNTIME_IDENTITY_MOUNT_PATH,
    RUNTIME_IDENTITY_VOLUME_NAME,
    SECRET_KEYS,
    UPDATE_OWNER_ANNOTATION,
    DeploymentArtifactDigestConflictError,
    DeploymentOwnershipError,
)

from .acceptance_config import ENV_NAMES
from .cluster import AcceptanceCluster
from .lifecycle import (
    AcceptanceIds,
    LifecyclePair,
    annotations_of,
    artifact_record,
    assert_objects_equal,
    delivery_key,
    instance_label,
    lifecycle_pair,
    provision,
    resource_version,
)

CLUSTER_TAG = "deploy"

pytestmark = pytest.mark.plugin_acceptance


_instance_label = instance_label
_delivery_key = delivery_key


def _names(pair: LifecyclePair, ids: AcceptanceIds) -> tuple[str, str]:
    return (
        pair.a.admin.resource_name(ids.plugin_id, ids.plugin_instance_id),
        pair.a.admin.canonical_delivery_secret_name(ids.plugin_id, ids.plugin_instance_id),
    )


async def _snapshot(cluster: AcceptanceCluster, pair: LifecyclePair, ids: AcceptanceIds) -> dict[str, Any]:
    name, delivery_name = _names(pair, ids)
    return {
        "deployment": await cluster.read("deployments", name, group="apps"),
        "service": await cluster.read("services", name),
        "secret": await cluster.read("secrets", name),
        "delivery": await cluster.read("secrets", delivery_name),
    }


def _strict_rvs(snapshot: Mapping[str, Any]) -> dict[str, str]:
    """resourceVersions of objects only this harness writes.

    Deployments are deliberately excluded: the deployment controller adds
    deployment.kubernetes.io/revision and status asynchronously, so a strict rv
    comparison there would measure controller noise, not lifecycle mutations.
    Service/Secret/delivery have no such writer inside the disposable namespace.
    """
    return {key: resource_version(snapshot[key]) for key in ("service", "secret", "delivery")}


def _deployment_state(deployment: Mapping[str, Any]) -> dict[str, Any]:
    """Semantic lifecycle state that a real mutation would have to change."""
    container = _backend_container(deployment)
    return {
        "annotations": {
            key: annotations_of(deployment).get(key)
            for key in (ARTIFACT_ANNOTATION, BACKEND_ANNOTATION, PLUGIN_INSTANCE_ANNOTATION, UPDATE_OWNER_ANNOTATION)
        },
        "template_annotations": {
            key: annotations_of(deployment, template=True).get(key)
            for key in (ARTIFACT_ANNOTATION, BACKEND_ANNOTATION, PLUGIN_INSTANCE_ANNOTATION, UPDATE_OWNER_ANNOTATION)
        },
        "image": container["image"],
        "replicas": deployment["spec"].get("replicas"),
        "selector": deployment["spec"]["selector"],
        "env": container["env"],
        "volumes": deployment["spec"]["template"]["spec"]["volumes"],
        "volumeMounts": container["volumeMounts"],
    }


def _identity_volume(deployment: Mapping[str, Any]) -> Mapping[str, Any]:
    volumes = deployment["spec"]["template"]["spec"]["volumes"]
    return next(volume for volume in volumes if volume.get("name") == RUNTIME_IDENTITY_VOLUME_NAME)


def _backend_container(deployment: Mapping[str, Any]) -> Mapping[str, Any]:
    return deployment["spec"]["template"]["spec"]["containers"][0]


def _assert_identity_projection(deployment: Mapping[str, Any], ids: AcceptanceIds) -> None:
    volume = _identity_volume(deployment)
    items = {
        item["path"]: item["fieldRef"]["fieldPath"]
        for item in volume["downwardAPI"]["items"]
    }
    assert items == {
        "plugin_instance_id": f"metadata.annotations['{PLUGIN_INSTANCE_ANNOTATION}']",
        "backend_digest": f"metadata.annotations['{BACKEND_ANNOTATION}']",
    }
    mounts = _backend_container(deployment)["volumeMounts"]
    assert {
        "name": RUNTIME_IDENTITY_VOLUME_NAME,
        "mountPath": RUNTIME_IDENTITY_MOUNT_PATH,
        "readOnly": True,
    } in mounts


async def test_ensure_initial_projects_identity_and_readonly_delivery(
    cluster: AcceptanceCluster, make_ids
) -> None:
    ids = make_ids("ensure")
    async with lifecycle_pair(cluster, ids) as pair:
        name = await provision(pair)
        assert name == _names(pair, ids)[0]
        snapshot = await _snapshot(cluster, pair, ids)
        deployment = snapshot["deployment"]
        assert deployment is not None
        assert deployment["metadata"]["labels"][MANAGED_BY] == MANAGED_BY_VALUE
        assert deployment["spec"]["replicas"] == 0
        assert deployment["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
        expected = {
            ARTIFACT_ANNOTATION: ids.bundle_v1,
            BACKEND_ANNOTATION: ids.backend_v1,
            PLUGIN_INSTANCE_ANNOTATION: ids.plugin_instance_id,
        }
        for key, value in expected.items():
            assert annotations_of(deployment).get(key) == value
            assert annotations_of(deployment, template=True).get(key) == value
        assert UPDATE_OWNER_ANNOTATION not in annotations_of(deployment)
        assert UPDATE_OWNER_ANNOTATION not in annotations_of(deployment, template=True)
        _assert_identity_projection(deployment, ids)
        assert _backend_container(deployment)["image"] == ids.backend_ref(ids.backend_v1)

        env = {entry["name"]: entry for entry in _backend_container(deployment)["env"]}
        for key in SECRET_KEYS:
            assert env[key]["valueFrom"]["secretKeyRef"] == {"name": name, "key": key}
        assert env["PLUGIN_ARTIFACT_DIGEST"]["valueFrom"]["fieldRef"]["fieldPath"] == (
            f"metadata.annotations['{ARTIFACT_ANNOTATION}']"
        )

        secret = snapshot["secret"]
        assert secret is not None
        assert set(secret["data"]) == set(SECRET_KEYS)
        delivery = snapshot["delivery"]
        assert delivery is not None
        assert delivery["metadata"]["name"] != name, "delivery Secret must be a separate object"
        assert set(delivery["data"]) == {
            _delivery_key(ids.delivery_ref_a),
            _delivery_key(ids.delivery_ref_b),
        }

        volumes = deployment["spec"]["template"]["spec"]["volumes"]
        delivery_volume = next(
            volume for volume in volumes if str(volume.get("name", "")).startswith(DELIVERY_VOLUME_PREFIX)
        )
        delivery_name = _names(pair, ids)[1]
        assert delivery_volume["secret"]["secretName"] == delivery_name
        assert delivery_volume["secret"]["defaultMode"] == DELIVERY_DEFAULT_MODE == 0o400
        assert "items" not in delivery_volume["secret"]
        assert {
            "name": delivery_volume["name"],
            "mountPath": DELIVERY_MOUNT_PATH,
            "readOnly": True,
        } in _backend_container(deployment)["volumeMounts"]

        service = snapshot["service"]
        assert service is not None
        assert service["spec"]["selector"] == {INSTANCE_LABEL: _instance_label(ids)}
        assert deployment["spec"]["selector"]["matchLabels"] == {INSTANCE_LABEL: _instance_label(ids)}

        binding = await pair.a.admin.runtime_binding(
            company_id=ids.company_id, plugin_id=ids.plugin_id
        )
        assert binding.artifact_digest == ids.bundle_v1
        assert binding.update_owner_token is None
        assert binding.plugin_instance_id == ids.plugin_instance_id

        # Idempotent replay from the SECOND process: no Secret/Service writes
        # (strict rv) and no semantic Deployment change (controller rv noise is
        # intentionally not treated as lifecycle idempotency evidence).
        before = await _snapshot(cluster, pair, ids)
        await pair.b.admin.ensure_deployment(
            **ids.scope(),
            artifact_digest=ids.bundle_v1,
            dsn_ref=ids.dsn_ref,
            webhook_secret_ref=ids.webhook_ref,
        )
        after = await _snapshot(cluster, pair, ids)
        assert _strict_rvs(after) == _strict_rvs(before)
        assert _deployment_state(after["deployment"]) == _deployment_state(before["deployment"])


async def _update(pair: LifecyclePair, ids: AcceptanceIds, side: str, **kwargs: Any) -> None:
    admin = pair.a.admin if side == "a" else pair.b.admin
    await admin.update_deployment(
        **ids.scope(),
        dsn_ref=ids.dsn_ref,
        webhook_secret_ref=ids.webhook_ref,
        **kwargs,
    )


async def test_owner_update_same_owner_retry_and_binding(cluster: AcceptanceCluster, make_ids) -> None:
    ids = make_ids("owner")
    owner_a = f"owner-a-{cluster.config.run_id}"
    async with lifecycle_pair(cluster, ids) as pair:
        await provision(pair)
        await _update(
            pair,
            ids,
            "a",
            artifact_digest=ids.bundle_v2,
            expected_current_artifact_digest=ids.bundle_v1,
            update_owner_token=owner_a,
            expected_current_update_owner_token=None,
        )
        pair.instances.pin(ids, ids.bundle_v2)
        snapshot = await _snapshot(cluster, pair, ids)
        deployment = snapshot["deployment"]
        assert annotations_of(deployment).get(UPDATE_OWNER_ANNOTATION) == owner_a
        assert annotations_of(deployment, template=True).get(UPDATE_OWNER_ANNOTATION) == owner_a
        assert annotations_of(deployment).get(ARTIFACT_ANNOTATION) == ids.bundle_v2
        assert annotations_of(deployment).get(BACKEND_ANNOTATION) == ids.backend_v2
        binding = await pair.b.admin.runtime_binding(
            company_id=ids.company_id, plugin_id=ids.plugin_id
        )
        assert binding.artifact_digest == ids.bundle_v2
        assert binding.update_owner_token == owner_a

        # A retried update by the SAME owner after a timeout is idempotent even
        # though its expected-current digest is now stale.
        pair.instances.pin(ids, ids.bundle_v1)
        await _update(
            pair,
            ids,
            "a",
            artifact_digest=ids.bundle_v2,
            expected_current_artifact_digest=ids.bundle_v1,
            update_owner_token=owner_a,
            expected_current_update_owner_token=None,
        )
        after = await _snapshot(cluster, pair, ids)
        assert _strict_rvs(after) == _strict_rvs(snapshot)
        assert _deployment_state(after["deployment"]) == _deployment_state(deployment)
        binding = await pair.a.admin.runtime_binding(
            company_id=ids.company_id, plugin_id=ids.plugin_id
        )
        assert binding.artifact_digest == ids.bundle_v2
        assert binding.update_owner_token == owner_a


async def test_foreign_owner_and_stale_digest_denied_before_mutation(
    cluster: AcceptanceCluster, make_ids
) -> None:
    ids = make_ids("fence")
    owner_a = f"owner-a-{cluster.config.run_id}"
    owner_b = f"owner-b-{cluster.config.run_id}"
    async with lifecycle_pair(cluster, ids) as pair:
        await provision(pair)
        await _update(
            pair,
            ids,
            "a",
            artifact_digest=ids.bundle_v2,
            expected_current_artifact_digest=ids.bundle_v1,
            update_owner_token=owner_a,
            expected_current_update_owner_token=None,
        )
        pair.instances.pin(ids, ids.bundle_v2)
        before_snapshot = await _snapshot(cluster, pair, ids)
        before = _strict_rvs(before_snapshot)

        # Process B tries to steal the update ownership of the SAME digest.
        with pytest.raises(DeploymentArtifactDigestConflictError):
            await _update(
                pair,
                ids,
                "b",
                artifact_digest=ids.bundle_v2,
                expected_current_artifact_digest=ids.bundle_v2,
                update_owner_token=owner_b,
                expected_current_update_owner_token=owner_b,
            )
        # Process B acts on a stale expected digest (live is V2, not V1).
        pair.instances.pin(ids, ids.bundle_v3)
        with pytest.raises(DeploymentArtifactDigestConflictError):
            await _update(
                pair,
                ids,
                "b",
                artifact_digest=ids.bundle_v3,
                expected_current_artifact_digest=ids.bundle_v1,
                update_owner_token=owner_b,
                expected_current_update_owner_token=owner_b,
            )
        after = await _snapshot(cluster, pair, ids)
        assert _strict_rvs(after) == before, "a denied update must not mutate Service/Secret state"
        assert _deployment_state(after["deployment"]) == _deployment_state(before_snapshot["deployment"]), (
            "a denied update must not change the live Deployment digest/owner/image"
        )
        binding = await pair.a.admin.runtime_binding(
            company_id=ids.company_id, plugin_id=ids.plugin_id
        )
        assert binding.artifact_digest == ids.bundle_v2
        assert binding.update_owner_token == owner_a

        # Foreign ownership labels are rejected before any write as well.
        name = _names(pair, ids)[0]
        async with cluster.bootstrap() as api:
            await api.request(
                "PATCH",
                cluster.path("deployments", name, group="apps"),
                body={"metadata": {"labels": {INSTANCE_LABEL: None}}},
                content_type="application/merge-patch+json",
            )
            try:
                with pytest.raises(DeploymentOwnershipError):
                    await _update(
                        pair,
                        ids,
                        "b",
                        artifact_digest=ids.bundle_v3,
                        expected_current_artifact_digest=ids.bundle_v2,
                        update_owner_token=owner_b,
                        expected_current_update_owner_token=owner_a,
                    )
            finally:
                await api.request(
                    "PATCH",
                    cluster.path("deployments", name, group="apps"),
                    body={"metadata": {"labels": {INSTANCE_LABEL: _instance_label(ids)}}},
                    content_type="application/merge-patch+json",
                )
        restored = await cluster.read("deployments", name, group="apps")
        assert annotations_of(restored).get(ARTIFACT_ANNOTATION) == ids.bundle_v2
        assert annotations_of(restored).get(UPDATE_OWNER_ANNOTATION) == owner_a
        final_rvs = _strict_rvs(await _snapshot(cluster, pair, ids))
        assert final_rvs["secret"] == before["secret"]
        assert final_rvs["delivery"] == before["delivery"]


async def test_rollback_restores_digest_owner_and_removes_annotation(
    cluster: AcceptanceCluster, make_ids
) -> None:
    ids = make_ids("rollback")
    owner_a = f"owner-a-{cluster.config.run_id}"
    owner_b = f"owner-b-{cluster.config.run_id}"
    async with lifecycle_pair(cluster, ids) as pair:
        await provision(pair)
        await _update(
            pair,
            ids,
            "a",
            artifact_digest=ids.bundle_v2,
            expected_current_artifact_digest=ids.bundle_v1,
            update_owner_token=owner_a,
            expected_current_update_owner_token=None,
        )
        pair.instances.pin(ids, ids.bundle_v2)

        # Legitimate owner handoff to the second process (V3, owner B).
        pair.instances.pin(ids, ids.bundle_v3)
        await _update(
            pair,
            ids,
            "b",
            artifact_digest=ids.bundle_v3,
            expected_current_artifact_digest=ids.bundle_v2,
            update_owner_token=owner_b,
            expected_current_update_owner_token=owner_a,
        )
        binding = await pair.a.admin.runtime_binding(
            company_id=ids.company_id, plugin_id=ids.plugin_id
        )
        assert binding.artifact_digest == ids.bundle_v3
        assert binding.update_owner_token == owner_b

        # Rollback to the previous digest AND previous owner.
        pair.instances.pin(ids, ids.bundle_v2)
        await _update(
            pair,
            ids,
            "a",
            artifact_digest=ids.bundle_v2,
            expected_current_artifact_digest=ids.bundle_v3,
            update_owner_token=owner_a,
            expected_current_update_owner_token=owner_b,
        )
        deployment = (await _snapshot(cluster, pair, ids))["deployment"]
        assert annotations_of(deployment).get(ARTIFACT_ANNOTATION) == ids.bundle_v2
        assert annotations_of(deployment).get(UPDATE_OWNER_ANNOTATION) == owner_a
        assert annotations_of(deployment, template=True).get(UPDATE_OWNER_ANNOTATION) == owner_a

        # Final rollback to V1 with NO owner: the annotation must be removed
        # from both the Deployment and its pod template, not left stale.
        pair.instances.pin(ids, ids.bundle_v1)
        await _update(
            pair,
            ids,
            "a",
            artifact_digest=ids.bundle_v1,
            expected_current_artifact_digest=ids.bundle_v2,
            update_owner_token=None,
            expected_current_update_owner_token=owner_a,
        )
        snapshot = await _snapshot(cluster, pair, ids)
        deployment = snapshot["deployment"]
        assert UPDATE_OWNER_ANNOTATION not in annotations_of(deployment)
        assert UPDATE_OWNER_ANNOTATION not in annotations_of(deployment, template=True)
        assert annotations_of(deployment).get(ARTIFACT_ANNOTATION) == ids.bundle_v1
        assert annotations_of(deployment).get(BACKEND_ANNOTATION) == ids.backend_v1
        assert annotations_of(deployment, template=True).get(ARTIFACT_ANNOTATION) == ids.bundle_v1
        assert _backend_container(deployment)["image"] == ids.backend_ref(ids.backend_v1)
        _assert_identity_projection(deployment, ids)

        # Post-rollback runtime_binding as observed by the OTHER process.
        binding = await pair.b.admin.runtime_binding(
            company_id=ids.company_id, plugin_id=ids.plugin_id
        )
        assert binding.artifact_digest == ids.bundle_v1
        assert binding.update_owner_token is None
        assert binding.plugin_instance_id == ids.plugin_instance_id


async def test_uninstall_is_scoped_and_preserves_sibling(cluster: AcceptanceCluster, make_ids) -> None:
    ids = make_ids("uninstall")
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
        sibling_snapshot = await _snapshot(cluster, pair, sibling)

        assert await pair.a.admin.uninstall(company_id=ids.company_id, plugin_id=ids.plugin_id)
        removed = await _snapshot(cluster, pair, ids)
        if any(value is not None for value in removed.values()):
            raise AssertionError("uninstall must remove only this instance's owned resources")
        sibling_after = await _snapshot(cluster, pair, sibling)
        if not all(value is not None for value in sibling_after.values()):
            raise AssertionError("sibling instance resources must survive the other uninstall")
        assert _strict_rvs(sibling_after) == _strict_rvs(sibling_snapshot), "sibling objects must be untouched"
        assert _deployment_state(sibling_after["deployment"]) == _deployment_state(
            sibling_snapshot["deployment"]
        ), "sibling Deployment must be untouched"

        assert await pair.b.admin.uninstall(
            company_id=sibling.company_id, plugin_id=sibling.plugin_id
        )
        if any(value is not None for value in (await _snapshot(cluster, pair, sibling)).values()):
            raise AssertionError("sibling uninstall must remove the sibling's own resources")


async def test_scope_is_k8s_only_and_has_no_sql_update_lock(cluster: AcceptanceCluster, make_ids) -> None:
    """SqlPluginUpdateLock needs PostgreSQL: it is NOT part of this harness.

    There is no simulated lock success anywhere in this package; a separate
    PostgreSQL acceptance harness owns that contract. This guard fails if the
    SQL lock or a database setting is ever wired into the K8s-only composition.
    """
    ids = make_ids("scope")
    async with lifecycle_pair(cluster, ids) as pair:
        for component in (pair.a.admin, pair.b.admin, pair.a.projection, pair.b.projection):
            for value in vars(component).values():
                assert "UpdateLock" not in type(value).__name__
                assert "Sql" not in type(value).__name__
    assert not any("DATABASE" in name or "POSTGRES" in name for name in ENV_NAMES)


SIDECAR_VOLUME_NAME = "acceptance-nonmanaged-data"
SIDECAR_MOUNT_PATH = "/var/tmp/acceptance-nonmanaged"


def _mount_named(deployment: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return next(
        mount for mount in _backend_container(deployment)["volumeMounts"] if mount.get("name") == name
    )


def _delivery_volume_of(deployment: Mapping[str, Any]) -> Mapping[str, Any]:
    return next(
        volume
        for volume in deployment["spec"]["template"]["spec"]["volumes"]
        if str(volume.get("name", "")).startswith(DELIVERY_VOLUME_PREFIX)
    )


def _expected_entries(deployment: Mapping[str, Any]) -> dict[str, Any]:
    mounts = _backend_container(deployment)["volumeMounts"]
    return {
        "identity_volume": _identity_volume(deployment),
        "identity_mount": _mount_named(deployment, RUNTIME_IDENTITY_VOLUME_NAME),
        "delivery_volume": _delivery_volume_of(deployment),
        "delivery_mount": next(m for m in mounts if m.get("mountPath") == DELIVERY_MOUNT_PATH),
        "sidecar_volume": next(
            volume
            for volume in deployment["spec"]["template"]["spec"]["volumes"]
            if volume.get("name") == SIDECAR_VOLUME_NAME
        ),
        "sidecar_mount": _mount_named(deployment, SIDECAR_VOLUME_NAME),
    }


def _assert_entries_intact(deployment: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    current = _expected_entries(deployment)
    for key, value in expected.items():
        assert_objects_equal(current[key], value, label=f"deployment entry {key}")


async def test_non_managed_volumes_survive_update_retry_deny_rollback_uninstall(
    cluster: AcceptanceCluster, make_ids
) -> None:
    """Non-managed sibling volumes/mounts must survive every lifecycle write.

    A bootstrap-injected emptyDir volume+mount is NOT owned by the lifecycle
    adapter. Production _apply must retain it verbatim across update,
    same-owner retry, foreign owner/digest denials and rollback, while the
    managed identity/delivery entries stay intact and uninstall still deletes
    only the instance's own resources (sibling instance preserved).
    """
    ids = make_ids("volumes")
    owner_a = f"owner-a-{cluster.config.run_id}"
    owner_b = f"owner-b-{cluster.config.run_id}"
    async with lifecycle_pair(cluster, ids) as pair:
        name = await provision(pair)
        # Inject a non-managed volume+mount; merge patch replaces lists, so the
        # full current lists plus the sidecar entries are sent (no secrets).
        live = await cluster.read("deployments", name, group="apps")
        volumes = list(live["spec"]["template"]["spec"]["volumes"])
        backend = dict(_backend_container(live))
        mounts = list(backend["volumeMounts"])
        volumes.append({"name": SIDECAR_VOLUME_NAME, "emptyDir": {}})
        mounts.append({"name": SIDECAR_VOLUME_NAME, "mountPath": SIDECAR_MOUNT_PATH, "readOnly": True})
        backend["volumeMounts"] = mounts
        async with cluster.bootstrap() as api:
            await api.request(
                "PATCH",
                cluster.path("deployments", name, group="apps"),
                body={"spec": {"template": {"spec": {"volumes": volumes, "containers": [backend]}}}},
                content_type="application/merge-patch+json",
            )
        expected = _expected_entries(await cluster.read("deployments", name, group="apps"))

        # Update to V2 with an owner token: sidecar and managed entries intact.
        await _update(
            pair,
            ids,
            "a",
            artifact_digest=ids.bundle_v2,
            expected_current_artifact_digest=ids.bundle_v1,
            update_owner_token=owner_a,
            expected_current_update_owner_token=None,
        )
        pair.instances.pin(ids, ids.bundle_v2)
        _assert_entries_intact(await cluster.read("deployments", name, group="apps"), expected)

        # Same-owner retry: retention is idempotent, not duplicated/dropped.
        pair.instances.pin(ids, ids.bundle_v1)
        await _update(
            pair,
            ids,
            "a",
            artifact_digest=ids.bundle_v2,
            expected_current_artifact_digest=ids.bundle_v1,
            update_owner_token=owner_a,
            expected_current_update_owner_token=None,
        )
        _assert_entries_intact(await cluster.read("deployments", name, group="apps"), expected)

        # Foreign owner and stale digest denials change nothing at all.
        before = _strict_rvs(await _snapshot(cluster, pair, ids))
        pair.instances.pin(ids, ids.bundle_v2)
        with pytest.raises(DeploymentArtifactDigestConflictError):
            await _update(
                pair,
                ids,
                "b",
                artifact_digest=ids.bundle_v2,
                expected_current_artifact_digest=ids.bundle_v2,
                update_owner_token=owner_b,
                expected_current_update_owner_token=owner_b,
            )
        pair.instances.pin(ids, ids.bundle_v3)
        with pytest.raises(DeploymentArtifactDigestConflictError):
            await _update(
                pair,
                ids,
                "b",
                artifact_digest=ids.bundle_v3,
                expected_current_artifact_digest=ids.bundle_v1,
                update_owner_token=owner_b,
                expected_current_update_owner_token=owner_b,
            )
        _assert_entries_intact(await cluster.read("deployments", name, group="apps"), expected)
        assert _strict_rvs(await _snapshot(cluster, pair, ids)) == before

        # Rollback to V1 without owner: managed entries still intact, sidecar
        # preserved, and the owner annotation removed from both levels.
        pair.instances.pin(ids, ids.bundle_v1)
        await _update(
            pair,
            ids,
            "a",
            artifact_digest=ids.bundle_v1,
            expected_current_artifact_digest=ids.bundle_v2,
            update_owner_token=None,
            expected_current_update_owner_token=owner_a,
        )
        rolled_back = await cluster.read("deployments", name, group="apps")
        _assert_entries_intact(rolled_back, expected)
        assert UPDATE_OWNER_ANNOTATION not in annotations_of(rolled_back)
        assert UPDATE_OWNER_ANNOTATION not in annotations_of(rolled_back, template=True)
        assert annotations_of(rolled_back).get(ARTIFACT_ANNOTATION) == ids.bundle_v1

        # Uninstall deletes only this instance's owned resources; the sibling
        # instance (and its objects) is preserved.
        sibling = ids.sibling()
        for bundle_digest, backend_digest in sibling.versions():
            pair.artifacts.add(artifact_record(sibling, bundle_digest, backend_digest))
        pair.instances.add(sibling, sibling.bundle_v1)
        pair.tenant_secrets.seed(
            key=sibling.dsn_key, secret_ref=sibling.dsn_ref, value=sibling.dsn_value_v1
        )
        pair.webhook.add(sibling.webhook_ref, sibling.webhook_value)
        await pair.b.projection.reconcile_secret_values(
            **sibling.scope(), values=sibling.delivery_values()
        )
        await pair.b.admin.ensure_deployment(
            **sibling.scope(),
            artifact_digest=sibling.bundle_v1,
            dsn_ref=sibling.dsn_ref,
            webhook_secret_ref=sibling.webhook_ref,
        )
        sibling_before = await _snapshot(cluster, pair, sibling)
        assert await pair.a.admin.uninstall(company_id=ids.company_id, plugin_id=ids.plugin_id)
        if any(value is not None for value in (await _snapshot(cluster, pair, ids)).values()):
            raise AssertionError("uninstall must remove only this instance's owned resources")
        sibling_after = await _snapshot(cluster, pair, sibling)
        if not all(value is not None for value in sibling_after.values()):
            raise AssertionError("sibling instance resources must survive the other uninstall")
        assert _strict_rvs(sibling_after) == _strict_rvs(sibling_before)
        assert _deployment_state(sibling_after["deployment"]) == _deployment_state(
            sibling_before["deployment"]
        )
