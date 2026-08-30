"""Kubernetes Pod exec via WebSocket (remotecommand stream protocol)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from prodavan.infrastructure.k8s.auth import InClusterAuth

logger = logging.getLogger(__name__)

_CHANNEL_STDOUT = 1
_CHANNEL_STDERR = 2
_CHANNEL_ERROR = 3


@dataclass(frozen=True, slots=True)
class ExecResult:
    stdout: bytes
    stderr: bytes
    exit_code: int | None


def _build_exec_url(
    *,
    auth: InClusterAuth,
    namespace: str,
    pod_name: str,
    command: list[str],
    container: str,
) -> str:
    base = auth.api_base().replace("https://", "wss://").replace("http://", "ws://")
    query = urlencode(
        [
            ("container", container),
            ("stdin", "false"),
            ("stdout", "true"),
            ("stderr", "true"),
            ("tty", "false"),
            *(("command", part) for part in command),
        ],
        quote_via=quote,
    )
    return f"{base}/api/v1/namespaces/{namespace}/pods/{pod_name}/exec?{query}"


def _parse_stream_message(data: bytes) -> tuple[int, bytes]:
    if not data:
        return _CHANNEL_STDOUT, b""
    channel = data[0]
    return channel, data[1:]


async def exec_in_pod(
    *,
    auth: InClusterAuth,
    namespace: str,
    pod_name: str,
    command: list[str],
    container: str = "sandbox",
    timeout: float = 60.0,
) -> ExecResult:
    """Run command in pod container; collect stdout/stderr."""
    from websockets.asyncio.client import connect

    url = _build_exec_url(
        auth=auth,
        namespace=namespace,
        pod_name=pod_name,
        command=command,
        container=container,
    )
    headers = {"Authorization": auth.headers()["Authorization"]}
    stdout = bytearray()
    stderr = bytearray()
    error_text = bytearray()
    exit_code: int | None = 0

    ssl_ctx: Any = None
    ca = auth.client_kwargs().get("verify")
    if ca and ca is not False:
        import ssl

        ssl_ctx = ssl.create_default_context(cafile=str(ca))

    async with connect(url, additional_headers=headers, ssl=ssl_ctx, open_timeout=timeout) as ws:
        while True:
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except TimeoutError:
                break
            if isinstance(message, str):
                message = message.encode("utf-8")
            channel, payload = _parse_stream_message(message)
            if channel == _CHANNEL_STDOUT:
                stdout.extend(payload)
            elif channel == _CHANNEL_STDERR:
                stderr.extend(payload)
            elif channel == _CHANNEL_ERROR:
                error_text.extend(payload)
                if payload:
                    try:
                        import json

                        meta = json.loads(payload.decode("utf-8"))
                        if meta.get("status") == "Success":
                            exit_code = 0
                        elif "code" in meta:
                            exit_code = int(meta["code"])
                    except Exception:
                        exit_code = 1
                break

    if error_text and exit_code is None:
        exit_code = 1
        logger.warning(
            "k8s exec error pod=%s cmd=%s err=%s",
            pod_name,
            command[:3],
            error_text[:200],
        )

    return ExecResult(stdout=bytes(stdout), stderr=bytes(stderr), exit_code=exit_code)
