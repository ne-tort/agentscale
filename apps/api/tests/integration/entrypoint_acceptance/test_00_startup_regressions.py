"""Offline regressions for cold-start configuration blockers (not executed)."""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from prodavan.application.plugins.artifacts.oci import parse_oci_ref
from prodavan.application.plugins.jobs.tasks import ManifestJobTasks
from prodavan.application.plugins.wiring.config import PluginPlatformConfig
from prodavan.infrastructure.secrets.file_store import FileSecretStore

from .probe import _composition, _running_ttl_renewed
from .processes import API_ROOT, ManagedProcess, isolated_environment
from .world import (
    EntrypointWorld,
    build_minio_acceptance_config,
    validated_oci_registry,
)

pytestmark = [pytest.mark.integration, pytest.mark.entrypoint_acceptance]


def test_disposable_engines_are_disposed_without_async_context_manager() -> None:
    """`create_async_engine` returns an AsyncEngine, which is not an async CM.

    `async with create_async_engine(...)` raised TypeError before any network
    call, so the whole world fixture could never open on any OS. Engines must
    go through the explicit `_engine_scope` helper instead.
    """
    from sqlalchemy.ext.asyncio import AsyncEngine

    from . import database

    assert not hasattr(AsyncEngine, "__aenter__")
    assert not hasattr(AsyncEngine, "aclose")
    assert hasattr(AsyncEngine, "dispose")
    source = inspect.getsource(database)
    assert "async with _engine_for(" not in source
    assert "async with _engine_scope(" in source


def test_cabinet_parent_is_flushed_before_dependent_seed_rows() -> None:
    """The seed must not rely on ORM ordering for real foreign keys.

    `cabinet_company_grants` references `cabinet_instances`, but no ORM
    relationship connects those models, so SQLAlchemy emitted the grant INSERT
    first and PostgreSQL raised ForeignKeyViolationError.
    """
    from . import durable

    source = inspect.getsource(durable.seed_bootstrap_identity)
    parent = source.index("CabinetInstanceRow(")
    flush = source.index("await session.flush()", parent)
    dependent = source.index("CabinetCompanyGrantRow(", parent)
    assert parent < flush < dependent


def test_processes_use_api_root_not_tests_for_cwd_pythonpath_and_helpers() -> None:
    expected = Path(__file__).resolve().parents[3]
    assert API_ROOT.resolve() == expected.resolve()
    assert API_ROOT.name == "api"
    assert (API_ROOT / "src").is_dir()
    assert API_ROOT != API_ROOT / "tests"
    env = isolated_environment({})
    assert env["PYTHONPATH"] == str(API_ROOT / "src")
    owner = SimpleNamespace(
        environment=env,
        sensitive=set(),
        config=SimpleNamespace(shutdown_timeout_seconds=1.0),
    )
    helper = EntrypointWorld.helper(owner, "worker-deferred")
    assert helper.argv[1:4] == [
        "-m", "tests.integration.entrypoint_acceptance.probe", "worker-deferred"
    ]
    assert "cwd=str(API_ROOT)" in inspect.getsource(ManagedProcess.start)
    assert "parents[2]" not in inspect.getsource(ManagedProcess)


def test_schemeless_digest_pinned_bundle_ref_has_valid_uvicorn_oci_registry() -> None:
    digest = "sha256:" + "a" * 64
    bundle_ref = "registry.example.test:5443/disposable/entrypoint@" + digest
    ref = parse_oci_ref(bundle_ref)
    registry = validated_oci_registry(bundle_ref)
    assert registry == "registry.example.test:5443"
    assert registry
    assert ref.digest == digest
    assert ref.image_ref() == bundle_ref
    # Exercise the same production validation used by both cold entrypoints.
    PluginPlatformConfig(
        enabled=True,
        claims_secret="entrypoint-test-claims-secret-32-bytes",
        event_signing_secret_ref="file://ai_keys/entrypoint-event.secret",
        static_origin="https://static.acceptance.test",
        shell_origin="https://shell.acceptance.test",
        cosign_identity="https://github.com/disposable/entrypoint/.github/workflows/sign.yml@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        oci_registry=registry,
    ).validate()


