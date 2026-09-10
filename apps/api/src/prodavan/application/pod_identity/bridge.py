"""Pod Identity Bridge — scoped JWT for Project Pods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError

POD_BRIDGE_AUD = "prodavan-pod-bridge"
POD_BRIDGE_TYP = "pod_bridge"

SCOPE_AGENT_EVENTS = "agent:events"
SCOPE_INTERNAL_CREDENTIALS = "internal:credentials"
SCOPE_INTERNAL_HYDRATE = "internal:hydrate"
SCOPE_INFRA_CACHE = "infra:cache"
SCOPE_INFRA_DOCS = "infra:docs"
SCOPE_INFRA_USERDB = "infra:userdb"
SCOPE_INFRA_EVENTS = "infra:events"
SCOPE_INFRA_OBJECTS = "infra:objects"


def module_rows_scope(module_id: str) -> str:
    return f"module:{module_id}:rows"


def module_actions_scope(module_id: str) -> str:
    return f"module:{module_id}:actions"


def default_platform_scopes() -> list[str]:
    return [
        SCOPE_AGENT_EVENTS,
        SCOPE_INTERNAL_CREDENTIALS,
        SCOPE_INTERNAL_HYDRATE,
        SCOPE_INFRA_CACHE,
        SCOPE_INFRA_DOCS,
        SCOPE_INFRA_USERDB,
        SCOPE_INFRA_EVENTS,
        SCOPE_INFRA_OBJECTS,
    ]


def build_launch_scopes(module_ids: list[str]) -> list[str]:
    scopes = list(default_platform_scopes())
    for mid in module_ids:
        mid_s = (mid or "").strip()
        if not mid_s:
            continue
        scopes.append(module_rows_scope(mid_s))
        scopes.append(module_actions_scope(mid_s))
    # stable unique order
    seen: set[str] = set()
    out: list[str] = []
    for s in scopes:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


@dataclass(frozen=True, slots=True)
class PodBridgeClaims:
    project_id: str
    cabinet_id: str
    company_id: str
    pod_id: str
    gen: int
    scopes: tuple[str, ...]
    jti: str
    exp: int
    acting_employee_id: str | None = None
    session_id: str | None = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def require_scope(self, scope: str) -> None:
        if not self.has_scope(scope):
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail=f"pod bridge missing scope {scope}",
            )

    def require_project(self, project_id: str) -> None:
        if self.project_id != project_id:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="pod bridge project_id mismatch",
            )


def _signing_secret() -> str:
    secret = (settings.pod_identity_bridge_secret or "").strip()
    if secret:
        return secret
    # Fallback so existing clusters work before GitOps secret lands.
    fallback = (settings.pod_agent_bridge_auth_token or "").strip()
    if fallback:
        return fallback
    return (settings.auth_test_secret or "").strip() or "dev-pod-bridge-secret"


_GEN_FALLBACK: dict[str, int] = {}


def _gen_key(pod_id: str) -> str:
    from prodavan.core.infra.cache import cache_key

    return cache_key("pod_bridge", "gen", pod_id)


async def get_pod_bridge_generation(pod_id: str) -> int:
    from prodavan.core.infra.cache import cache_get

    raw = await cache_get(_gen_key(pod_id))
    if raw is None:
        return int(_GEN_FALLBACK.get(pod_id, 0))
    try:
        return int(raw)
    except ValueError:
        return int(_GEN_FALLBACK.get(pod_id, 0))


async def bump_pod_bridge_generation(pod_id: str) -> int:
    """Invalidate all JWTs for this pod (pause/stop/delete/reload)."""
    from prodavan.core.infra.cache import cache_set

    current = await get_pod_bridge_generation(pod_id)
    nxt = current + 1
    _GEN_FALLBACK[pod_id] = nxt
    # Long TTL — generations are monotonic counters, not session data.
    await cache_set(_gen_key(pod_id), str(nxt), ttl_sec=60 * 60 * 24 * 30)
    return nxt


async def mint_pod_bridge_token(
    *,
    project_id: str,
    cabinet_id: str,
    company_id: str,
    pod_id: str,
    scopes: list[str],
    acting_employee_id: str | None = None,
    session_id: str | None = None,
    ttl_seconds: int | None = None,
) -> tuple[str, PodBridgeClaims]:
    ttl = int(ttl_seconds if ttl_seconds is not None else settings.pod_identity_bridge_ttl_seconds)
    ttl = max(60, ttl)
    gen = await get_pod_bridge_generation(pod_id)
    now = datetime.now(UTC)
    jti = uuid4().hex
    exp_dt = now + timedelta(seconds=ttl)
    payload: dict[str, Any] = {
        "typ": POD_BRIDGE_TYP,
        "aud": POD_BRIDGE_AUD,
        "sub": f"pod:{pod_id}",
        "project_id": project_id,
        "cabinet_id": cabinet_id,
        "company_id": company_id,
        "pod_id": pod_id,
        "gen": gen,
        "scopes": list(scopes),
        "jti": jti,
        "iat": now,
        "exp": exp_dt,
    }
    if acting_employee_id:
        payload["acting_employee_id"] = acting_employee_id
    if session_id:
        payload["session_id"] = session_id
    token = jwt.encode(payload, _signing_secret(), algorithm="HS256")
    claims = PodBridgeClaims(
        project_id=project_id,
        cabinet_id=cabinet_id,
        company_id=company_id,
        pod_id=pod_id,
        gen=gen,
        scopes=tuple(scopes),
        jti=jti,
        exp=int(exp_dt.timestamp()),
        acting_employee_id=acting_employee_id,
        session_id=session_id,
    )
    return token, claims


def peek_pod_bridge_token(token: str) -> dict[str, Any] | None:
    """Decode without verify — used only to detect typ=pod_bridge for middleware."""
    try:
        return jwt.decode(
            token,
            options={"verify_signature": False, "verify_aud": False, "verify_exp": False},
        )
    except Exception:
        return None


async def verify_pod_bridge_token(token: str) -> PodBridgeClaims:
    secret = _signing_secret()
    if not secret:
        raise AppError(
            code="UNAUTHORIZED",
            title="Unauthorized",
            status=401,
            detail="pod bridge not configured",
        )
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=POD_BRIDGE_AUD,
            options={"require": ["exp", "jti", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppError(
            code="UNAUTHORIZED",
            title="Unauthorized",
            status=401,
            detail="pod bridge token expired",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise AppError(
            code="UNAUTHORIZED",
            title="Unauthorized",
            status=401,
            detail="invalid pod bridge token",
        ) from exc

    if payload.get("typ") != POD_BRIDGE_TYP:
        raise AppError(
            code="UNAUTHORIZED",
            title="Unauthorized",
            status=401,
            detail="not a pod bridge token",
        )

    project_id = str(payload.get("project_id") or "").strip()
    cabinet_id = str(payload.get("cabinet_id") or "").strip()
    company_id = str(payload.get("company_id") or "").strip()
    pod_id = str(payload.get("pod_id") or "").strip()
    if not project_id or not cabinet_id or not company_id or not pod_id:
        raise AppError(
            code="UNAUTHORIZED",
            title="Unauthorized",
            status=401,
            detail="pod bridge claims incomplete",
        )

    try:
        gen = int(payload.get("gen", 0))
    except (TypeError, ValueError):
        gen = -1
    current_gen = await get_pod_bridge_generation(pod_id)
    if gen != current_gen:
        raise AppError(
            code="UNAUTHORIZED",
            title="Unauthorized",
            status=401,
            detail="pod bridge token revoked",
        )

    scopes_raw = payload.get("scopes") or []
    if not isinstance(scopes_raw, list):
        scopes_raw = []
    scopes = tuple(str(s) for s in scopes_raw if str(s).strip())
    exp_val = payload.get("exp")
    exp_i = int(exp_val) if exp_val is not None else 0
    return PodBridgeClaims(
        project_id=project_id,
        cabinet_id=cabinet_id,
        company_id=company_id,
        pod_id=pod_id,
        gen=gen,
        scopes=scopes,
        jti=str(payload.get("jti") or ""),
        exp=exp_i,
        acting_employee_id=(str(payload["acting_employee_id"]) if payload.get("acting_employee_id") else None),
        session_id=(str(payload["session_id"]) if payload.get("session_id") else None),
    )
