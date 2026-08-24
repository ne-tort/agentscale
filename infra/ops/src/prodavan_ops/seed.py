from __future__ import annotations

import json
import os
from typing import Any

import httpx

from prodavan_ops.k8s import load_kube
from kubernetes import client
from kubernetes.stream import stream


def seed(
    *,
    namespace: str = "prodavan",
    http_port: int | None = None,
    host: str = "prodavan.local",
    skip_e2e: bool = False,
) -> None:
    """Dev seed: mint test JWTs via API pod and create company/cabinet."""
    http_port = http_port or int(os.environ.get("HTTP_PORT", "8088"))
    api_base = f"http://127.0.0.1:{http_port}/api/v1"
    admin_email = os.environ.get("SEED_ADMIN_EMAIL", "admin@prodavan.local")
    company_name = os.environ.get("SEED_COMPANY_NAME", "Dev Company")
    cabinet_name = os.environ.get("SEED_CABINET_NAME", "Dev Cabinet")

    load_kube()
    admin_token = _mint(namespace, "seed-platform-admin", admin_email, True)
    emp_token = _mint(namespace, "seed-company-admin", admin_email, False)

    with httpx.Client(timeout=30.0) as http:
        headers_emp = {
            "Host": host,
            "Authorization": f"Bearer {emp_token}",
            "Content-Type": "application/json",
        }
        headers_admin = {
            "Host": host,
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        }

        me = http.get(f"{api_base}/me", headers=headers_emp)
        me.raise_for_status()
        body = me.json()
        memberships = ((body.get("employee") or {}).get("memberships")) or []
        company_id = memberships[0]["company_id"] if memberships else ""
        if not company_id:
            r = http.post(
                f"{api_base}/companies",
                headers=headers_admin,
                json={
                    "name": company_name,
                    "admin_email": admin_email,
                    "admin_display_name": "Dev Admin",
                },
            )
            print(f"POST /companies -> {r.status_code}")
            r.raise_for_status()
            me = http.get(f"{api_base}/me", headers=headers_emp)
            me.raise_for_status()
            memberships = ((me.json().get("employee") or {}).get("memberships")) or []
            company_id = memberships[0]["company_id"] if memberships else ""
        if not company_id:
            raise RuntimeError("no company_id from /me after seed")
        print(f"company_id={company_id}")

        # AI key + cabinet — best-effort matching prior shell behaviour
        keys = http.get(f"{api_base}/companies/{company_id}/ai-keys", headers=headers_emp)
        if keys.status_code == 200 and not keys.json():
            created = http.post(
                f"{api_base}/companies/{company_id}/ai-keys",
                headers=headers_emp,
                json={"provider": "cursor_sdk", "label": "dev-fixture"},
            )
            print(f"POST ai-keys -> {created.status_code}")

        cabs = http.get(
            f"{api_base}/companies/{company_id}/cabinets", headers=headers_emp
        )
        cab_id = ""
        if cabs.status_code == 200:
            items = cabs.json() if isinstance(cabs.json(), list) else cabs.json().get("items") or []
            for item in items:
                if item.get("name") == cabinet_name:
                    cab_id = item.get("id") or ""
                    break
        if not cab_id:
            created = http.post(
                f"{api_base}/companies/{company_id}/cabinets",
                headers=headers_emp,
                json={"name": cabinet_name},
            )
            print(f"POST cabinets -> {created.status_code}")
            if created.status_code < 400:
                cab_id = (created.json() or {}).get("id") or ""

        print(f"cabinet_id={cab_id or '(none)'}")
        if skip_e2e or os.environ.get("SKIP_E2E") == "1":
            print("seed OK (skip e2e)")
            return
        print("seed OK")


def _mint(namespace: str, sub: str, email: str, platform_admin: bool) -> str:
    v1 = client.CoreV1Api()
    script = f"""
import datetime, jwt
from prodavan.config.settings import settings
now = datetime.datetime.now(datetime.UTC)
payload = {{
    "sub": {sub!r},
    "email": {email!r},
    "aud": settings.oidc_audience,
    "iat": now,
    "exp": now + datetime.timedelta(days=7),
    "platform_admin": {str(platform_admin)},
    "roles": ["platform.admin"] if {str(platform_admin)} else [],
}}
print(jwt.encode(payload, settings.auth_test_secret, algorithm="HS256"))
"""
    resp = stream(
        v1.connect_get_namespaced_pod_exec,
        name=_api_pod_name(namespace),
        namespace=namespace,
        command=["python", "-c", script],
        container="api",
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )
    token = (resp or "").strip().splitlines()[-1].strip()
    if not token:
        raise RuntimeError("failed to mint JWT via API pod")
    return token


def _api_pod_name(namespace: str) -> str:
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(
        namespace, label_selector="app=prodavan-api"
    )
    for p in pods.items:
        phase = (p.status.phase if p.status else None) or ""
        if phase == "Running":
            return p.metadata.name
    raise RuntimeError("no running prodavan-api pod")
