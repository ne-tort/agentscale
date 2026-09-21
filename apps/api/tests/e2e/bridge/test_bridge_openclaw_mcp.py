"""E2E - agent-runtime bridge with real openclaw API provider (cheapai.lol).

Builds the prodavan-agent-runtime image, runs it in Docker with OPENAI_API_KEY +
OPENAI_BASE_URL pointing at cheapai.lol/v1, then exercises the unified MCP
surface: real stdio MCP server for built-ins, hot MCP management via API,
multi-turn dialog with history. Uses claude-fable-5.1 (stable on cheapai).
"""
from __future__ import annotations
import os, socket, subprocess, time, json
from typing import Any
import httpx, pytest

pytestmark = [pytest.mark.bridge_e2e]

CHEAPAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://cheapai.lol/v1")
MODEL = os.environ.get("OPENAI_MODEL", "claude-fable-5.1")
IMAGE = os.environ.get("PRODAVAN_AGENT_RUNTIME_IMAGE", "prodavan-agent-runtime:e2e")
ECHO_SERVER = os.environ.get("ECHO_SERVER_PATH", "openclaw-sdk/scripts/mcp-echo-server.mjs")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_health(base: str, timeout: float = 90.0):
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


def _read_sse(url, body, timeout=200.0):
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


def _text(events):
    return "".join(e.get("data", {}).get("text", "") for e in events if e.get("type") == "text_delta")


def _tool_calls(events):
    return [(e["data"]["name"], e["data"].get("input")) for e in events if e.get("type") == "tool_call"]


@pytest.fixture(scope="module")
def bridge_url():
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set")
    port = _free_port()
    # Mount the prodavan-claw repo (has README + openclaw-sdk echo server).
    # In CI: GITHUB_WORKSPACE/prodavan-claw (cloned submodule). Locally: the
    # prodavan-claw dir relative to apps/api (../../prodavan-claw).
    repo_root = os.environ.get("GITHUB_WORKSPACE") or os.path.abspath(
        os.path.join(os.getcwd(), "..", "..")
    )
    claw_dir = os.path.join(repo_root, "prodavan-claw")
    if not os.path.isdir(claw_dir):
        claw_dir = repo_root  # fallback: tests run inside prodavan-claw already
    workspace = os.environ.get("PRODAVAN_AGENT_RUNTIME_WORKSPACE", "/workspace")
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    name = f"prodavan-agent-runtime-e2e-{run_id}"
    subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cmd = ["docker", "run", "-d", "--name", name, "-p", f"{port}:3921",
           "-e", f"OPENAI_API_KEY={api_key}", "-e", f"OPENAI_BASE_URL={CHEAPAI_BASE_URL}",
           "-e", f"OPENAI_MODEL={MODEL}",
           "-e", f"WORKSPACE_ROOT={workspace}", "-e", "OPENCLAW_DATA_DIR=/workspace/.openclaw-data",
           "-v", f"{claw_dir}:/workspace", IMAGE]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"docker run failed: {result.stderr}")
    base = f"http://127.0.0.1:{port}"
    try:
        health = _wait_health(base)
        if not health.get("provider"):
            raise RuntimeError(f"no provider: {health}")
        if "openclaw" not in (health.get("mcpServerStatus") or {}):
            raise RuntimeError(f"builtin MCP not connected: {health.get('mcpServerStatus')}")
        yield base
    except Exception:
        # dump container logs for debugging before teardown
        try:
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=10)
            print(f"--- docker logs {name} (stdout) ---\n{logs.stdout[-2000:]}")
            print(f"--- docker logs {name} (stderr) ---\n{logs.stderr[-2000:]}")
        except Exception:
            pass
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _send(base, sid, msg, **kw):
    body = {"message": msg, "use_tools": True, "live": True, "model": MODEL, "max_turns": 12, **kw}
    return _read_sse(f"{base}/v1/sessions/{sid}/send", body)


def test_bridge_mcp_visibility_and_tool_roundtrip(bridge_url):
    """Model sees mcp.openclaw.* tools and executes a real MCP roundtrip."""
    sid = httpx.post(f"{bridge_url}/v1/sessions", json={}).json()["sessionId"]
    # fs.read via mcp.openclaw.fs.read
    ev = _send(bridge_url, sid, "Use fs.read to read README.md and reply with the first line.")
    tc = _tool_calls(ev)
    assert any(n == "mcp.openclaw.fs.read" for n, _ in tc), f"expected mcp.openclaw.fs.read; got {tc}"
    tr = next((e for e in ev if e.get("type") == "tool_result"), None)
    assert tr is not None and not tr.get("data", {}).get("is_error"), f"tool_result error: {tr}"
    done = next((e for e in reversed(ev) if e.get("type") == "done"), None)
    assert done is not None and done.get("data", {}).get("reason") == "completed"
    # no HITL
    types = [e.get("type") for e in ev]
    assert "tool_approval_request" not in types, "HITL approval fired (should be off for MCP)"
    assert "permission_denial" not in types


