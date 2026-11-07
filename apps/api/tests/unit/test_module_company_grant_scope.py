"""Admin module company-grant scope — «Все компании» / «Выбранные компании».

``modules.company_grant_scope`` used to be honoured on read only, so nothing
could set it. These tests pin the admin write path: the scope is an ordinary
field of any module, a switch is effective immediately, and the explicit grant
list survives the round trip (so switching back to ``selected`` restores the
selection the administrator had made).

Run against in-memory SQLite so the real queries decide visibility, not mocks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles

from prodavan.api.v1 import admin_modules
from prodavan.api.v1.admin_modules import PatchAdminModuleBody
from prodavan.application.modules.company_module_service import CompanyModuleService
from prodavan.application.modules.module_service import ModuleService
from prodavan.domain.errors import AppError
from prodavan.domain.modules import ModuleCompanyGrantScope
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.persistence.models.modules import (
    ModuleCabinetBindingRow,
    ModuleCompanyGrantRow,
    ModuleInstanceDataRow,
    ModuleInstanceMetaDocumentRow,
    ModuleInstanceRow,
    ModuleMetaDocumentRow,
    ModuleProjectBindingRow,
    ModuleRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


# Every table the module catalog services touch, so the queries really execute.
_TABLES = (
    CompanyRow,
    CabinetInstanceRow,
    CabinetCompanyGrantRow,
    ProjectRow,
    ModuleRow,
    ModuleMetaDocumentRow,
    ModuleCompanyGrantRow,
    ModuleCabinetBindingRow,
    ModuleProjectBindingRow,
    ModuleInstanceRow,
    ModuleInstanceMetaDocumentRow,
    ModuleInstanceDataRow,
)


@pytest.fixture
async def db():  # noqa: ANN201
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: [t.__table__.create(sync, checkfirst=True) for t in _TABLES])
    session = AsyncSession(engine)
    session.add(CompanyRow(id="co_acme", name="Acme", login_slug="acme"))
    session.add(CompanyRow(id="co_globex", name="Globex", login_slug="globex"))
    await session.commit()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _new_module(
    session: AsyncSession,
    *,
    name: str = "Scoped module",
    scope: str = ModuleCompanyGrantScope.SELECTED,
) -> ModuleRow:
    row = ModuleRow(name=name, status="active", company_grant_scope=scope)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _granted_company_ids(session: AsyncSession, module_id: str) -> list[str]:
    q = await session.execute(
        select(ModuleCompanyGrantRow.company_id)
        .where(ModuleCompanyGrantRow.module_id == module_id)
        .order_by(ModuleCompanyGrantRow.company_id)
    )
    return list(q.scalars().all())


# --------------------------------------------------------------- visibility


async def test_scope_all_reaches_company_without_grant(db: AsyncSession) -> None:
    """`all` lists the module for a company that has no grant row (and never had one)."""
    row = await _new_module(db)
    company_modules = CompanyModuleService(db)

    assert await company_modules.list_for_company("co_acme") == []

    out = await ModuleService(db).update_admin(module_id=row.id, company_grant_scope="all")
    assert out["company_grant_scope"] == "all"

    # Same session, no restart: visibility is read from the row every call.
    listed = await company_modules.list_for_company("co_acme")
    assert [item["id"] for item in listed] == [row.id]

    got = await company_modules.get_for_company(company_id="co_acme", module_id=row.id)
    assert got["id"] == row.id
    # No explicit grant → the module shares the platform SoT, read via a global bind.
    assert got["bind_kind"] == "global"
    assert got["child_may_edit"] is False
    assert got["writable"] is False


async def test_scope_selected_hides_module_again(db: AsyncSession) -> None:
    row = await _new_module(db)
    modules = ModuleService(db)
    company_modules = CompanyModuleService(db)
    await modules.update_admin(module_id=row.id, company_grant_scope="all")
    assert await company_modules.list_for_company("co_acme") != []

    out = await modules.update_admin(module_id=row.id, company_grant_scope="selected")
    assert out["company_grant_scope"] == "selected"

    assert await company_modules.list_for_company("co_acme") == []
    with pytest.raises(AppError) as excinfo:
        await company_modules.get_for_company(company_id="co_acme", module_id=row.id)
    assert excinfo.value.status == 404


async def test_scope_switch_keeps_explicit_grants(db: AsyncSession) -> None:
    """`all` stops *reading* grants; it does not delete them."""
    row = await _new_module(db)
    modules = ModuleService(db)
    company_modules = CompanyModuleService(db)

    await modules.update_admin(module_id=row.id, company_ids=["co_globex"])
    assert await _granted_company_ids(db, row.id) == ["co_globex"]

    await modules.update_admin(module_id=row.id, company_grant_scope="all")
    assert await _granted_company_ids(db, row.id) == ["co_globex"]
    assert [item["id"] for item in await company_modules.list_for_company("co_acme")] == [row.id]

    await modules.update_admin(module_id=row.id, company_grant_scope="selected")
    # Back to the administrator's selection: Globex keeps the module, Acme loses it.
    assert [item["id"] for item in await company_modules.list_for_company("co_globex")] == [row.id]
    assert await company_modules.list_for_company("co_acme") == []


# ------------------------------------------------------------------ exposure


async def test_public_row_exposes_scope_in_list_and_get(db: AsyncSession) -> None:
    row = await _new_module(db)
    modules = ModuleService(db)

    listed = {item["id"]: item for item in await modules.list_all_admin()}
    assert listed[row.id]["company_grant_scope"] == ModuleCompanyGrantScope.SELECTED
    assert (await modules.get_admin(module_id=row.id))["company_grant_scope"] == "selected"

    await modules.update_admin(module_id=row.id, company_grant_scope="all")

    listed = {item["id"]: item for item in await modules.list_all_admin()}
    assert listed[row.id]["company_grant_scope"] == ModuleCompanyGrantScope.ALL
    assert (await modules.get_admin(module_id=row.id))["company_grant_scope"] == "all"


# ---------------------------------------------------------------- guard rails


async def test_invalid_scope_rejected_by_service(db: AsyncSession) -> None:
    row = await _new_module(db)
    with pytest.raises(AppError) as excinfo:
        await ModuleService(db).update_admin(module_id=row.id, company_grant_scope="everything")
    assert excinfo.value.status == 422
    assert excinfo.value.detail == "company_grant_scope must be selected or all"

    await db.refresh(row)
    assert row.company_grant_scope == ModuleCompanyGrantScope.SELECTED


def test_patch_body_accepts_only_selected_or_all() -> None:
    """422 at the edge: the pattern rejects anything else before the service runs."""
    assert PatchAdminModuleBody(company_grant_scope="all").company_grant_scope == "all"
    assert PatchAdminModuleBody(company_grant_scope="selected").company_grant_scope == "selected"
    assert PatchAdminModuleBody().company_grant_scope is None
    with pytest.raises(ValidationError):
        PatchAdminModuleBody(company_grant_scope="everything")


async def test_patch_route_forwards_company_grant_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class _SpyService:
        def __init__(self, session) -> None:  # noqa: ANN001
            self._session = session

        async def update_admin(self, **kwargs) -> dict:  # noqa: ANN003
            captured.update(kwargs)
            return {"id": kwargs["module_id"], "company_grant_scope": kwargs["company_grant_scope"]}

    monkeypatch.setattr(admin_modules, "ModuleService", _SpyService)
    body = PatchAdminModuleBody(company_grant_scope="all")

    out = await admin_modules.patch_module("mod_scoped", body, MagicMock(), AsyncMock())

    assert captured["module_id"] == "mod_scoped"
    assert captured["company_grant_scope"] == "all"
    assert out["company_grant_scope"] == "all"


async def test_delete_refused_while_all_and_allowed_after_selected(db: AsyncSession) -> None:
    row = await _new_module(db)
    modules = ModuleService(db)
    await modules.update_admin(module_id=row.id, company_grant_scope="all")

    with pytest.raises(AppError) as excinfo:
        await modules.delete_admin(module_id=row.id)
    assert excinfo.value.status == 403
    assert await db.get(ModuleRow, row.id) is not None

    await modules.update_admin(module_id=row.id, company_grant_scope="selected")
    assert await modules.delete_admin(module_id=row.id) == {"id": row.id, "deleted": True}
    assert await db.get(ModuleRow, row.id) is None
