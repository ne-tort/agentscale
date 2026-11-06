"""Tenant-scoped persistence for resource roots, versions and bindings.

Design points that the HTTP surface relies on:

* **Tenant scoping without a tenant column.** ``resources`` carries a polymorphic
  ``(scope_kind, scope_id)`` pair, so every read is filtered by the set of owner
  scopes derived from the request tenant (``SqlAlchemyResourceScopeProof``). A
  resource in another tenant's scope is simply absent — the caller answers
  ``404``.
* **Durable idempotency.** A create receipt and one receipt per appended version
  are stored inside ``resources.metadata`` in the *same* transaction as the row
  they describe, so a replayed key returns the stored row instead of a duplicate.
  A concurrent duplicate still loses to ``uq_resource_scope_kind_slug`` or
  ``uq_resource_version_number`` and surfaces as ``StoreConflict``.
* **Compare-and-set on lifecycle.** ``append_version`` and ``transition_lifecycle``
  lock the root row and compare ``active_version`` with the caller's expectation,
  so a lost race is ``StaleResourceState`` and never an in-place rewrite.
* **The generation is not ours.** ``project_generation`` reads the durable
  ``runtime_revision_heads`` ledger through
  ``application.resource_materialization.durable`` — this adapter never derives a
  generation from a plan, a request or process memory.

Every mutating method commits its own unit of work: one HTTP request performs one
mutation, and a partially committed request must not exist.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.migration.resources.lifecycle_contracts import (
    ResourceKind,
    ResourceLifecycle,
    ResourceScope,
    ScopeKind,
    VersionStatus,
)
from prodavan.application.resources.api.contracts import (
    METADATA_CREATE_RECEIPT,
    METADATA_LIFECYCLE,
    METADATA_VERSION_NOTES,
    METADATA_VERSION_RECEIPTS,
    NewVersion,
    ResourceAggregate,
    StoredBinding,
    StoredResource,
    StoredVersion,
)
from prodavan.application.resources.api.errors import StoreConflict
from prodavan.infrastructure.persistence.models.resources import (
    ResourceBindingRow,
    ResourceRow,
    ResourceVersionRow,
)
from prodavan.infrastructure.persistence.models.runtime_revisions import (
    RuntimeRevisionHeadRow,
)

from .sqlalchemy_scope import SqlAlchemyResourceScopeProof

_DEFAULT_LIFECYCLE = ResourceLifecycle.DRAFT.value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lifecycle_expr():
    return func.jsonb_extract_path_text(ResourceRow.metadata_, METADATA_LIFECYCLE)


def _receipt_expr(key: str):
    return func.jsonb_extract_path_text(
        ResourceRow.metadata_, METADATA_CREATE_RECEIPT, "idempotency_key"
    ) == key
class SqlAlchemyResourceStore:
    """ORM-backed implementation of ``ResourceStorePort``."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        scopes: SqlAlchemyResourceScopeProof | None = None,
    ) -> None:
        self._session = session
        self._scopes = scopes or SqlAlchemyResourceScopeProof(session)

    # -- helpers --------------------------------------------------------

    async def _scope_filter(self, tenant_id: str, column_kind, column_id):
        pairs = await self._scopes.owner_scope_pairs(tenant_id)
        return or_(
            *[
                and_(column_kind == kind, column_id == scope_id)
                for kind, scope_id in pairs
            ]
        )

    async def _owns(self, tenant_id: str, kind: str, scope_id: str) -> bool:
        pair = (kind, scope_id)
        return pair in set(await self._scopes.owner_scope_pairs(tenant_id))

    async def _tenant_for(self, kind: str, scope_id: str) -> str:
        tenant_id = await self._scopes.tenant_of(kind, scope_id)
        if not tenant_id:
            raise StoreConflict("tenant_scope_unresolved")
        return tenant_id

    def _stored(self, row: ResourceRow, *, tenant_id: str) -> StoredResource:
        return StoredResource(
            tenant_id=tenant_id,
            resource_id=str(row.id),
            kind=ResourceKind(row.kind),
            slug=str(row.slug),
            title=str(row.title),
            description=_text_of(row, "description"),
            scope=ResourceScope(
                kind=ScopeKind(row.scope_kind),
                scope_id=str(row.scope_id),
                tenant_id=tenant_id,
            ),
            lifecycle=_lifecycle_of(row),
            active_version=row.active_version,
            metadata=dict(row.metadata_ or {}),
            created_by=str(row.created_by) if row.created_by else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _stored_version(self, row: ResourceVersionRow) -> StoredVersion:
        return StoredVersion(
            resource_id=str(row.resource_id),
            version_id=str(row.id),
            number=int(row.number),
            status=VersionStatus(row.status),
            payload=dict(row.payload or {}),
            content_digest=str(row.checksum or ""),
            created_by=str(row.created_by) if row.created_by else None,
            created_at=row.created_at,
        )

    @staticmethod
    def _stored_binding(row: ResourceBindingRow) -> StoredBinding:
        return StoredBinding(
            tenant_id="",
            binding_id=str(row.id),
            resource_id=str(row.resource_id),
            target_kind=str(row.target_scope_kind),
            target_id=str(row.target_scope_id),
            binding_kind=str(row.binding_kind),
            child_may_edit=bool(row.child_may_edit),
            version_number=int(row.version_number or 1),
            project_ids=tuple(row.project_ids or ()),
            session_id=str(row.session_id) if row.session_id else None,
            created_by=str(row.created_by) if row.created_by else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    # -- reads ----------------------------------------------------------

    async def get_resource(self, *, tenant_id: str, resource_id: str) -> StoredResource | None:
        row = await self._session.get(ResourceRow, resource_id)
        if row is None:
            return None
        if not await self._owns(tenant_id, str(row.scope_kind), str(row.scope_id)):
            return None
        return self._stored(row, tenant_id=tenant_id)

    async def list_resources(
        self,
        *,
        tenant_id: str,
        kind: ResourceKind | None = None,
        lifecycle: ResourceLifecycle | None = None,
        limit: int = 50,
        after_id: str | None = None,
    ) -> tuple[StoredResource, ...]:
        stmt = select(ResourceRow).where(
            await self._scope_filter(tenant_id, ResourceRow.scope_kind, ResourceRow.scope_id)
        )
        if kind is not None:
            stmt = stmt.where(ResourceRow.kind == kind.value)
        if lifecycle is not None:
            if lifecycle is ResourceLifecycle.DRAFT:
                # Rows written before the metadata key existed are drafts.
                stmt = stmt.where(
                    or_(
                        _lifecycle_expr() == lifecycle.value,
                        _lifecycle_expr().is_(None),
                    )
                )
            else:
                stmt = stmt.where(_lifecycle_expr() == lifecycle.value)
        if after_id:
            stmt = stmt.where(ResourceRow.id > after_id)
        stmt = stmt.order_by(ResourceRow.id).limit(max(1, limit))
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(self._stored(row, tenant_id=tenant_id) for row in rows)

    async def aggregates(
        self, *, tenant_id: str, resource_ids: Sequence[str]
    ) -> Mapping[str, ResourceAggregate]:
        ids = [value for value in resource_ids if value]
        if not ids:
            return {}
        version_rows = await self._session.execute(
            select(ResourceVersionRow.resource_id, ResourceVersionRow.number).where(
                ResourceVersionRow.resource_id.in_(ids)
            )
        )
        versions: dict[str, list[int]] = {}
        for resource_id, number in version_rows.all():
            versions.setdefault(str(resource_id), []).append(int(number))
        binding_rows = await self._session.execute(
            select(ResourceBindingRow.resource_id, func.count())
            .where(ResourceBindingRow.resource_id.in_(ids))
            .group_by(ResourceBindingRow.resource_id)
        )
        bindings = {str(resource_id): int(count) for resource_id, count in binding_rows.all()}
        return {
            value: ResourceAggregate(
                version_numbers=tuple(versions.get(value, ())),
                binding_count=bindings.get(value, 0),
            )
            for value in ids
        }

    async def list_versions(self, *, resource_id: str) -> tuple[StoredVersion, ...]:
        rows = await self._session.execute(
            select(ResourceVersionRow)
            .where(ResourceVersionRow.resource_id == resource_id)
            .order_by(ResourceVersionRow.number)
        )
        return tuple(self._stored_version(row) for row in rows.scalars().all())

    async def get_version(self, *, resource_id: str, number: int) -> StoredVersion | None:
        row = await self._session.scalar(
            select(ResourceVersionRow).where(
                ResourceVersionRow.resource_id == resource_id,
                ResourceVersionRow.number == number,
            )
        )
        return self._stored_version(row) if row is not None else None

    # -- writes ---------------------------------------------------------

    async def create_resource(
        self,
        *,
        tenant_id: str,
        kind: ResourceKind,
        slug: str,
        title: str,
        description: str,
        scope: ResourceScope,
        metadata: Mapping[str, object],
        created_by: str,
        first_version: NewVersion,
        idempotency_key: str,
        request_digest: str,
    ) -> tuple[StoredResource, bool]:
        existing = await self._session.scalar(
            select(ResourceRow)
            .where(
                await self._scope_filter(
                    tenant_id, ResourceRow.scope_kind, ResourceRow.scope_id
                ),
                _receipt_expr(idempotency_key),
            )
            .limit(1)
        )
        if existing is not None:
            return self._stored(existing, tenant_id=tenant_id), True

        payload = dict(metadata)
        payload[METADATA_LIFECYCLE] = str(payload.get(METADATA_LIFECYCLE) or _DEFAULT_LIFECYCLE)
        payload[METADATA_CREATE_RECEIPT] = {
            "idempotency_key": idempotency_key,
            "request_digest": request_digest,
            "version_number": 1,
        }
        notes = dict(payload.get(METADATA_VERSION_NOTES) or {})
        notes["1"] = {"label": first_version.label, "change_note": first_version.change_note}
        payload[METADATA_VERSION_NOTES] = notes
        receipts = dict(payload.get(METADATA_VERSION_RECEIPTS) or {})
        receipts[idempotency_key] = {"request_digest": request_digest, "number": 1}
        payload[METADATA_VERSION_RECEIPTS] = receipts

        root = ResourceRow(
            kind=kind.value,
            slug=slug,
            title=title,
            scope_kind=scope.kind.value,
            scope_id=scope.scope_id,
            metadata_=payload,
            active_version=None,
            created_by=created_by,
        )
        self._session.add(root)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise StoreConflict("resource_slug_conflict") from exc

        version = ResourceVersionRow(
            resource_id=root.id,
            number=1,
            status=VersionStatus.DRAFT.value,
            payload=dict(first_version.payload),
            checksum=first_version.content_digest,
            created_by=created_by,
        )
        self._session.add(version)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise StoreConflict("resource_version_conflict") from exc
        await self._session.refresh(root)
        return self._stored(root, tenant_id=tenant_id), False

    async def append_version(
        self,
        *,
        resource_id: str,
        version: NewVersion,
        created_by: str,
        expected_active_version: int | None,
        idempotency_key: str,
        request_digest: str,
    ) -> tuple[StoredVersion, bool]:
        root = await self._session.scalar(
            select(ResourceRow).where(ResourceRow.id == resource_id).with_for_update()
        )
        if root is None:
            raise StoreConflict("resource_missing")
        receipts = dict((root.metadata_ or {}).get(METADATA_VERSION_RECEIPTS) or {})
        recorded = receipts.get(idempotency_key)
        if isinstance(recorded, Mapping):
            number = recorded.get("number")
            stored = await self._session.scalar(
                select(ResourceVersionRow).where(
                    ResourceVersionRow.resource_id == root.id,
                    ResourceVersionRow.number == int(number or 0),
                )
            )
            if stored is not None:
                return self._stored_version(stored), True
        if expected_active_version != root.active_version:
            raise StoreConflict("active_version_stale")

        highest = await self._session.scalar(
            select(func.max(ResourceVersionRow.number)).where(
                ResourceVersionRow.resource_id == root.id
            )
        )
        number = int(highest or 0) + 1
        row = ResourceVersionRow(
            resource_id=root.id,
            number=number,
            status=VersionStatus.DRAFT.value,
            payload=dict(version.payload),
            checksum=version.content_digest,
            created_by=created_by,
        )
        self._session.add(row)
        payload = dict(root.metadata_ or {})
        notes = dict(payload.get(METADATA_VERSION_NOTES) or {})
        notes[str(number)] = {"label": version.label, "change_note": version.change_note}
        payload[METADATA_VERSION_NOTES] = notes
        receipts[idempotency_key] = {"request_digest": request_digest, "number": number}
        payload[METADATA_VERSION_RECEIPTS] = receipts
        root.metadata_ = payload
        root.updated_at = _now()
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise StoreConflict("resource_version_conflict") from exc
        await self._session.refresh(row)
        return self._stored_version(row), False

    async def transition_lifecycle(
        self,
        *,
        resource_id: str,
        lifecycle: ResourceLifecycle,
        active_version: int | None,
        version_statuses: Sequence[tuple[str, VersionStatus]],
        expected_active_version: int | None,
    ) -> StoredResource:
        root = await self._session.scalar(
            select(ResourceRow).where(ResourceRow.id == resource_id).with_for_update()
        )
        if root is None:
            raise StoreConflict("resource_missing")
        if expected_active_version != root.active_version:
            raise StoreConflict("active_version_stale")
        payload = dict(root.metadata_ or {})
        payload[METADATA_LIFECYCLE] = lifecycle.value
        root.metadata_ = payload
        root.active_version = active_version
        root.updated_at = _now()
        for version_id, status in version_statuses:
            row = await self._session.get(ResourceVersionRow, version_id)
            if row is None or str(row.resource_id) != str(root.id):
                continue
            row.status = status.value
            if status is VersionStatus.PUBLISHED:
                row.published_at = _now()
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise StoreConflict("resource_lifecycle_conflict") from exc
        await self._session.refresh(root)
        tenant_id = await self._tenant_for(str(root.scope_kind), str(root.scope_id))
        return self._stored(root, tenant_id=tenant_id)

    # -- bindings -------------------------------------------------------

    async def list_bindings(
        self, *, tenant_id: str, resource_id: str | None = None
    ) -> tuple[StoredBinding, ...]:
        stmt = select(ResourceBindingRow).where(
            ResourceBindingRow.resource_id.in_(
                select(ResourceRow.id).where(
                    await self._scope_filter(
                        tenant_id, ResourceRow.scope_kind, ResourceRow.scope_id
                    )
                )
            )
        )
        if resource_id:
            stmt = stmt.where(ResourceBindingRow.resource_id == resource_id)
        rows = await self._session.execute(stmt.order_by(ResourceBindingRow.id))
        return tuple(self._stored_binding(row) for row in rows.scalars().all())

    async def list_project_bindings(
        self, *, tenant_id: str, project_id: str
    ) -> tuple[StoredBinding, ...]:
        stmt = (
            select(ResourceBindingRow)
            .where(
                ResourceBindingRow.target_scope_kind == "project",
                ResourceBindingRow.target_scope_id == project_id,
                ResourceBindingRow.resource_id.in_(
                    select(ResourceRow.id).where(
                        await self._scope_filter(
                            tenant_id, ResourceRow.scope_kind, ResourceRow.scope_id
                        )
                    )
                ),
            )
            .order_by(ResourceBindingRow.id)
        )
        rows = await self._session.execute(stmt)
        return tuple(self._stored_binding(row) for row in rows.scalars().all())

    async def get_binding(
        self, *, tenant_id: str, resource_id: str, binding_id: str
    ) -> StoredBinding | None:
        row = await self._session.scalar(
            select(ResourceBindingRow).where(
                ResourceBindingRow.id == binding_id,
                ResourceBindingRow.resource_id == resource_id,
            )
        )
        if row is None:
            return None
        root = await self._session.get(ResourceRow, row.resource_id)
        if root is None or not await self._owns(
            tenant_id, str(root.scope_kind), str(root.scope_id)
        ):
            return None
        return self._stored_binding(row)

    async def create_binding(
        self,
        *,
        tenant_id: str,
        binding: StoredBinding,
        request_digest: str,
    ) -> tuple[StoredBinding, bool]:
        root = await self._session.get(ResourceRow, binding.resource_id)
        if root is None:
            raise StoreConflict("resource_missing")
        existing = await self._session.scalar(
            select(ResourceBindingRow).where(
                ResourceBindingRow.resource_id == binding.resource_id,
                ResourceBindingRow.target_scope_kind == binding.target_kind,
                ResourceBindingRow.target_scope_id == binding.target_id,
                ResourceBindingRow.session_id.is_(None),
            )
        )
        if existing is not None:
            same_edge = (
                str(existing.binding_kind) == binding.binding_kind.value
                and int(existing.version_number or 0) == binding.version_number
                and bool(existing.child_may_edit) == binding.child_may_edit
            )
            if not same_edge:
                raise StoreConflict("binding_exists")
            return self._stored_binding(existing), True
        row = ResourceBindingRow(
            resource_id=binding.resource_id,
            target_scope_kind=binding.target_kind,
            target_scope_id=binding.target_id,
            binding_kind=binding.binding_kind.value,
            child_may_edit=binding.child_may_edit,
            version_number=binding.version_number,
            project_ids=list(binding.project_ids),
            session_id=binding.session_id,
            created_by=binding.created_by,
        )
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise StoreConflict("binding_exists") from exc
        await self._session.refresh(row)
        return self._stored_binding(row), False

    async def delete_binding(
        self, *, tenant_id: str, resource_id: str, binding_id: str
    ) -> bool:
        root = await self._session.get(ResourceRow, resource_id)
        if root is None or not await self._owns(
            tenant_id, str(root.scope_kind), str(root.scope_id)
        ):
            return False
        result = await self._session.execute(
            delete(ResourceBindingRow).where(
                ResourceBindingRow.id == binding_id,
                ResourceBindingRow.resource_id == resource_id,
            )
        )
        if not result.rowcount:
            return False
        await self._session.commit()
        return True

    # -- generation -----------------------------------------------------

    async def project_generation(self, *, tenant_id: str, project_id: str) -> int:
        """Durable generation from ``runtime_revision_heads`` (owned elsewhere)."""
        try:
            from prodavan.application.resource_materialization.durable.contracts import (
                resource_key_for_project,
            )
            from prodavan.application.resource_materialization.durable.heads import (
                SqlAlchemyRevisionHeadStore,
            )
        except Exception:  # noqa: BLE001 - degraded to the plain aggregate below
            resource_key_for_project = None  # type: ignore[assignment]
            SqlAlchemyRevisionHeadStore = None  # type: ignore[assignment]

        if SqlAlchemyRevisionHeadStore is not None and resource_key_for_project is not None:
            observed = await SqlAlchemyRevisionHeadStore().project_generation(
                self._session,
                project_id=project_id,
                resource_key=resource_key_for_project(project_id),
            )
            return int(observed or 0)

        observed = await self._session.scalar(
            select(func.max(RuntimeRevisionHeadRow.generation)).where(
                RuntimeRevisionHeadRow.project_id == project_id
            )
        )
        return int(observed or 0)


def _text_of(row: ResourceRow, key: str) -> str:
    value = (row.metadata_ or {}).get(key)
    return value if isinstance(value, str) else ""


def _lifecycle_of(row: ResourceRow) -> ResourceLifecycle:
    raw = (row.metadata_ or {}).get(METADATA_LIFECYCLE)
    try:
        return ResourceLifecycle(raw) if raw is not None else ResourceLifecycle.DRAFT
    except ValueError:
        # An unknown stored lifecycle is corrupt, not a draft; the command layer
        # reports it through ``views.lifecycle_of`` as a stable error code.
        return ResourceLifecycle.DRAFT


__all__ = ["SqlAlchemyResourceStore"]