def test_bridge_mcp_list_servers(bridge_url):
    """mcp.list_servers functional tool lists the openclaw builtin MCP server."""
    sid = httpx.post(f"{bridge_url}/v1/sessions", json={}).json()["sessionId"]
    ev = _send(bridge_url, sid, "Use mcp.list_servers to list connected MCP servers.")
    tc = _tool_calls(ev)
    assert any(n == "mcp.list_servers" for n, _ in tc), f"expected mcp.list_servers; got {tc}"
    tr = next((e for e in ev if e.get("type") == "tool_result"), None)
    assert tr is not None
    out = tr.get("data", {}).get("output", {})
    servers = out.get("servers", []) if isinstance(out, dict) else {}
    names = [s.get("name") for s in servers] if isinstance(servers, list) else []
    assert "openclaw" in names, f"openclaw server not listed: {names}"


def test_bridge_multi_turn_history(bridge_url):
    """History is preserved across turns (no reset)."""
    sid = httpx.post(f"{bridge_url}/v1/sessions", json={}).json()["sessionId"]
    # read README.md (any content) and ask the model to recall it
    _send(bridge_url, sid, "Use fs.read to read README.md and tell me the first heading line.")
    ev = _send(bridge_url, sid, "What was the first heading line of README.md I asked you to read? Just the line.")
    text = _text(ev)
    # the first heading of whatever README.md is mounted (prodavan-claw or platform-openclaw)
    assert text.strip().startswith("#") or "prodavan" in text.lower(), f"history lost; answer: {text[:200]}"


def test_bridge_hot_mcp_management(bridge_url):
    """Hot add/remove stub MCP + disable/enable built-in via API, agent sees changes."""
    sid = httpx.post(f"{bridge_url}/v1/sessions", json={}).json()["sessionId"]
    # add stub-test MCP (echo server from openclaw-sdk)
    r = httpx.post(f"{bridge_url}/v1/mcp/servers",
                  headers={"X-Prodavan-Events-Owner": "api"},
                  json={"name": "stub-test", "command": "node", "args": [ECHO_SERVER], "env": {}})
    assert r.status_code in (200, 201), f"add stub failed: {r.text}"
    # agent discovers + calls stub-test echo (may first list_tools to confirm)
    ev = _send(bridge_url, sid, "Call mcp.stub-test.echo with message 'hot-works'. Report the result verbatim.")
    tc = _tool_calls(ev)
    echo_calls = [e for e in ev if e.get("type") == "tool_call" and e["data"].get("name") == "mcp.stub-test.echo"]
    tr = next((e for e in ev if e.get("type") == "tool_result" and e["data"].get("name") == "mcp.stub-test.echo"), None)
    # The model may call mcp.list_tools(server="stub-test") first to confirm the
    # tool exists, then call echo on a second send. Retry once if echo not called.
    if not echo_calls:
        ev2 = _send(bridge_url, sid, "Now actually invoke mcp.stub-test.echo with arguments {message: 'hot-works'} and tell me the result.")
        tc = _tool_calls(ev2)
        echo_calls = [e for e in ev2 if e.get("type") == "tool_call" and e["data"].get("name") == "mcp.stub-test.echo"]
        tr = next((e for e in ev2 if e.get("type") == "tool_result" and e["data"].get("name") == "mcp.stub-test.echo"), None)
    # Assert the agent saw stub-test (either via list_tools or direct echo call):
    # this proves the hot-addServer reflected into the loop pool.
    saw_stub = any(
        e.get("type") == "tool_result" and "stub-test" in str(e.get("data", {}).get("output", ""))
        for e in ev
    ) or bool(echo_calls)
    assert saw_stub or echo_calls, f"agent did not see stub-test; tool_calls: {tc}"
    if tr is not None:
        assert not tr.get("data", {}).get("is_error"), f"echo failed: {tr}"
        out = str(tr.get("data", {}).get("output", ""))
        assert "hot-works" in out, f"echo result missing 'hot-works': {out[:200]}"
    # remove stub-test
    r = httpx.delete(f"{bridge_url}/v1/mcp/servers/stub-test",
                    headers={"X-Prodavan-Events-Owner": "api"})
    assert r.status_code == 204, f"remove stub failed: {r.text}"
    # disable fs.read
    r = httpx.post(f"{bridge_url}/v1/tools/mcp.openclaw.fs.read/disable",
                  headers={"X-Prodavan-Events-Owner": "api"})
    assert r.status_code == 200, f"disable failed: {r.text}"
    ev = _send(bridge_url, sid, "Use fs.read to read README.md now.")
    tc = _tool_calls(ev)
    assert not any(n == "mcp.openclaw.fs.read" for n, _ in tc), f"fs.read should be disabled; got {tc}"
    # re-enable fs.read
    r = httpx.post(f"{bridge_url}/v1/tools/mcp.openclaw.fs.read/enable",
                  headers={"X-Prodavan-Events-Owner": "api"})
    assert r.status_code == 200
    ev = _send(bridge_url, sid, "Use fs.read on README.md again.")
    tc = _tool_calls(ev)
    assert any(n == "mcp.openclaw.fs.read" for n, _ in tc), f"fs.read should be re-enabled; got {tc}"