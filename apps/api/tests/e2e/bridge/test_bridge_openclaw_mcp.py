"""E2E - agent-runtime bridge with real openclaw API provider (cheapai.lol)."""
from __future__ import annotations
import os, socket, subprocess, time
from typing import Any
import httpx, pytest

CHEAPAI_BASE_URL = "https://api.cheapai.lol/v1"
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
        yield base
    finally:
        subprocess.run(["docker","rm","-f",name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_bridge_mcp_tool_call_roundtrip(bridge_url):
    create = httpx.post(f"{bridge_url}/v1/sessions", json={})
    assert create.status_code == 200, create.text
    sid = create.json()["sessionId"]
    events = _read_sse(f"{bridge_url}/v1/sessions/{sid}/send", {
        "message": "Use the fs.read tool to read package.json and reply with the name field value.",
        "use_tools": True, "live": True})
    types = [e.get("type") for e in events]
    assert "tool_call" in types, f"missing tool_call; {types}"
    assert "tool_result" in types, f"missing tool_result; {types}"
    done = next((e for e in reversed(events) if e.get("type") == "done"), None)
    assert done is not None, "missing done"
    assert done.get("data", {}).get("reason") == "completed", f"bad done: {done}"
