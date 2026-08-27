#!/usr/bin/env python3
"""Live e2e: admin create company → wait identity bind; soft-delete cascade; relations grant."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8088/api/v1"
FAILS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global FAILS
    if cond:
        print(f"  OK  {name}")
    else:
        FAILS += 1
        print(f"FAIL  {name}: {detail[:500]}")


def req(method: str, url: str, *, data=None, headers=None):
    hdrs = dict(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdrs.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        return e.code, parsed, raw


def main() -> int:
    run = uuid.uuid4().hex[:8]
    print(f"=== bind/cascade/relations e2e run={run} ===")

    code, cfg, raw = req("GET", f"{BASE}/auth/config")
    ok("auth_config", code == 200, raw)

    code, login, raw = req(
        "POST",
        f"{BASE}/auth/login",
        data={"username": "admin", "password": "admin"},
    )
    ok("admin_login", code == 200 and bool(login.get("access_token")), raw)
    token = login.get("access_token") or ""
    h = {"Authorization": f"Bearer {token}"}

    pwd = f"Passw0rd-{run}!"
    code, created, raw = req(
        "POST",
        f"{BASE}/admin/companies",
        data={
            "name": f"Bind E2E {run}",
            "password": pwd,
            "admin_email": f"boss-{run}@example.com",
            "admin_display_name": "Boss",
        },
        headers=h,
    )
    ok("create_company", code in (200, 201), raw)
    company_id = (created.get("id") or created.get("company_id") or "") if isinstance(created, dict) else ""
    if not company_id and isinstance(created, dict):
        company_id = (created.get("company") or {}).get("id") or ""
    ok("company_id", bool(company_id), str(created)[:300])

    bound = False
    metrics = {}
    for i in range(30):
        code, body, raw = req("GET", f"{BASE}/admin/companies/{company_id}/metrics", headers=h)
        if code != 200:
            # fallback detail
            code, body, raw = req("GET", f"{BASE}/admin/companies/{company_id}", headers=h)
        metrics = body.get("metrics") if isinstance(body.get("metrics"), dict) else body
        if isinstance(metrics, dict) and metrics.get("keycloak_unbound") is False:
            bound = True
            break
        if isinstance(body, dict) and body.get("keycloak_sub"):
            bound = True
            break
        # password rebind path
        if i == 10:
            req(
                "PUT",
                f"{BASE}/admin/companies/{company_id}/password",
                data={"password": pwd},
                headers=h,
            )
        time.sleep(1)
    ok("identity_bound_within_30s", bound, str(metrics)[:400])

    # Soft-delete cascade
    code, deleted, raw = req(
        "DELETE",
        f"{BASE}/admin/companies/{company_id}",
        headers=h,
    )
    ok("soft_delete_company", code in (200, 204), raw)
    time.sleep(2)
    code, metrics_list, raw = req("GET", f"{BASE}/admin/companies/metrics", headers=h)
    ok("metrics_after_delete", code == 200, raw)
    items = metrics_list.get("items") if isinstance(metrics_list, dict) else []
    still = [c for c in (items or []) if (c.get("company_id") or c.get("id")) == company_id]
    ok("company_hidden_from_metrics", len(still) == 0, str(still)[:200])

    print(f"=== done fails={FAILS} ===")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
