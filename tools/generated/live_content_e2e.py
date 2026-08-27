#!/usr/bin/env python3
"""Live-stand E2E: Keycloak ROPC + companies/employees + content ACL."""

from __future__ import annotations

import os
import sys
import uuid

import httpx

BASE = os.environ.get("PRODAVAN_E2E_BASE", "http://prodavan-api:8000/api/v1").rstrip("/")
KC = os.environ.get(
    "PRODAVAN_E2E_KC", "http://prodavan-keycloak:8080"
).rstrip("/")
CLIENT_ID = os.environ.get("OIDC_FLUTTER_CLIENT_ID", "prodavan-flutter")
SERVICES_ID = os.environ.get("KEYCLOAK_ADMIN_CLIENT_ID", "prodavan-services")
SERVICES_SECRET = os.environ.get(
    "KEYCLOAK_ADMIN_CLIENT_SECRET", "change-me-prodavan-services"
)


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK  {name}")
    else:
        print(f"FAIL  {name} {detail}", file=sys.stderr)
        sys.exit(1)


def hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def ropc(c: httpx.Client, username: str, password: str) -> str:
    r = c.post(
        f"{KC}/realms/prodavan/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "username": username,
            "password": password,
            "scope": "openid offline_access",
        },
    )
    check(f"ROPC {username}", r.status_code == 200, r.text[:300])
    return r.json()["access_token"]


def admin_api_token(c: httpx.Client) -> str:
    r = c.post(
        f"{KC}/realms/prodavan/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": SERVICES_ID,
            "client_secret": SERVICES_SECRET,
        },
    )
    check("KC client_credentials", r.status_code == 200, r.text[:300])
    return r.json()["access_token"]


def set_password(c: httpx.Client, *, admin_tok: str, user_id: str, password: str) -> None:
    r = c.put(
        f"{KC}/admin/realms/prodavan/users/{user_id}/reset-password",
        headers=hdr(admin_tok),
        json={"type": "password", "value": password, "temporary": False},
    )
    check(f"set password {user_id[:12]}", r.status_code in (204, 200), r.text[:200])
    # Clear required actions + mark verified so ROPC is not blocked.
    u = c.get(f"{KC}/admin/realms/prodavan/users/{user_id}", headers=hdr(admin_tok))
    check(f"get user {user_id[:12]}", u.status_code == 200, u.text[:200])
    body = u.json()
    body["emailVerified"] = True
    body["requiredActions"] = []
    if not body.get("lastName"):
        body["lastName"] = "User"
    if not body.get("firstName"):
        body["firstName"] = (body.get("username") or "User")[:50]
    if not body.get("email") and "@" in str(body.get("username") or ""):
        body["email"] = body["username"]
    p = c.put(
        f"{KC}/admin/realms/prodavan/users/{user_id}",
        headers=hdr(admin_tok),
        json=body,
    )
    check(f"profile ready {user_id[:12]}", p.status_code in (204, 200), p.text[:200])


