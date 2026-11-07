from __future__ import annotations

import os
import time

import httpx

_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)


def _assert_web_shell(base: str, headers: dict[str, str], client: httpx.Client) -> None:
    """Fail if `/` is not the TypeScript SPA shell.

    `/` returning 200 says almost nothing: the Flutter web image answered 200
    too, so a deploy that silently served the old frontend would pass. The two
    bundles are distinguishable in the entry document — Vite emits
    `<div id="root">` plus a hashed `/assets/*.js`, Flutter emits
    `flutter_bootstrap.js` and `main.dart.js`.
    """
    resp = client.get(f"{base}/", headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"web shell: / -> HTTP {resp.status_code} at {base}")
    html = resp.text
    markers = {
        "root mount point": 'id="root"',
        "vite asset bundle": "/assets/",
    }
    missing = [name for name, needle in markers.items() if needle not in html]
    if missing:
        looks_flutter = "flutter" in html.lower() or "main.dart.js" in html
        hint = (
            " — this looks like the Flutter bundle; the web image still serves "
            "apps/flutter/Dockerfile output"
            if looks_flutter
            else ""
        )
        raise RuntimeError(
            f"web shell is not the TypeScript app (missing: {', '.join(missing)}){hint}"
        )
    print("web shell -> TypeScript SPA")


def smoke(
    *,
    addr: str | None = None,
    port: int | None = None,
    host_header: str = "localhost",
    attempts: int = 6,
    sleep_sec: float = 3.0,
    connect_fail_limit: int = 3,
) -> None:
    port = port or int(os.environ.get("HTTP_PORT", "8088"))
    addr = addr or os.environ.get("SMOKE_ADDR") or "127.0.0.1"
    base = f"http://{addr}:{port}"
    paths = ["/health/live", "/health/ready", "/api/v1/auth/config", "/"]
    headers = {"Host": host_header}
    timeout = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for path in paths:
            ok = False
            connect_fails = 0
            for i in range(1, attempts + 1):
                try:
                    r = client.get(f"{base}{path}", headers=headers)
                    print(f"{path} -> HTTP {r.status_code}")
                    connect_fails = 0
                    if r.status_code == 200:
                        ok = True
                        break
                except _CONNECT_ERRORS as exc:
                    connect_fails += 1
                    print(f"{path} -> error {exc}")
                    if connect_fails >= connect_fail_limit:
                        raise RuntimeError(
                            f"smoke: {base} unreachable after {connect_fails} connect errors ({exc})"
                        ) from exc
                except httpx.HTTPError as exc:
                    print(f"{path} -> error {exc}")
                print(f"  retry {i}/{attempts} in {sleep_sec}s...")
                time.sleep(sleep_sec)
            if not ok:
                raise RuntimeError(f"smoke failed for {path} at {base}")
        _assert_web_shell(base, headers, client)
    print(f"smoke OK {base} (Host: {host_header})")
