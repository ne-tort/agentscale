#!/usr/bin/env python3
"""Live e2e: cabinets/modules create, bidirectional bind, empty company unbind, cascade soft-delete."""
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
    print(f"=== modules/cabinets e2e run={run} ===")

    code, login, raw = req(
        "POST", f"{BASE}/auth/login", data={"username": "admin", "password": "admin"}
    )
    ok("admin_login", code == 200 and bool(login.get("access_token")), raw)
    h = {"Authorization": f"Bearer {login.get('access_token') or ''}"}

    # Company (for later grant)
    code, co, raw = req(
        "POST",
        f"{BASE}/admin/companies",
        data={"name": f"BindCo {run}", "password": f"Passw0rd-{run}!"},
        headers=h,
    )
    ok("create_company", code in (200, 201), raw)
    company_id = co.get("id") or (co.get("company") or {}).get("id") or ""

    # Cabinet without companies
    code, cab, raw = req(
        "POST",
        f"{BASE}/admin/cabinets",
        data={"name": f"Cab {run}"},
        headers=h,
    )
    ok("create_cabinet_no_company", code in (200, 201), raw)
    cabinet_id = cab.get("id") or ""
    ok("cabinet_empty_companies", cab.get("company_ids") in ([], None) or cab.get("company_ids") == [], str(cab)[:300])

    # Module
    code, mod, raw = req(
        "POST",
        f"{BASE}/admin/modules",
        data={"name": f"Mod {run}"},
        headers=h,
    )
    ok("create_module", code in (200, 201), raw)
    module_id = mod.get("id") or ""

    # Bind module→cabinet
    code, mod2, raw = req(
        "PATCH",
        f"{BASE}/admin/modules/{module_id}",
        data={"cabinet_ids": [cabinet_id]},
        headers=h,
    )
    ok("module_bind_cabinet", code == 200 and cabinet_id in (mod2.get("cabinet_ids") or []), raw)

    # Mirror on cabinet
    code, cab2, raw = req("GET", f"{BASE}/admin/cabinets/{cabinet_id}", headers=h)
    ok(
        "cabinet_lists_module",
        code == 200 and module_id in (cab2.get("module_ids") or []),
        raw,
    )

    # Bind company then unbind all
    code, cab3, raw = req(
        "PATCH",
        f"{BASE}/admin/cabinets/{cabinet_id}",
        data={"company_ids": [company_id]},
        headers=h,
    )
    ok("cabinet_bind_company", code == 200 and company_id in (cab3.get("company_ids") or []), raw)

    code, cab4, raw = req(
        "PATCH",
        f"{BASE}/admin/cabinets/{cabinet_id}",
        data={"company_ids": []},
        headers=h,
    )
    ok(
        "cabinet_unbind_all_companies",
        code == 200 and (cab4.get("company_ids") or []) == [],
        raw,
    )

    # Soft-delete company cascade smoke
    code, _, raw = req("DELETE", f"{BASE}/admin/companies/{company_id}", headers=h)
    ok("soft_delete_company", code in (200, 204), raw)
    time.sleep(1)

    # Cleanup module/cabinet
    req("DELETE", f"{BASE}/admin/modules/{module_id}", headers=h)
    req("DELETE", f"{BASE}/admin/cabinets/{cabinet_id}", headers=h)

    print(f"=== done fails={FAILS} ===")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
