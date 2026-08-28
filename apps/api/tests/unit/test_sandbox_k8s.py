"""I8 sandbox Job contract — not on the project create path."""

from __future__ import annotations

from pathlib import Path

import pytest

from prodavan.application.projects.sandbox_k8s import run_pvc_probe_job, sandbox_k8s_status
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.k8s.sandbox_jobs import (
    SANDBOX_PROBE_JOB_NAME,
    InClusterJobApi,
    pvc_probe_job_body,
)


def test_pvc_probe_job_mounts_api_pvc() -> None:
    body = pvc_probe_job_body(
        namespace="prodavan",
        image="ghcr.io/ne-tort/prodavan-api:local",
        pvc_name="prodavan-api-storage",
    )
    spec = body["spec"]["template"]["spec"]
    assert spec["serviceAccountName"] == "prodavan-sandbox"
    assert spec["volumes"][0]["persistentVolumeClaim"]["claimName"] == "prodavan-api-storage"
    assert spec["containers"][0]["volumeMounts"][0]["readOnly"] is True
    assert spec["containers"][0]["volumeMounts"][0]["mountPath"] == "/data/storage"
    assert body["metadata"]["name"] == SANDBOX_PROBE_JOB_NAME
    assert spec["containers"][0]["command"][2].startswith("import os")


def test_in_cluster_available_requires_token(tmp_path: Path) -> None:
    from prodavan.infrastructure.k8s.auth import InClusterAuth

    api = InClusterJobApi(auth=InClusterAuth(token_dir=tmp_path, host="10.0.0.1", port="443"))
    assert api.available() is False
    (tmp_path / "token").write_text("tok", encoding="utf-8")
    assert api.available() is True


def test_sandbox_status_reports_object_ws_create_path() -> None:
    from prodavan.infrastructure.k8s.auth import InClusterAuth

    status = sandbox_k8s_status(api=InClusterJobApi(auth=InClusterAuth(token_dir=Path("/nope"), host=None)))
    assert status["create_path"] == "object-ws"
    assert status["isolator"] == "not_wired"
    assert status["enabled"] is False


@pytest.mark.asyncio
async def test_run_pvc_probe_disabled() -> None:
    assert settings.sandbox_k8s_jobs is False
    with pytest.raises(AppError) as exc:
        await run_pvc_probe_job()
    assert exc.value.code == "SANDBOX_K8S_DISABLED"
    assert exc.value.status == 409
