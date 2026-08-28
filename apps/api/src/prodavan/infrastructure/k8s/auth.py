"""In-cluster Kubernetes API authentication (SA token + CA)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class InClusterAuth:
    """Bearer token auth for the pod ServiceAccount."""

    def __init__(
        self,
        *,
        token_dir: Path | None = None,
        host: str | None = None,
        port: str | None = None,
    ) -> None:
        self._token_dir = token_dir or SA_DIR
        self._host = host if host is not None else os.environ.get("KUBERNETES_SERVICE_HOST")
        self._port = port if port is not None else os.environ.get("KUBERNETES_SERVICE_PORT", "443")

    @property
    def host(self) -> str | None:
        return self._host

    @property
    def port(self) -> str:
        return self._port or "443"

    def available(self) -> bool:
        return bool(self._host) and (self._token_dir / "token").is_file()

    def headers(self) -> dict[str, str]:
        token = (self._token_dir / "token").read_text(encoding="utf-8").strip()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def client_kwargs(self) -> dict[str, Any]:
        ca = self._token_dir / "ca.crt"
        return {"verify": str(ca) if ca.is_file() else False, "timeout": 30.0}

    def api_base(self) -> str:
        if not self._host:
            raise RuntimeError("kubernetes service host is not configured")
        return f"https://{self._host}:{self.port}"
