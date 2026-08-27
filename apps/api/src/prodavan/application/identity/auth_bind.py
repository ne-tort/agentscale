"""Identity — bind keycloak_sub from Auth Service registration events."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow

logger = logging.getLogger(__name__)


def parse_client_ref(client_ref: str) -> tuple[str, str] | None:
    """Return (kind, id) for ``company:<uuid>`` / ``employee:<uuid>``."""
    raw = (client_ref or "").strip()
    if ":" not in raw:
        return None
    kind, _, rest = raw.partition(":")
    kind = kind.strip().lower()
    entity_id = rest.strip()
    if kind not in ("company", "employee") or not entity_id:
        return None
    return kind, entity_id


async def apply_auth_user_registered_payload(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    client_ref = str(payload.get("client_ref") or "").strip()
    sub = str(payload.get("sub") or "").strip()
    parsed = parse_client_ref(client_ref)
    if parsed is None or not sub:
        logger.warning("auth.user.registered ignored client_ref=%s sub=%s", client_ref, bool(sub))
        return {"ok": False, "reason": "invalid_payload", "client_ref": client_ref}

    kind, entity_id = parsed
    if kind == "company":
        row = await session.get(CompanyRow, entity_id)
        if row is None:
            return {"ok": False, "reason": "company_not_found", "client_ref": client_ref}
        if row.keycloak_sub and row.keycloak_sub != sub:
            logger.warning(
                "company %s already bound to different sub (keep existing)",
                entity_id,
            )
            return {
                "ok": True,
                "bound": False,
                "reason": "already_bound",
                "client_ref": client_ref,
                "keycloak_sub": row.keycloak_sub,
            }
        row.keycloak_sub = sub
        await session.commit()
        return {"ok": True, "bound": True, "kind": "company", "id": entity_id, "keycloak_sub": sub}

    row = await session.get(EmployeeRow, entity_id)
    if row is None:
        return {"ok": False, "reason": "employee_not_found", "client_ref": client_ref}
    if row.keycloak_sub and row.keycloak_sub != sub:
        logger.warning(
            "employee %s already bound to different sub (keep existing)",
            entity_id,
        )
        return {
            "ok": True,
            "bound": False,
            "reason": "already_bound",
            "client_ref": client_ref,
            "keycloak_sub": row.keycloak_sub,
        }
    row.keycloak_sub = sub
    await session.commit()
    return {"ok": True, "bound": True, "kind": "employee", "id": entity_id, "keycloak_sub": sub}
