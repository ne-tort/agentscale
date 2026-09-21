"""E2E - agent-runtime bridge with real openclaw API provider (cheapai.lol).

Builds the prodavan-agent-runtime image, runs it in Docker with OPENAI_API_KEY +
OPENAI_BASE_URL pointing at cheapai.lol/v1 (openclaw HTTP provider, not a
proprietary SDK), then sends a tool-mode request that must flow:
model (cheapai) -> tool_call (mcp.openclaw.fs.read) -> tool_result -> done.
Exercises the unified MCP surface (real stdio MCP server for built-ins) on the
OpenClaw loop path, with a live HTTP provider.
"""
from __future__ import annotations
import os, socket, subprocess, time
from typing import Any
import httpx, pytest

pytestmark = [pytest.mark.bridge_e2e]

CHEAPAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://cheapai.lol/v1")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
IMAGE = os.environ.get("PRODAVAN_AGENT_RUNTIME_IMAGE", "prodavan-agent-runtime:e2e")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_health(base: str, timeout: float = 60.0):
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/health", timeout=5.0)
            if r.status_code == 200:
                return r.json()
        except Exception as exc:
            last_err = exc
        time.sleep(1.0)
    raise RuntimeError(f"bridge health timeout: {last_err}")


def _read_sse(url, body, timeout=120.0):
    import json
    events = []
    with httpx.stream("POST", url, json=body, timeout=timeout) as resp:
        resp.raise_for_status()
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))
    return events


@pytest.fixture(scope="module")
def bridge_url():
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set")
    port = _free_port()
    workspace = os.environ.get("PRODAVAN_AGENT_RUNTIME_WORKSPACE", "/workspace")
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    name = f"prodavan-agent-runtime-e2e-{run_id}"
    subprocess.run(["docker","rm","-f",name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cmd = ["docker","run","-d","--name",name,"-p",f"{port}:3921",
           "-e",f"OPENAI_API_KEY={api_key}","-e",f"OPENAI_BASE_URL={CHEAPAI_BASE_URL}",
           "-e",f"OPENAI_MODEL={MODEL}",
           "-e",f"WORKSPACE_ROOT={workspace}","-e","OPENCLAW_DATA_DIR=/workspace/.openclaw-data",
           "-v",f"{os.getcwd()}:/workspace", IMAGE]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"docker run failed: {result.stderr}")
    base = f"http://127.0.0.1:{port}"
    try:
        health = _wait_health(base)
        if not health.get("provider"):
            raise RuntimeError(f"no provider: {health}")
        if "openclaw" not in (health.get("mcpServerStatus") or {}):
            raise RuntimeError(f"builtin MCP server not connected: {health.get('mcpServerStatus')}")
        yield base
    finally:
        subprocess.run(["docker","rm","-f",name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_bridge_mcp_tool_call_roundtrip(bridge_url):
    """Model (cheapai) -> tool_call (mcp.openclaw.fs.read) -> tool_result -> done."""
    create = httpx.post(f"{bridge_url}/v1/sessions", json={})
    assert create.status_code in (200, 201), create.text
    sid = create.json()["sessionId"]
    events = _read_sse(f"{bridge_url}/v1/sessions/{sid}/send", {
        "message": "Read README.md with the fs.read tool, then reply with the first line of the file.",
        "use_tools": True, "live": True, "model": MODEL})
    types = [e.get("type") for e in events]
    assert "tool_call" in types, f"missing tool_call; {types}"
    assert "tool_result" in types, f"missing tool_result; {types}"
    tr = next((e for e in events if e.get("type") == "tool_result"), None)
    assert tr is not None and not tr.get("data", {}).get("is_error"), f"tool_result error: {tr}"
    done = next((e for e in reversed(events) if e.get("type") == "done"), None)
    assert done is not None, "missing done"
    assert done.get("data", {}).get("reason") == "completed", f"bad done: {done}"
    assert "tool_approval_request" not in types, "HITL approval should not fire (no HITL for built-ins)"
    assert "permission_denial" not in types, "permission_denial should not fire (no HITL for built-ins)"