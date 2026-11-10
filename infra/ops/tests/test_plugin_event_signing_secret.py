"""Offline static contracts for the dev plugin-event signing reference.

No kustomize, cluster, network, kubectl, Docker, or Git command is used.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
DEV = ROOT / "infra" / "k3s" / "overlays" / "dev"
PLUGINS = DEV / "plugins"
EXAMPLE = PLUGINS / "plugin-event-signing.sealed.yaml.example"
PATCHES = (
    (DEV / "patch-dev-plugin-event-signing.yaml", "agentscale-api", "api"),
    (PLUGINS / "patch-plugin-event-signing.yaml", "plugin-gateway", "gateway"),
)
REF = "k8s-secret://agentscale-plugin-events/signing-key"


def _documents(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]


def test_sealed_example_has_only_ciphertext_placeholder() -> None:
    documents = _documents(EXAMPLE)
    assert len(documents) == 1
    sealed = documents[0]
    assert sealed["apiVersion"] == "bitnami.com/v1alpha1"
    assert sealed["kind"] == "SealedSecret"
    assert sealed["metadata"] == {
        "name": "agentscale-plugin-events", "namespace": "agentscale-dev"
    }
    assert "data" not in sealed and "stringData" not in sealed
    assert sealed["spec"]["encryptedData"] == {
        "signing-key": "SEALED_CIPHERTEXT_REQUIRED"
    }
    template = sealed["spec"]["template"]
    assert template["metadata"] == sealed["metadata"]
    assert template["type"] == "Opaque"
    assert "data" not in template and "stringData" not in template


def test_example_is_not_applied_and_platform_stays_disabled() -> None:
    dev = yaml.safe_load((DEV / "kustomization.yaml").read_text(encoding="utf-8"))
    plugins = yaml.safe_load((PLUGINS / "kustomization.yaml").read_text(encoding="utf-8"))
    assert all(not str(item).endswith(".example") for item in dev["resources"])
    assert all(not str(item).endswith(".example") for item in plugins["resources"])
    assert {"path": "patch-dev-plugin-event-signing.yaml"} in dev["patches"]
    assert {"path": "patch-plugin-event-signing.yaml"} in plugins["patches"]

    api_config = (ROOT / "infra/k3s/base/agentscale-api/configmap.yaml").read_text(
        encoding="utf-8"
    )
    gateway_config = (ROOT / "infra/k3s/base/plugins/plugin-gateway.yaml").read_text(
        encoding="utf-8"
    )
    assert 'PLUGIN_PLATFORM_ENABLED: "false"' in api_config
    assert 'PLUGIN_PLATFORM_ENABLED: "false"' in gateway_config
    for path, _, _ in PATCHES:
        assert "PLUGIN_PLATFORM_ENABLED" not in path.read_text(encoding="utf-8")


def test_api_and_gateway_share_only_the_opaque_read_only_reference() -> None:
    for path, deployment_name, container_name in PATCHES:
        documents = _documents(path)
        assert len(documents) == 1
        deployment = documents[0]
        assert deployment["kind"] == "Deployment"
        assert deployment["metadata"]["name"] == deployment_name
        pod = deployment["spec"]["template"]["spec"]
        container = next(item for item in pod["containers"] if item["name"] == container_name)
        event_env = [item for item in container["env"] if item["name"] == "PLUGIN_EVENT_SIGNING_SECRET_REF"]
        assert event_env == [{"name": "PLUGIN_EVENT_SIGNING_SECRET_REF", "value": REF}]
        assert all(
            item["name"] not in {"PLUGIN_CLAIMS_SECRET", "AUTH_TEST_SECRET"}
            for item in container["env"]
        )
        mount = next(item for item in container["volumeMounts"] if item["name"] == "plugin-event-signing")
        assert mount["mountPath"] == "/var/run/secrets/agentscale-plugin-events"
        assert mount["readOnly"] is True

        volume = next(item for item in pod["volumes"] if item["name"] == "plugin-event-signing")
        secret = volume["secret"]
        assert secret["secretName"] == "agentscale-plugin-events"
        assert secret["defaultMode"] in (0o400, "0400")
        assert secret["optional"] is True
        assert secret["items"] == [{"key": "signing-key", "path": "signing-key"}]
