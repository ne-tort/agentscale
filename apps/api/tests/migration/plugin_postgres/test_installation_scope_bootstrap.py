"""Real SQL installation-scope repository and private/public bootstrap acceptance."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from prodavan.api.v1.plugin_bootstrap import private_bootstrap
from prodavan.application.plugins.bootstrap.service import PluginBootstrapService
from prodavan.application.plugins.installation_scope.repository import SqlInstallationScopeRepository
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.installation_scope import PluginInstallationScopeRow
from prodavan.infrastructure.persistence.models.plugin_platform import ModuleSourceRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

from ._fixtures import PLUGIN_ID
from ._scope_fixtures import (
    CABINET_ID,
    COMPANY_ID,
    PROJECT_ID,
    seed_ready_bootstrap,
    session_factory,
)
from .conftest import HEAD_REVISION, alembic_run, engine_for

pytestmark = [pytest.mark.integration, pytest.mark.plugin_postgres]

ORIGINS = {
    "plugin_static_origin": "https://static.acceptance.test",
    "plugin_shell_origin": "https://shell.acceptance.test",
}


async def prepare_head(db) -> None:
    await alembic_run(db.dsn, "upgrade", "head")
    assert await db.stamped_revision() == HEAD_REVISION


async def scope_count(db) -> int:
    return int(await db.scalar("SELECT count(*) FROM plugin_installation_scopes") or 0)


async def test_ready_source_scope_and_private_public_bootstrap(pg_disposable_db) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        factory = session_factory(engine)
        seeded = await seed_ready_bootstrap(engine)
        repository = SqlInstallationScopeRepository(session_factory=factory)

        scope = await repository.ensure_from_source(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            plugin_instance_id=seeded.plugin_instance_id,
        )
        assert (scope.company_id, scope.cabinet_id, scope.project_id) == (
            COMPANY_ID,
            CABINET_ID,
            PROJECT_ID,
        )
        assert scope.plugin_instance_id == seeded.plugin_instance_id
        repeated = await repository.ensure_from_source(
            company_id=COMPANY_ID,
            plugin_id=PLUGIN_ID,
            plugin_instance_id=seeded.plugin_instance_id,
        )
        assert repeated == scope
        assert await scope_count(db) == 1
        stored = await db.rows(
            """
            SELECT plugin_instance_id, company_id, cabinet_id, project_id,
                   created_at, updated_at
            FROM plugin_installation_scopes
            """
        )
        assert len(stored) == 1
        row = stored[0]
        assert tuple(row[:4]) == (
            seeded.plugin_instance_id,
            COMPANY_ID,
            CABINET_ID,
            PROJECT_ID,
        )
        assert row[4] is not None and row[5] is not None

        service = PluginBootstrapService(session_factory=factory)
        with patch.object(settings, "plugin_static_origin", ORIGINS["plugin_static_origin"]):
            with patch.object(settings, "plugin_shell_origin", ORIGINS["plugin_shell_origin"]):
                private = await service.private(
                    employee=seeded.employee,
                    company_id=COMPANY_ID,
                    plugin_id=PLUGIN_ID,
                )
                assert private.scope.company_id == COMPANY_ID
                assert private.scope.cabinet_id == CABINET_ID
                assert private.scope.project_id == PROJECT_ID
                assert private.scope.session_id is None
                assert private.manifest_version == seeded.record.version
                assert private.artifact_digest == seeded.record.digest
                with patch(
                    "prodavan.api.v1.plugin_bootstrap.get_session_factory",
                    return_value=factory,
                ):
                    http_private = await private_bootstrap(
                        company_id=COMPANY_ID,
                        plugin_id=PLUGIN_ID,
                        route_path="/",
                        employee=seeded.employee,
                    )
                assert http_private.scope.company_id == COMPANY_ID
                assert http_private.scope.cabinet_id == CABINET_ID
                assert http_private.scope.project_id == PROJECT_ID

                public = await service.public(company_id=COMPANY_ID, plugin_id=PLUGIN_ID)
                assert public.scope.company_id == COMPANY_ID
                assert public.scope.cabinet_id is None
                assert public.scope.project_id is None
                assert public.scope.session_id is None

        with pytest.raises(AppError) as wrong_tenant:
            await repository.get(
                company_id="co_other",
                plugin_instance_id=seeded.plugin_instance_id,
            )
        assert wrong_tenant.value.code == "PLUGIN_BOOTSTRAP_SCOPE_MISMATCH"

        async with factory() as session:
            durable = await session.get(
                PluginInstallationScopeRow, seeded.plugin_instance_id
            )
            assert durable is not None
            await session.delete(durable)
            await session.commit()
        assert await scope_count(db) == 0

        with patch.object(settings, "plugin_static_origin", ORIGINS["plugin_static_origin"]):
            with patch.object(settings, "plugin_shell_origin", ORIGINS["plugin_shell_origin"]):
                with patch(
                    "prodavan.api.v1.plugin_bootstrap.get_session_factory",
                    return_value=factory,
                ):
                    with pytest.raises(AppError) as missing:
                        await private_bootstrap(
                            company_id=COMPANY_ID,
                            plugin_id=PLUGIN_ID,
                            route_path="/",
                            employee=seeded.employee,
                        )
                assert missing.value.code == "PLUGIN_BOOTSTRAP_SCOPE_UNAVAILABLE"

                # Public bootstrap never requires or inherits private parent scope.
                public_without_scope = await service.public(
                    company_id=COMPANY_ID,
                    plugin_id=PLUGIN_ID,
                )
                assert public_without_scope.scope.company_id == COMPANY_ID
                assert public_without_scope.scope.cabinet_id is None
                assert public_without_scope.scope.project_id is None

                async with factory() as session:
                    project = await session.get(ProjectRow, PROJECT_ID)
                    assert project is not None
                    project.status = "paused"
                    await session.commit()
                with pytest.raises(AppError) as inactive_project:
                    await repository.ensure_from_source(
                        company_id=COMPANY_ID,
                        plugin_id=PLUGIN_ID,
                        plugin_instance_id=seeded.plugin_instance_id,
                    )
                assert inactive_project.value.code == "PLUGIN_BOOTSTRAP_SCOPE_MISMATCH"
                async with factory() as session:
                    project = await session.get(ProjectRow, PROJECT_ID)
                    assert project is not None
                    project.status = "active"
                    await session.commit()
                restored = await repository.ensure_from_source(
                    company_id=COMPANY_ID,
                    plugin_id=PLUGIN_ID,
                    plugin_instance_id=seeded.plugin_instance_id,
                )
                assert restored.project_id == PROJECT_ID


async def test_missing_or_nondeterministic_binding_is_not_backfilled(pg_disposable_db) -> None:
    db = pg_disposable_db
    await prepare_head(db)
    async with engine_for(db.dsn) as engine:
        factory = session_factory(engine)
        seeded = await seed_ready_bootstrap(engine)
        repository = SqlInstallationScopeRepository(session_factory=factory)

        with pytest.raises(AppError) as random_id:
            await repository.ensure_from_source(
                company_id=COMPANY_ID,
                plugin_id=PLUGIN_ID,
                plugin_instance_id="pinst_random_legacy",
            )
        assert random_id.value.code == "PLUGIN_INSTALLATION_SCOPE_UNAVAILABLE"
        assert await scope_count(db) == 0

        async with factory() as session:
            publication = seeded.record.publication
            assert publication is not None
            source = await session.get(ModuleSourceRow, publication.source_id)
            assert source is not None and source.status == "ready"
            source.cabinet_id = None
            source.project_id = None
            await session.commit()

        with pytest.raises(AppError) as missing_pair:
            await repository.ensure_from_source(
                company_id=COMPANY_ID,
                plugin_id=PLUGIN_ID,
                plugin_instance_id=seeded.plugin_instance_id,
            )
        assert missing_pair.value.code == "PLUGIN_INSTALLATION_SCOPE_UNAVAILABLE"
        assert await scope_count(db) == 0
