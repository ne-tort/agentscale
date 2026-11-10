"""Изоляция объявленных manifest-scope'ов на один доверенный instance.

``validate_plugin_instance_scopes`` вызывается из ModuleSource и CI-публикации
ДО любых SQL/provider-мутаций: он не даёт подписанному манифесту адресовать
чужой plugin instance через ``pods.allowlist`` или ref'ы ``secret_delivery``.
Прямых тестов не было — проверялись только следствия в двух сервисах.
"""

from __future__ import annotations

import pytest

from prodavan.domain.plugins.helpers import plugin_instance_id_for
from prodavan.domain.plugins.scopes import validate_plugin_instance_scopes
from prodavan.domain.plugins.types import (
    BackendEntrypoint,
    Capability,
    FrontendEntrypoint,
    MigrationEngine,
    MigrationsDeclaration,
    PluginManifest,
    PodAllowlistEntry,
    PodsDeclaration,
    SecretDeliveryDeclaration,
)

COMPANY = "co_acme"
PLUGIN = "hello_plugin"
INSTANCE = plugin_instance_id_for(COMPANY, PLUGIN)


def _manifest(*, plugin_id: str = PLUGIN, pods=None, secret_delivery=None) -> PluginManifest:
    return PluginManifest(
        plugin_id=plugin_id,
        version="1.2.0",
        protocol="plugin-rpc/1",
        frontend=FrontendEntrypoint(
            entry="index.html", base_path="/pluginpage/x/",
        ),
        backend=BackendEntrypoint(port=8080, health="/health"),
        capabilities=(Capability.CACHE,),
        migrations=MigrationsDeclaration(
            engine=MigrationEngine.LIQUIBASE, path="migrations/changelog.xml",
        ),
        egress_ref="grant:co_123/x",
        public_pages=(),
        webhooks=(),
        min_platform_version="1.0.0",
        pods=pods,
        secret_delivery=secret_delivery,
    )


def _accepting(**_: str) -> None:
    return None


def _rejecting(**_: str) -> None:
    raise ValueError("bad ref")


def test_manifest_without_scope_declarations_is_valid() -> None:
    # Отсутствие объявления не даёт доступа и не является ошибкой.
    validate_plugin_instance_scopes(
        _manifest(),
        company_id=COMPANY,
        plugin_id=PLUGIN,
        secret_ref_validator=_accepting,
    )


def test_rejects_foreign_plugin_instance_in_pod_allowlist() -> None:
    foreign = PodAllowlistEntry(
        plugin_instance_id=plugin_instance_id_for("co_foreign", PLUGIN),
        project_id="pr_1",
        job_id="job_1",
    )
    with pytest.raises(ValueError, match="pods.allowlist"):
        validate_plugin_instance_scopes(
            _manifest(pods=PodsDeclaration(allowlist=(foreign,))),
            company_id=COMPANY,
            plugin_id=PLUGIN,
            secret_ref_validator=_accepting,
        )


def test_accepts_own_instance_in_pod_allowlist() -> None:
    own = PodAllowlistEntry(
        plugin_instance_id=INSTANCE,
        project_id="pr_1",
        job_id="job_1",
    )
    validate_plugin_instance_scopes(
        _manifest(pods=PodsDeclaration(allowlist=(own,))),
        company_id=COMPANY,
        plugin_id=PLUGIN,
        secret_ref_validator=_accepting,
    )


def test_secret_ref_validator_receives_the_trusted_instance() -> None:
    seen: list[dict[str, str]] = []

    def record(**kwargs: str) -> None:
        seen.append(kwargs)

    validate_plugin_instance_scopes(
        _manifest(secret_delivery=SecretDeliveryDeclaration(
            mode="k8s_secret", allowlist=("vault://a/b",),
        )),
        company_id=COMPANY,
        plugin_id=PLUGIN,
        secret_ref_validator=record,
    )
    assert seen == [{
        "company_id": COMPANY,
        "plugin_id": PLUGIN,
        "plugin_instance_id": INSTANCE,
        "secret_ref": "vault://a/b",
    }]


def test_secret_ref_failure_is_normalized_without_leaking_the_ref() -> None:
    with pytest.raises(ValueError) as caught:
        validate_plugin_instance_scopes(
            _manifest(secret_delivery=SecretDeliveryDeclaration(
                mode="k8s_secret",
                allowlist=("vault://ai_keys/secrets/co_other/x",),
            )),
            company_id=COMPANY,
            plugin_id=PLUGIN,
            secret_ref_validator=_rejecting,
        )
    assert "vault://" not in str(caught.value)
    assert "does not match the trusted" in str(caught.value)


def test_manifest_plugin_id_must_match_requested_scope() -> None:
    with pytest.raises(ValueError, match="does not match the requested scope"):
        validate_plugin_instance_scopes(
            _manifest(plugin_id="other_plugin"),
            company_id=COMPANY,
            plugin_id=PLUGIN,
            secret_ref_validator=_accepting,
        )


def test_explicit_expected_instance_overrides_the_derived_one() -> None:
    legacy = plugin_instance_id_for("co_legacy", PLUGIN)
    own = PodAllowlistEntry(
        plugin_instance_id=legacy,
        project_id="pr_1",
        job_id="job_1",
    )
    # Сохранённый легаси-id остаётся авторитетным, если пришёл от сервера.
    validate_plugin_instance_scopes(
        _manifest(pods=PodsDeclaration(allowlist=(own,))),
        company_id=COMPANY,
        plugin_id=PLUGIN,
        secret_ref_validator=_accepting,
        expected_instance_id=legacy,
    )


def test_invalid_explicit_instance_is_rejected() -> None:
    with pytest.raises(ValueError, match="expected_instance_id is invalid"):
        validate_plugin_instance_scopes(
            _manifest(),
            company_id=COMPANY,
            plugin_id=PLUGIN,
            secret_ref_validator=_accepting,
            expected_instance_id="../escape",
        )


def test_non_manifest_and_non_callable_validator_are_programming_errors() -> None:
    with pytest.raises(TypeError):
        validate_plugin_instance_scopes(
            {},  # type: ignore[arg-type]
            company_id=COMPANY,
            plugin_id=PLUGIN,
            secret_ref_validator=_accepting,
        )
    with pytest.raises(TypeError):
        validate_plugin_instance_scopes(
            _manifest(),
            company_id=COMPANY,
            plugin_id=PLUGIN,
            secret_ref_validator=None,  # type: ignore[arg-type]
        )
