"""The dev HTTPS router must give `/api` a strictly higher Traefik priority than `/`.

Traefik turns every path of an Ingress into its own router, and
`traefik.ingress.kubernetes.io/router.priority` is an annotation on the Ingress —
not on a path. So a single object carrying `/api`, `/health` and `/` gave all
three the same priority. With `/` tying `/api`, requests for the API were
answered by the web container, which serves `index.html` for any path it does
not know: the site loaded, `/health` was green, and every login POST returned
HTML. This happened on https://agentscale.dev and the public smoke check caught
it, but only after a deploy.

The overlay therefore renders two Ingresses. These tests pin the arrangement so
the tie cannot come back.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEV_OVERLAY = REPO_ROOT / "infra" / "k3s" / "overlays" / "dev"
API_INGRESS = DEV_OVERLAY / "ingress-https-api.yaml"
WEB_INGRESS = DEV_OVERLAY / "ingress-https-web.yaml"


def _priority(doc: dict) -> int:
    raw = doc["metadata"].get("annotations", {}).get(
        "traefik.ingress.kubernetes.io/router.priority"
    )
    assert raw is not None, f"{doc['metadata']['name']} has no router priority"
    return int(raw)


def _paths(doc: dict) -> set[str]:
    return {
        path["path"]
        for rule in doc["spec"]["rules"]
        for path in rule["http"]["paths"]
    }


def _load(path: Path) -> dict:
    docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]
    assert len(docs) == 1, f"{path.name} should hold exactly one Ingress"
    return docs[0]


def test_api_ingress_outranks_the_spa() -> None:
    api = _load(API_INGRESS)
    web = _load(WEB_INGRESS)

    assert _paths(api) == {"/api", "/health"}
    assert _paths(web) == {"/"}
    assert _priority(api) > _priority(web), (
        "a / request must never be able to reach nginx ahead of /api"
    )


def test_both_ingresses_are_pinned_to_the_dev_names() -> None:
    for path in (API_INGRESS, WEB_INGRESS):
        doc = _load(path)
        hosts = {rule["host"] for rule in doc["spec"]["rules"]}
        assert hosts == {"agentscale.dev", "localhost"}, path.name
        tls_hosts = {host for entry in doc["spec"]["tls"] for host in entry["hosts"]}
        assert tls_hosts == hosts, f"{path.name} serves TLS for different hosts"
        # The base object carried the entrypoint and TLS annotations; a split-out
        # Ingress has to state them itself or it lands on the wrong entrypoint.
        annotations = doc["metadata"]["annotations"]
        assert annotations.get(
            "traefik.ingress.kubernetes.io/router.entrypoints"
        ) == "websecure", path.name
        assert annotations.get("traefik.ingress.kubernetes.io/router.tls") == "true"
        assert doc["spec"].get("ingressClassName") == "traefik"


def test_api_ingress_points_at_the_api_service() -> None:
    doc = _load(API_INGRESS)
    for rule in doc["spec"]["rules"]:
        for path in rule["http"]["paths"]:
            assert path["backend"]["service"]["name"] == "agentscale-api"
            assert path["backend"]["service"]["port"]["number"] == 8000


def test_web_ingress_points_at_the_web_service() -> None:
    doc = _load(WEB_INGRESS)
    for rule in doc["spec"]["rules"]:
        for path in rule["http"]["paths"]:
            assert path["backend"]["service"]["name"] == "agentscale-web"


def test_dev_overlay_drops_the_wildcard_https_ingress() -> None:
    """Otherwise dev would answer on :443 for any Host, which belongs to prod."""
    patch = (DEV_OVERLAY / "patch-dev-ingress.yaml").read_text(encoding="utf-8")
    assert "name: agentscale-https" in patch
    assert "$patch: delete" in patch

    kustomization = (DEV_OVERLAY / "kustomization.yaml").read_text(encoding="utf-8")
    assert "- ingress-https-api.yaml" in kustomization
    assert "- ingress-https-web.yaml" in kustomization


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl is not installed")
def test_rendered_dev_overlay_has_no_priority_tie() -> None:
    rendered = subprocess.run(
        ["kubectl", "kustomize", str(DEV_OVERLAY)],
        capture_output=True,
        text=True,
        # kustomize выводит комментарии/аннотации манифестов как есть; в них
        # есть кириллица. Без явной кодировки Windows использует cp1251 и
        # subprocess падает с UnicodeDecodeError в reader-потоке.
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert rendered.returncode == 0, rendered.stderr

    ingresses = [
        doc
        for doc in yaml.safe_load_all(rendered.stdout)
        if doc and doc.get("kind") == "Ingress"
    ]
    by_name = {doc["metadata"]["name"]: doc for doc in ingresses}
    assert "agentscale-https" not in by_name, "the wildcard HTTPS object survived"
    assert {"agentscale-https-api", "agentscale-https-web"} <= set(by_name)

    api, web = by_name["agentscale-https-api"], by_name["agentscale-https-web"]
    assert _priority(api) > _priority(web)
    assert _paths(api).isdisjoint(_paths(web))
