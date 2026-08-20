"""Current user introspection."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.deps import CurrentUser, get_current_user, get_session
from prodavan.application.dto.auth import MeResponse, TenantResponse, UserResponse
from prodavan.infrastructure.persistence.models.tenants import Tenant, User
from prodavan.infrastructure.persistence.rls import apply_rls

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=MeResponse)
async def get_me(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    await apply_rls(
        session,
        user_id=current.user_id,
        tenant_id=current.tenant_id,
    )
    user_result = await session.execute(select(User).where(User.id == current.user_id))
    user = user_result.scalar_one()
    tenant_result = await session.execute(select(Tenant).where(Tenant.id == current.tenant_id))
    tenant = tenant_result.scalar_one()
    return MeResponse(
        user=UserResponse(id=user.id, email=user.email, display_name=user.display_name),
        tenant=TenantResponse(id=tenant.id, slug=tenant.slug, display_name=tenant.display_name),
        cabinet_ids=current.cabinet_ids,
    )