def test_minio_provisioner_gets_distinct_admin_and_presign_secrets_with_redaction() -> None:
    config = build_minio_acceptance_config(
        endpoint="https://minio.acceptance.test:9443",
        root_access_key="disposable-root",
        root_secret_key="root-secret-value",
        admin_secret_key="admin-secret-value",
        presign_secret_key="presign-secret-value",
        mc_binary="mc",
        region="us-east-1",
    )
    assert config.admin_secret_key == "admin-secret-value"
    assert config.presign_secret_key == "presign-secret-value"
    assert len({
        config.root_secret_key,
        config.admin_secret_key,
        config.presign_secret_key,
    }) == 3
    with pytest.raises(RuntimeError, match="distinct"):
        build_minio_acceptance_config(
            endpoint=config.endpoint,
            root_access_key=config.root_access_key,
            root_secret_key="same-secret",
            admin_secret_key="same-secret",
            presign_secret_key="presign-secret-value",
            mc_binary="mc",
            region="us-east-1",
        )
    source = inspect.getsource(EntrypointWorld._provision_minio)
    assert "self.sensitive.update({admin_secret, presign_secret})" in source
    assert "admin_secret_key=admin_secret" in source
    assert "presign_secret_key=presign_secret" in source


def test_composition_probe_uses_lifespan_worker_and_closure_owner() -> None:
    source = inspect.getsource(_composition)
    assert "get_lifespan_manager" in source
    assert "get_worker_manager" not in source
    assert "reader.__defaults__[0].__self__" in source

    class Store:
        async def by_digest(self, digest: str) -> str:
            return digest

    store = Store()
    reader = ManifestJobTasks._select_artifact_reader(SimpleNamespace(_store=store))
    assert reader.__defaults__ is not None
    assert reader.__defaults__[0].__self__ is store


def test_lease_renewal_counts_only_growing_running_ttl() -> None:
    running = {"state": "running"}
    completed = {"state": "completed"}
    assert _running_ttl_renewed(running, 20_000, 19_000, False) is True
    assert _running_ttl_renewed(running, 19_000, 19_000, False) is False
    assert _running_ttl_renewed(running, 20_000, None, False) is False
    assert _running_ttl_renewed(completed, 60_000, 19_000, False) is False
    assert _running_ttl_renewed(running, 60_000, 19_000, True) is False
    assert "previous_ttl + 1000" not in inspect.getsource(_running_ttl_renewed)


def test_tampered_event_uses_valid_different_sha256_digest() -> None:
    from .test_04_signed_events import test_signed_event_is_real_kafka_wire_and_hmac_verified

    source = inspect.getsource(test_signed_event_is_real_kafka_wire_and_hmac_verified)
    assert 'tampered["payload_digest"] = "sha256:" + "f" * 64' in source
    digest = "sha256:" + "f" * 64
    assert len(digest) == 71
    assert digest != "sha256:" + "0" * 64


def test_cross_tenant_denial_is_typed_forbidden_not_503() -> None:
    from .test_06_bootstrap_scope import test_cross_tenant_private_bootstrap_is_typed_forbidden

    source = inspect.getsource(test_cross_tenant_private_bootstrap_is_typed_forbidden)
    assert "response.status_code == 403" in source
    assert 'payload.get("code") == "FORBIDDEN"' in source
    assert "503" not in source


def test_startup_failure_markers_are_mutation_specific() -> None:
    from .test_99_revoke_shutdown_startup_failures import _expect_startup_failure

    source = inspect.getsource(_expect_startup_failure)
    for marker in (
        "plugin_job_declarations_required",
        "MISSING_DEPENDENCY",
        "plugin_job_publication_load_failed",
        "redis: ping failed on startup",
    ):
        assert marker in source
    assert "secret not in tail" in source


@pytest.mark.asyncio
async def test_secret_refs_are_deleted_and_no_files_remain(tmp_path: Path) -> None:
    world = EntrypointWorld.__new__(EntrypointWorld)
    world.secret_root = tmp_path / "secrets"
    world._secret_refs = set()
    world.sensitive = set()
    first = world._put_secret("first", "first-test-only-secret")
    second = world._put_secret("second", "second-test-only-secret")
    assert len(world._secret_refs) == 2
    assert FileSecretStore(root=world.secret_root).get(first) == "first-test-only-secret"
    assert FileSecretStore(root=world.secret_root).get(second) == "second-test-only-secret"
    await world._close_secrets()
    assert world._secret_refs == set()
    assert not world.secret_root.exists()
    assert list(tmp_path.rglob("*.secret")) == []
