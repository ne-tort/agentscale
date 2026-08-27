#!/usr/bin/env python3
"""Live-stand E2E: companies, employees, content assets/aliases, ACL (in-cluster)."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt

BASE = os.environ.get("PRODAVAN_E2E_BASE", "http://prodavan-api:8000/api/v1").rstrip("/")
AUTH_SECRET = os.environ.get(
    "AUTH_TEST_SECRET", "k3s-dev-change-me-in-production-32b"
)
AUD = os.environ.get("OIDC_AUDIENCE", "prodavan-api")


def mint(sub: str, *, email: str | None = None, platform_admin: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": AUD,
        "exp": now + timedelta(hours=1),
        "iat": now,
        "platform_admin": platform_admin,
        "roles": ["platform.admin"] if platform_admin else ["employee"],
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, AUTH_SECRET, algorithm="HS256")


def hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK  {name}")
    else:
        print(f"FAIL  {name} {detail}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    run_id = uuid.uuid4().hex[:8]
    slug = f"e2e.readme.{run_id}"
    print(f"live e2e base={BASE} run={run_id}")

    with httpx.Client(timeout=30.0) as c:
        ready = c.get(f"{BASE.replace('/api/v1', '')}/health/ready")
        check("health/ready", ready.status_code == 200, ready.text)
        body = ready.json()
        check("file_store ready", body.get("checks", {}).get("file_store") == "ok", str(body))

        admin_login = c.post(f"{BASE}/auth/test/login", json={"persona": "platform_admin"})
        check("platform_admin login", admin_login.status_code == 200, admin_login.text)
        padmin_tok = admin_login.json()["access_token"]

        co_login = c.post(f"{BASE}/auth/test/login", json={"persona": "company_principal"})
        check("demo company principal", co_login.status_code == 200, co_login.text)
        company_id = co_login.json()["company_id"]

        boss_email = f"e2e-boss-{run_id}@test.local"
        worker_email = f"e2e-worker-{run_id}@test.local"
        boss_inv = c.post(
            f"{BASE}/companies/{company_id}/employees",
            headers=hdr(padmin_tok),
            json={"email": boss_email, "display_name": "E2E Boss", "role": "company_admin"},
        )
        check("invite boss", boss_inv.status_code == 201, boss_inv.text)
        boss_id = boss_inv.json()["id"]
        boss_sub = boss_inv.json()["keycloak_sub"]
        boss_tok = mint(boss_sub, email=boss_email)

        worker_inv = c.post(
            f"{BASE}/companies/{company_id}/employees",
            headers=hdr(padmin_tok),
            json={"email": worker_email, "display_name": "E2E Worker", "role": "employee"},
        )
        check("invite worker", worker_inv.status_code == 201, worker_inv.text)
        worker_id = worker_inv.json()["id"]
        worker_sub = worker_inv.json()["keycloak_sub"]
        worker_tok = mint(worker_sub, email=worker_email)

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
        check(
            "resolve alias (boss)",
            resolve_boss.status_code in (200, 302),
            resolve_boss.text[:200],
        )
        if resolve_boss.status_code == 200:
            check("resolve body", resolve_boss.content == payload, "")
        elif resolve_boss.status_code == 302:
            loc = resolve_boss.headers.get("location", "")
            check("resolve redirect", bool(loc), loc)
            dl = c.get(loc)
            check("redirect download", dl.status_code == 200 and dl.content == payload, dl.text[:100])

        resolve_worker = c.get(
            f"{BASE}/content/aliases/{slug}/resolve",
            headers=hdr(worker_tok),
            follow_redirects=False,
        )
        check(
            "resolve alias (company worker)",
            resolve_worker.status_code in (200, 302),
            resolve_worker.text[:200],
        )

        alias_acl = c.put(
            f"{BASE}/content/aliases/{alias_id}/acl",
            headers=hdr(boss_tok),
            json={
                "entries": [
                    {
                        "principal_kind": "employee",
                        "principal_id": boss_id,
                        "permission": "read",
                    }
                ]
            },
        )
        check("alias ACL replace", alias_acl.status_code == 200, alias_acl.text)

        demo = c.post(f"{BASE}/auth/test/login", json={"persona": "platform_admin"})
        check("platform admin for outsider check", demo.status_code == 200, demo.text)
        # JWT with unknown sub — no employee / company membership.
        outsider_tok = mint("test-outsider-no-membership", email=f"outsider-{run_id}@e2e.test")
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

    print("live e2e: ALL PASSED")


if __name__ == "__main__":
    main()
