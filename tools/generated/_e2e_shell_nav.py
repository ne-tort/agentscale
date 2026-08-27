#!/usr/bin/env python3
"""Live e2e: seed modules expose admin shell nav in tabs meta."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8088/api/v1"
FAILS = 0

EXPECTED = {
    "mod_example_suppliers": "Поставщики",
    "mod_example_notes": "Заметки",
    "mod_example_hub": "Hub меню",
}


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
    print("=== shell nav e2e ===")

    code, login, raw = req(
        "POST", f"{BASE}/auth/login", data={"username": "admin", "password": "admin"}
    )
    ok("admin_login", code == 200 and bool(login.get("access_token")), raw)
    h = {"Authorization": f"Bearer {login.get('access_token') or ''}"}

    code, mods, raw = req("GET", f"{BASE}/admin/modules", headers=h)
    ok("list_modules", code == 200, raw)
    by_id = {m.get("id"): m for m in mods.get("items") or []}

    for module_id, title in EXPECTED.items():
        if module_id not in by_id:
            ok(f"module_{module_id}", False, "missing from catalog")
            continue
        code, doc, raw = req(
            "GET",
            f"{BASE}/admin/modules/{module_id}/meta/documents/tabs",
            headers=h,
        )
        ok(f"tabs_{module_id}", code == 200, raw)
        body = doc.get("body")
        shell = [
            t
            for t in (body or [])
            if isinstance(t, dict)
            and (t.get("nav") or {}).get("contour") == "admin"
            and t.get("enabled", True) is not False
        ]
        ok(
            f"shell_nav_{module_id}",
            any(t.get("title") == title for t in shell),
            str(body)[:300],
        )

    print(f"=== done fails={FAILS} ===")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