def main() -> None:
    run_id = uuid.uuid4().hex[:8]
    slug = f"e2e.readme.{run_id}"
    print(f"live e2e base={BASE} kc={KC} run={run_id}")

    with httpx.Client(timeout=45.0) as c:
        ready = c.get(f"{BASE.replace('/api/v1', '')}/health/ready")
        check("health/ready", ready.status_code == 200, ready.text)
        body = ready.json()
        check("file_store ready", body.get("checks", {}).get("file_store") == "ok", str(body))

        cfg = c.get(f"{BASE}/auth/config")
        check("auth/config oidc", cfg.status_code == 200 and cfg.json().get("auth_mode") == "oidc", cfg.text)

        padmin_tok = ropc(c, "admin", "admin")
        me = c.get(f"{BASE}/me", headers=hdr(padmin_tok))
        check("admin /me", me.status_code == 200 and "platform_admin" in me.json().get("contours", []), me.text)

        co = c.post(
            f"{BASE}/companies",
            headers=hdr(padmin_tok),
            json={
                "name": f"E2E Content {run_id}",
                "password": "e2e-company-pass",
                "admin_email": f"boss-{run_id}@e2e.test",
            },
        )
        check("create company", co.status_code == 201, co.text)
        company_id = co.json()["company"]["id"]
        boss_id = co.json()["admin_employee"]["id"]
        boss_sub = co.json()["admin_employee"]["keycloak_sub"]
        boss_email = co.json()["admin_employee"]["email"]

        kc_admin = admin_api_token(c)
        set_password(c, admin_tok=kc_admin, user_id=boss_sub, password="e2e-boss-pass")
        boss_tok = ropc(c, boss_email, "e2e-boss-pass")

        worker_email = f"worker-{run_id}@e2e.test"
        inv = c.post(
            f"{BASE}/companies/{company_id}/employees",
            headers=hdr(boss_tok),
            json={"email": worker_email, "display_name": "Worker", "role": "member"},
        )
        check("invite worker", inv.status_code == 201, inv.text)
        worker_id = inv.json()["id"]
        worker_sub = inv.json()["keycloak_sub"]
        set_password(c, admin_tok=kc_admin, user_id=worker_sub, password="e2e-worker-pass")
        worker_tok = ropc(c, worker_email, "e2e-worker-pass")

        asset = c.post(
            f"{BASE}/content/assets",
            headers=hdr(boss_tok),
            json={
                "owner_company_id": company_id,
                "title": "secret.txt",
                "mime": "text/plain",
                "visibility": "private",
            },
        )
        check("create private asset", asset.status_code == 200, asset.text)
        asset_id = asset.json()["id"]

        ver = c.post(
            f"{BASE}/content/assets/{asset_id}/versions",
            headers=hdr(boss_tok),
            json={"mime": "text/plain"},
        )
        check("begin version", ver.status_code == 200, ver.text)
        version_id = ver.json()["version"]["id"]
        upload_url = ver.json()["upload_url"]
        payload = b"# e2e live content\n"

        up = c.put(upload_url, content=payload, headers={"Content-Type": "text/plain"})
        check("s3 presigned PUT", up.status_code in (200, 204), up.text)

        fin = c.post(
            f"{BASE}/content/assets/{asset_id}/versions/{version_id}/finalize",
            headers=hdr(boss_tok),
        )
        check("finalize version", fin.status_code == 200, fin.text)

        denied = c.get(f"{BASE}/content/assets/{asset_id}", headers=hdr(worker_tok))
        check("private asset denied for coworker", denied.status_code == 403, denied.text)

        acl = c.put(
            f"{BASE}/content/assets/{asset_id}/acl",
            headers=hdr(boss_tok),
            json={
                "entries": [
                    {
                        "principal_kind": "employee",
                        "principal_id": worker_id,
                        "permission": "read",
                    }
                ]
            },
        )
        check("grant ACL read to worker", acl.status_code == 200, acl.text)

        allowed = c.get(f"{BASE}/content/assets/{asset_id}", headers=hdr(worker_tok))
        check("worker reads after ACL", allowed.status_code == 200, allowed.text)

        alias = c.post(
            f"{BASE}/content/aliases",
            headers=hdr(boss_tok),
            json={
                "slug": slug,
                "owner_company_id": company_id,
                "label": "E2E readme",
                "visibility": "company",
            },
        )
        check("create alias", alias.status_code == 200, alias.text)
        alias_id = alias.json()["id"]

        bind = c.post(
            f"{BASE}/content/aliases/{alias_id}/bind",
            headers=hdr(boss_tok),
            json={"asset_id": asset_id, "blob_version_id": version_id},
        )
        check("bind alias", bind.status_code == 200, bind.text)

        resolve_boss = c.get(
            f"{BASE}/content/aliases/{slug}/resolve",
            headers=hdr(boss_tok),
            follow_redirects=False,
        )
        check("resolve alias (boss)", resolve_boss.status_code in (200, 302), resolve_boss.text[:200])

        # Outsider: company principal of another org via fresh company
        co2 = c.post(
            f"{BASE}/companies",
            headers=hdr(padmin_tok),
            json={"name": f"E2E Other {run_id}", "password": "other-company-pass"},
        )
        check("create other company", co2.status_code == 201, co2.text)
        other_id = co2.json()["company"]["id"]
        outsider_tok = ropc(c, other_id, "other-company-pass")
        outsider_resolve = c.get(
            f"{BASE}/content/aliases/{slug}/resolve",
            headers=hdr(outsider_tok),
            follow_redirects=False,
        )
        check(
            "alias resolve denied for other company",
            outsider_resolve.status_code == 403,
            outsider_resolve.text[:200],
        )

        # Company principal login route smoke
        company_tok = ropc(c, company_id, "e2e-company-pass")
        co_me = c.get(f"{BASE}/me", headers=hdr(company_tok))
        check(
            "company principal /me",
            co_me.status_code == 200 and "company" in co_me.json().get("contours", []),
            co_me.text,
        )

    print("live e2e: ALL PASSED")


if __name__ == "__main__":
    main()
