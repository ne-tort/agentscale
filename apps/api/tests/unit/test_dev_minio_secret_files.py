"""Static dev-overlay acceptance checks; no kustomize or cluster execution."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
DEV = REPO_ROOT / "infra/k3s/overlays/dev"
BASE = REPO_ROOT / "infra/k3s/base"
PATCH_PATH = DEV / "patch-dev-minio-secret-files.yaml"

PLAINTEXT_ENV = {
    "PLUGIN_MINIO_ADMIN_ACCESS_KEY",
    "PLUGIN_MINIO_ADMIN_SECRET_KEY",
    "PLUGIN_MINIO_PRESIGN_ACCESS_KEY",
    "PLUGIN_MINIO_PRESIGN_SECRET_KEY",
}
EXPECTED_REFS = {
    "PLUGIN_MINIO_ADMIN_ACCESS_KEY_REF": (
        "k8s-secret://agentscale-minio-plugin-admin/access-key"
    ),
    "PLUGIN_MINIO_ADMIN_SECRET_KEY_REF": (
        "k8s-secret://agentscale-minio-plugin-admin/secret-key"
    ),
    "PLUGIN_MINIO_PRESIGN_ACCESS_KEY_REF": (
        "k8s-secret://agentscale-minio-plugin-presigner/access-key"
    ),
    "PLUGIN_MINIO_PRESIGN_SECRET_KEY_REF": (
        "k8s-secret://agentscale-minio-plugin-presigner/secret-key"
    ),
}
EXPECTED_MOUNTS = {
    "minio-plugin-admin-secret": (
        "/var/run/secrets/agentscale-minio-plugin-admin",
        "agentscale-minio-plugin-admin",
    ),
    "minio-plugin-presigner-secret": (
        "/var/run/secrets/agentscale-minio-plugin-presigner",
        "agentscale-minio-plugin-presigner",
    ),
}


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _container(document: dict, name: str) -> dict:
    containers = document["spec"]["template"]["spec"]["containers"]
    return next(container for container in containers if container["name"] == name)


def test_dev_patch_is_registered_and_removes_all_plaintext_minio_env() -> None:
    kustomization = _load(DEV / "kustomization.yaml")
    assert any(
        patch.get("path") == PATCH_PATH.name
        for patch in kustomization["patches"]
        if isinstance(patch, dict)
    )
    patch = _load(PATCH_PATH)
    patch_env = _container(patch, "api")["env"]
    deleted = {item["name"] for item in patch_env if item.get("$patch") == "delete"}
    additions = {item["name"]: item for item in patch_env if "$patch" not in item}

    assert deleted == PLAINTEXT_ENV
    assert PLAINTEXT_ENV.isdisjoint(additions)
    assert set(additions) == set(EXPECTED_REFS)
    for name, expected_ref in EXPECTED_REFS.items():
        assert additions[name]["value"] == expected_ref
        assert "valueFrom" not in additions[name]

    # Apply just this patch's explicit list semantics to the base env. This is
    # deliberately a static assertion, not a kustomize/build invocation.
    base = _load(BASE / "agentscale-api/deployment.yaml")
    base_env = _container(base, "api").get("env", [])
    effective_env = [item for item in base_env if item["name"] not in deleted]
    effective_env.extend(additions.values())
    assert PLAINTEXT_ENV.isdisjoint(item["name"] for item in effective_env)


def test_dev_mounts_are_read_only_and_project_only_the_two_required_keys() -> None:
    patch = _load(PATCH_PATH)
    pod_spec = patch["spec"]["template"]["spec"]
    mounts = {item["name"]: item for item in _container(patch, "api")["volumeMounts"]}
    volumes = {item["name"]: item for item in pod_spec["volumes"]}

    assert set(mounts) == set(EXPECTED_MOUNTS)
    assert set(volumes) == set(EXPECTED_MOUNTS)
    for name, (mount_path, secret_name) in EXPECTED_MOUNTS.items():
        assert mounts[name]["mountPath"] == mount_path
        assert mounts[name]["readOnly"] is True
        secret = volumes[name]["secret"]
        assert secret["secretName"] == secret_name
        assert secret["defaultMode"] == 0o400
        assert not secret.get("optional", False)
        assert {(item["key"], item["path"]) for item in secret["items"]} == {
            ("access-key", "access-key"),
            ("secret-key", "secret-key"),
        }


def test_existing_plugin_buckets_init_provisions_both_required_secret_shapes() -> None:
    documents = list(
        yaml.safe_load_all(
            (BASE / "platform/plugin-buckets-init.yaml").read_text(encoding="utf-8")
        )
    )
    secrets = {
        document["metadata"]["name"]: document
        for document in documents
        if document and document.get("kind") == "Secret"
    }

    for _volume_name, (_mount_path, secret_name) in EXPECTED_MOUNTS.items():
        assert secret_name in secrets
        assert set(secrets[secret_name].get("stringData", {})) == {"access-key", "secret-key"}


def test_dev_sts_canary_init_is_registered_and_uses_only_secret_refs() -> None:
    kustomization = _load(DEV / "kustomization.yaml")
    assert "plugin-sts-canary-init.yaml" in kustomization["resources"]
    path = DEV / "plugin-sts-canary-init.yaml"
    job = _load(path)
    assert job["kind"] == "Job"
    assert job["metadata"]["name"] == "plugin-sts-canary-init"
    container = _container(job, "provisioner")
    assert container["command"] == ["python", "-c"]
    for item in container["env"]:
        assert "value" not in item
        ref = item["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "agentscale-minio"
        assert ref["key"] in {"root-user", "root-password"}
    assert not (BASE / "platform" / path.name).exists()


def test_dev_sts_canary_init_is_idempotent_and_does_not_enable_probe() -> None:
    path = DEV / "plugin-sts-canary-init.yaml"
    raw = path.read_text(encoding="utf-8")
    script = _container(_load(path), "provisioner")["args"][0]
    assert 'BUCKET = "tenant-sts-probe"' in script
    assert 'KEY = "module-sts-probe/__authorization_canary__"' in script
    assert "create_bucket" in script
    assert "BucketAlreadyOwnedByYou" in script
    assert "put_object" in script
    assert 'Body=b""' in script
    assert "ContentLength=0" in script
    assert script.index("head_bucket") < script.index("create_bucket")
    assert script.index("head_object") < script.index("put_object")
    assert 'response.get("ContentLength") != 0' in script
    assert "delete_bucket" not in script
    assert "delete_object" not in script
    assert "PLUGIN_MINIO_STS_PROBE_ENABLED" not in raw
    assert "PLUGIN_MINIO_STS_ENABLED" not in raw
    assert "plugin-object-admin-secret" not in raw
    assert "plugin-object-presigner-secret" not in raw
