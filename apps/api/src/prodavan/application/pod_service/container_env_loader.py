"""Load container env bindings from enabled project modules."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_binding_service import ModuleBindingService
from prodavan.application.pod_service.container_env_resolver import (
    merge_env_bindings,
    resolve_plain_env,
    resolve_secret_env,
)
from prodavan.infrastructure.persistence.models.modules import ModuleMetaDocumentRow
from prodavan.infrastructure.persistence.models.projects import ProjectModuleBindingRow, ProjectRow
from prodavan.infrastructure.secrets.store import SecretStore, get_secret_store


class ContainerEnvLoader:
    def __init__(
        self,
        session: AsyncSession,
        *,
        secrets: SecretStore | None = None,
    ) -> None:
        self._session = session
        self._secrets = secrets or get_secret_store()

    async def load_for_project(
        self,
        project: ProjectRow,
        *,
        lifecycle: str = "project.launch",
    ) -> tuple[tuple[str, str], ...]:
        module_ids = await self._enabled_module_ids(project)
        plain_groups: list[list[tuple[str, str]]] = []
        secret_groups: list[list[tuple[str, str]]] = []
        for module_id in sorted(module_ids):
            plain_doc = await self._optional_document(module_id, "container_env")
            if plain_doc is not None:
                plain_groups.append(resolve_plain_env(plain_doc, lifecycle=lifecycle))
            secret_doc = await self._optional_document(module_id, "container_env_secrets")
            if secret_doc is not None:
                secret_groups.append(
                    resolve_secret_env(
                        secret_doc,
                        lifecycle=lifecycle,
                        secret_getter=self._secrets.get,
                    )
                )
        return merge_env_bindings(*plain_groups, *secret_groups)

    async def _enabled_module_ids(self, project: ProjectRow) -> set[str]:
        q = await self._session.execute(
            select(ProjectModuleBindingRow.module_id).where(
                ProjectModuleBindingRow.project_id == project.id
            )
        )
        bound = set(q.scalars().all())
        if bound:
            return bound
        return set(
            await ModuleBindingService(self._session).list_module_ids_for_cabinet(project.cabinet_id)
        )

    async def _optional_document(self, module_id: str, slug: str) -> list | None:
        q = await self._session.execute(
            select(ModuleMetaDocumentRow.body).where(
                ModuleMetaDocumentRow.module_id == module_id,
                ModuleMetaDocumentRow.slug == slug,
            )
        )
        body = q.scalar_one_or_none()
        if not isinstance(body, list):
            return None
        return body
