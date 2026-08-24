from __future__ import annotations

import os
import time

import httpx


def smoke(
    *,
    addr: str | None = None,
    port: int | None = None,
    host_header: str = "prodavan.local",
    attempts: int = 24,
    sleep_sec: float = 5.0,
) -> None:
    port = port or int(os.environ.get("HTTP_PORT", "8088"))
    addr = addr or os.environ.get("SMOKE_ADDR") or "127.0.0.1"
    base = f"http://{addr}:{port}"
    paths = ["/health/live", "/health/ready", "/api/v1/auth/config", "/"]
    headers = {"Host": host_header}
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        for path in paths:
            ok = False
            for i in range(1, attempts + 1):
                try:
                    r = client.get(f"{base}{path}", headers=headers)
                    print(f"{path} -> HTTP {r.status_code}")
                    if r.status_code == 200:
                        ok = True
                        break
                except httpx.HTTPError as exc:
                    print(f"{path} -> error {exc}")
                print(f"  retry {i}/{attempts} in {sleep_sec}s...")
                time.sleep(sleep_sec)
            if not ok:
                raise RuntimeError(f"smoke failed for {path} at {base}")
    print(f"smoke OK {base} (Host: {host_header})")
