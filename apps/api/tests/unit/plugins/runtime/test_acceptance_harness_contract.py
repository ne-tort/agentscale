"""Regression tests for acceptance-harness identity and RBAC contracts.

The disposable kind kubeconfig authenticates the bootstrap identity with an
administrator client certificate. If that certificate is sent together with a
TokenRequest Bearer token, the API server authenticates the certificate subject
and every RBAC assertion silently turns into an administrator check. These
tests pin the split between bootstrap and bearer-token TLS identities and the
single resourceNames-scoped TokenRequest RBAC rule.
"""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest

from tests.e2e.k8s.plugin_acceptance.acceptance_config import AcceptanceConfig
from tests.e2e.k8s.plugin_acceptance.cluster import ADMIN_SA, rbac_manifests
from tests.e2e.k8s.plugin_acceptance import kubeconfig as kubeconfig_module
from tests.e2e.k8s.plugin_acceptance.kubeconfig import (
    KubeconfigAuth,
    KubeconfigIdentity,
    NoMountedTokenAuth,
    StaticTokenAuth,
    load_identity,
)


def _config(path: Path) -> AcceptanceConfig:
    return AcceptanceConfig(
        kubeconfig_path=path,
        context=None,
        namespace_prefix="plugin-acc",
        run_id="run01",
        ttl_seconds=60,
    )


def test_token_request_role_is_scoped_only_to_lifecycle_admin() -> None:
    config = AcceptanceConfig(
        kubeconfig_path=Path("unused-offline-kubeconfig"),
        context=None,
        namespace_prefix="plugin-acc",
        run_id="run01",
        ttl_seconds=60,
    )
    manifests = rbac_manifests(config, "plugin-acc-run01-token")
    token_role = next(
        item for item in manifests
        if item["kind"] == "Role" and item["metadata"]["name"].endswith("-token-request")
    )
    assert token_role["rules"] == [
        {
            "apiGroups": [""],
            "resources": ["serviceaccounts/token"],
            "resourceNames": [ADMIN_SA],
            "verbs": ["create"],
        }
    ]
    # This is the only TokenRequest capability: unrelated SAs get no binding.
    token_rules = [
        rule
        for item in manifests
        if item["kind"] == "Role"
        for rule in item["rules"]
        if "serviceaccounts/token" in rule["resources"]
    ]
    assert token_rules == token_role["rules"]


def test_bearer_identities_use_token_only_ssl_context() -> None:
    bootstrap_context = ssl.create_default_context()
    token_context = ssl.create_default_context()
    identity = KubeconfigIdentity(
        api_base="https://127.0.0.1:6443",
        ssl_context=bootstrap_context,
        token_ssl_context=token_context,
    )

    bootstrap_kwargs = KubeconfigAuth(identity).client_kwargs()
    assert bootstrap_kwargs["verify"] is bootstrap_context

    static_kwargs = StaticTokenAuth(identity, "scoped-token").client_kwargs()
    assert static_kwargs["verify"] is token_context
    assert static_kwargs["trust_env"] is False

    no_mounted = NoMountedTokenAuth(identity)
    assert no_mounted.client_kwargs()["verify"] is token_context


def test_token_only_ssl_context_never_inherits_bootstrap_client_cert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded_cert_contexts: list[ssl.SSLContext] = []
    monkeypatch.setattr(
        ssl.SSLContext,
        "load_cert_chain",
        lambda self, *args, **kwargs: loaded_cert_contexts.append(self),
    )
    bootstrap_context = kubeconfig_module._tls_context(None, insecure=False)
    # Bootstrap identity: admin client certificate loaded into its context.
    bootstrap_context.load_cert_chain(certfile="dummy.crt", keyfile="dummy.key")

    token_context = kubeconfig_module._tls_context(None, insecure=False)
    assert token_context is not bootstrap_context
    assert loaded_cert_contexts == [bootstrap_context]
    assert token_context.check_hostname is True
    assert token_context.verify_mode is ssl.CERT_REQUIRED


def test_load_identity_wires_token_context_for_static_token_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kubeconfig = tmp_path / "disposable.kubeconfig"
    kubeconfig.write_text(
        """apiVersion: v1
kind: Config
current-context: kind-pf-test
clusters:
- name: pf-test
  cluster:
    server: https://127.0.0.1:6443
    insecure-skip-tls-verify: true
contexts:
- name: kind-pf-test
  context:
    cluster: pf-test
    user: pf-test
users:
- name: pf-test
  user:
    token: bootstrap-token
""",
        encoding="utf-8",
    )

    identity = load_identity(_config(kubeconfig))
    try:
        token_context = identity.token_ssl_context
        assert token_context is not None
        assert token_context is not identity.ssl_context
        assert StaticTokenAuth(identity, "scoped").client_kwargs()["verify"] is token_context
        assert NoMountedTokenAuth(identity).client_kwargs()["verify"] is token_context
        assert KubeconfigAuth(identity).client_kwargs()["verify"] is identity.ssl_context
    finally:
        identity.cleanup()
