"""Load container env bindings from enabled project modules (SoT instance data)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_binding_service import ModuleBindingService
from prodavan.application.modules.module_instance_service import (
    OWNER_CABINET,
    OWNER_PROJECT,
    ModuleInstanceService,
)
from prodavan.application.pod_service.container_env_resolver import (
    field_value_as_env_string,
    field_value_as_secret_ref,
    merge_env_bindings,
    resolve_plain_env,
    resolve_secret_env,
)
from prodavan.application.projects.materialize_planner import _row_applies_to_project
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.modules import ModuleMetaDocumentRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.secrets.cabinet_secret_store import assert_cabinet_secret_scope
from prodavan.infrastructure.secrets.store import SecretStore, get_secret_store


def _spec_cache_key(spec: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(spec.get("table_slug") or "").strip().lower(),
        str(spec.get("row_id") or "").strip(),
        str(spec.get("field") or "").strip(),
    )


def row_eligible_for_env(body: dict[str, Any], project_id: str) -> bool:
    """Whether a module data row may supply container_env value_from fields."""
    if not _row_applies_to_project(body, project_id):
        return False
    # Explicit enabled=false must not inject env (S4B and similar).
    if body.get("enabled") is False:
        return False
    return True


def _cache_getter(cache: dict[tuple[str, str, str], str | None]):
    def getter(spec: dict[str, Any]) -> str | None:
        return cache.get(_spec_cache_key(spec))

    return getter


class ContainerEnvLoader:
    def __init__(
        self,
        session: AsyncSession,
        *,
        secrets: SecretStore | None = None,
    ) -> None:
        self._session = session
        self._secrets = secrets or get_secret_store()
        self._instances = ModuleInstanceService(session)

    async def load_for_project(
        self,
        project: ProjectRow,
        *,
        lifecycle: str = "project.launch",
    ) -> tuple[tuple[str, str], ...]:
        inst = await self._session.get(CabinetInstanceRow, project.cabinet_id)
        if inst is None:
            return ()

        module_ids = await self._enabled_module_ids(project)
        plain_groups: list[list[tuple[str, str]]] = []
        secret_groups: list[list[tuple[str, str]]] = []
        for module_id in sorted(module_ids):
            plain_doc = await self._optional_document(module_id, "container_env")
            if plain_doc is not None:
                plain_cache = await self._build_row_cache(
                    plain_doc,
                    spec_key="value_from",
                    module_id=module_id,
                    project_id=project.id,
                    cabinet_id=project.cabinet_id,
                    secret=False,
                )
                plain_groups.append(
                    resolve_plain_env(
                        plain_doc,
                        lifecycle=lifecycle,
                        row_field_getter=_cache_getter(plain_cache),
                    )
                )
            secret_doc = await self._optional_document(module_id, "container_env_secrets")
            if secret_doc is not None:
                secret_cache = await self._build_row_cache(
                    secret_doc,
                    spec_key="secret_ref_from",
                    module_id=module_id,
                    project_id=project.id,
                    cabinet_id=project.cabinet_id,
                    secret=True,
                )
                secret_groups.append(
                    resolve_secret_env(
                        secret_doc,
                        lifecycle=lifecycle,
                        secret_getter=self._cabinet_scoped_secret_getter(project.cabinet_id),
                        row_field_getter=_cache_getter(secret_cache),
                    )
                )
        return merge_env_bindings(*plain_groups, *secret_groups)

    def _cabinet_scoped_secret_getter(self, cabinet_id: str):
        store = self._secrets

        def getter(secret_ref: str) -> str:
            if secret_ref.startswith(("file://cabinet_secrets/", "vault://cabinet_secrets/")):
                assert_cabinet_secret_scope(secret_ref, cabinet_id)
            return store.get(secret_ref)

        return getter

    async def _build_row_cache(
        self,
        doc: list,
        *,
        spec_key: str,
        module_id: str,
        project_id: str,
        cabinet_id: str,
        secret: bool,
    ) -> dict[tuple[str, str, str], str | None]:
        cache: dict[tuple[str, str, str], str | None] = {}
        for entry in doc:
            if not isinstance(entry, dict):
                continue
            spec = entry.get(spec_key)
            if not isinstance(spec, dict):
                continue
            key = _spec_cache_key(spec)
            if key in cache:
                continue
            body = await self._load_row_body(
                module_id=module_id,
                project_id=project_id,
                cabinet_id=cabinet_id,
                spec=spec,
            )
            if body is None:
                cache[key] = None
                continue
            field = spec.get("field")
            if not isinstance(field, str) or not field:
                cache[key] = None
                continue
            raw = body.get(field)
            cache[key] = (
                field_value_as_secret_ref(raw) if secret else field_value_as_env_string(raw)
            )
        return cache

    async def _sot_instance_id(self, *, module_id: str, project_id: str, cabinet_id: str) -> str | None:
        sot = await self._instances.resolve_sot_instance(
            module_id=module_id,
            owner_kind=OWNER_PROJECT,
            owner_id=project_id,
        )
        if sot is None:
            sot = await self._instances.resolve_sot_instance(
                module_id=module_id,
                owner_kind=OWNER_CABINET,
                owner_id=cabinet_id,
            )
        return sot.id if sot is not None else None

    async def _load_row_body(
        self,
        *,
        module_id: str,
        project_id: str,
        cabinet_id: str,
        spec: dict[str, Any],
    ) -> dict[str, Any] | None:
        table_slug = spec.get("table_slug")
        if not isinstance(table_slug, str) or not table_slug.strip():
            return None
        table_slug = table_slug.strip().lower()

        instance_id = await self._sot_instance_id(
            module_id=module_id, project_id=project_id, cabinet_id=cabinet_id
        )
        if instance_id is None:
            return None

        row_id_spec = spec.get("row_id")
        row_id: str | None = None
        if isinstance(row_id_spec, str) and row_id_spec.strip():
            row_id = row_id_spec.strip().replace("{project_id}", project_id)
            if row_id in ("{row_id}", ""):
                row_id = None

        if row_id is not None:
            row = await self._instances.get_data_row(
                instance_id=instance_id, table_slug=table_slug, row_id=row_id
            )
            if row is None:
                return None
            body = row.get("body")
            return body if isinstance(body, dict) else {}

        rows = await self._instances.list_data_rows(
            instance_id=instance_id, table_slug=table_slug
        )
        for row in rows:
            body = row.get("body") if isinstance(row.get("body"), dict) else {}
            if not row_eligible_for_env(body, project_id):
                continue
            return body
        return None

    async def _enabled_module_ids(self, project: ProjectRow) -> set[str]:
        return set(
            await ModuleBindingService(self._session).list_module_ids_for_project(project.id)
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
