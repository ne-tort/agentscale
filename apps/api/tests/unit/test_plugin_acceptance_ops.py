"""Structural offline tests for the prodavan-ops plugin v4 acceptance client.

The API test environment already supplies httpx/pytest.  Only the standalone
ops source directory is added to sys.path; backend code and Kubernetes are not
imported by the acceptance module.  No test performs a live network call.

Written during verification freeze and intentionally not executed in this slice.
"""

from __future__ import annotations

import ast
import importlib
import io
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
OPS_SRC = REPO_ROOT / "infra" / "ops" / "src"
if str(OPS_SRC) not in sys.path:
    sys.path.insert(0, str(OPS_SRC))

acceptance = importlib.import_module("prodavan_ops.plugin")

TOKEN = "unit-test-plugin-token-7f3e9c"
SERVICE_TOKEN = "unit-test-service-token-81c2"
DSN = "postgresql+asyncpg://plugin:secret-password@db.internal:5432/tenant_acme"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwbHVnaW4ifQ.dGVzdC1zaWduYXR1cmU"
ENV = {
    acceptance.API_BASE_URL_ENV: "https://plugins.test/api/v1",
    acceptance.BEARER_TOKEN_ENV: TOKEN,
}
QUOTAS = {
    "ops_per_minute": 1,
    "max_rows": 2,
    "max_bytes": 3,
    "max_keys": 2,
    "max_object_bytes": 4,
    "max_egress_bytes": 5,
}
_MISSING = object()


def _admin_body(*, quotas: Any = _MISSING) -> dict[str, Any]:
    item: dict[str, Any] = {
        "plugin_id": "hello",
        "plugin_instance_id": "instance-hello",
        "manifest_version": "1.0.0",
        "artifact_digest": "sha256:" + "a" * 64,
        "grant_id": "grant-hello",
        "grant_status": "active",
        "runtime_status": "running",
        "enabled": True,
        "capabilities": ["cache", "docs"],
        "egress_mode": "none",
    }
    if quotas is not _MISSING:
        item["quotas"] = quotas
    return {
        "company_id": "acme",
        "items": [item],
        "enabled_modules": ["hello"],
    }


def _execute(action: str, handler: Any, **kwargs: Any):
    environ = kwargs.pop("environ", ENV)
    company_id = kwargs.pop("company_id", "acme")
    return acceptance.execute_action(
        action,
        environ=environ,
        company_id=company_id,
        transport=httpx.MockTransport(handler),
        sleep_fn=lambda _seconds: None,
        **kwargs,
    )


def test_evidence_shape_and_exact_secret_redaction(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = _admin_body()
        body["items"][0].update(
            {
                "claims": {"token": TOKEN},
                "echo": TOKEN,
                "dsn": DSN,
                "debug_jwt": JWT,
                "presigned_url": "https://objects.test/get?X-Amz-Signature=secret-signature",
            }
        )
        return httpx.Response(
            200,
            json=body,
            headers={
                "x-request-id": request.headers["x-request-id"],
                "authorization": f"Bearer {TOKEN}",
                "x-debug-token": TOKEN,
                "x-debug-dsn": DSN,
                "set-cookie": "session=secret-cookie",
            },
        )

    result = _execute("status", handler)
    assert result.ok, result.console_document()
    assert result.exit_code == 0
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v1/admin/plugins/"
    assert request.url.params["company_id"] == "acme"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"

    destination = acceptance.write_evidence(result, tmp_path / "status.evidence.json")
    raw = destination.read_text(encoding="utf-8")
    for secret in (TOKEN, DSN, JWT, "secret-signature", "secret-cookie"):
        assert secret not in raw

    document = json.loads(raw)
    assert document["schema_version"] == "plugin-acceptance/v1"
    assert document["ok"] is True
    assert document["evidence_status"] == "complete"
    assert document["http_status"] == 200
    assert document["request_id"] == request.headers["x-request-id"]
    assert document["started_at"].endswith("Z")
    assert document["finished_at"].endswith("Z")
    assert document["latency_ms"] >= 0
    assert document["request"]["method"] == "GET"
    assert document["request"]["url"].startswith("https://plugins.test/api/v1/admin/plugins/")
    assert "authorization" not in document["request"]["headers"]
    assert document["request"]["headers"]["x-request-id"] == document["request_id"]
    assert document["response"]["status"] == 200
    assert document["response"]["headers"]["x-request-id"] == document["request_id"]
    assert "authorization" not in document["response"]["headers"]
    assert "x-debug-token" not in document["response"]["headers"]
    assert "set-cookie" not in document["response"]["headers"]
    assert len(document["attempts"]) == 1

    echoed = document["data"]["items"][0]
    assert echoed["echo"] == "[REDACTED]"
    assert echoed["claims"]["token"] == "[REDACTED]"
    assert echoed["dsn"] == "[REDACTED]"
    assert echoed["presigned_url"] == "[REDACTED]"
    assert TOKEN not in json.dumps(result.console_document())


def test_capabilities_missing_quotas_is_incomplete_fail_closed(tmp_path: Path) -> None:
    result = _execute(
        "capabilities",
        lambda _request: httpx.Response(200, json=_admin_body()),
    )

    assert not result.ok
    assert result.exit_code == acceptance.EXIT_CONTRACT
    assert result.evidence_status == "incomplete"
    assert result.error is not None
    assert result.error.code == "quotas_evidence_unavailable"
    verdict = result.console_document()
    assert verdict["status"] == "error"
    assert verdict["error"]["details"]["evidence"] == {
        "status": "incomplete",
        "grants": "available",
        "quotas": "unavailable",
    }
    assert verdict["data"]["items"][0]["grant_id"] == "grant-hello"

    evidence_path = acceptance.write_evidence(result, tmp_path / "capabilities.json")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["ok"] is False
    assert evidence["exit_code"] == acceptance.EXIT_CONTRACT
    assert evidence["evidence_status"] == "incomplete"
    assert evidence["error"]["code"] == "quotas_evidence_unavailable"


def test_capabilities_unavailable_quota_field_is_not_successful_evidence() -> None:
    body = _admin_body(quotas=None)
    body["items"][0]["quotas_status"] = "unavailable"

    result = _execute(
        "capabilities",
        lambda _request: httpx.Response(200, json=body),
    )

    assert not result.ok
    assert result.exit_code == acceptance.EXIT_CONTRACT
    assert result.evidence_status == "incomplete"
    assert result.error is not None
    assert result.error.code == "quotas_evidence_unavailable"


def test_capabilities_empty_modules_do_not_claim_effective_evidence() -> None:
    result = _execute(
        "capabilities",
        lambda _request: httpx.Response(
            200, json={"company_id": "acme", "items": [], "enabled_modules": []}
        ),
    )
    assert not result.ok
    assert result.exit_code == acceptance.EXIT_CONTRACT
    assert result.evidence_status == "incomplete"
    assert result.error is not None
    assert result.error.code == "grants_evidence_unavailable"


def test_capabilities_accepts_only_explicit_positive_effective_quotas() -> None:
    result = _execute(
        "capabilities",
        lambda _request: httpx.Response(200, json=_admin_body(quotas=QUOTAS)),
    )
    assert result.ok, result.console_document()
    assert result.data["items"][0]["quotas"] == QUOTAS
    assert QUOTAS["ops_per_minute"] == 1
    assert set(QUOTAS) == set(acceptance._QUOTA_KEYS)


@pytest.mark.parametrize("quota_name", acceptance._QUOTA_KEYS)
def test_ops_preserves_429_evidence_for_every_quota_dimension(
    tmp_path: Path, quota_name: str
) -> None:
    """Offline HTTP contract only; MockTransport is not live enforcement proof."""
    payload = tmp_path / f"{quota_name}.json"
    payload.write_text('{"key":"cache-key"}', encoding="utf-8")

    result = _execute(
        "invoke",
        lambda _request: httpx.Response(
            429,
            json={
                "code": "QUOTA_EXCEEDED",
                "detail": f"{quota_name} limit exceeded",
            },
        ),
        operation="cache.get",
        payload_file=payload,
    )

    assert not result.ok
    assert result.http_status == 429
    assert result.exit_code == acceptance.EXIT_HTTP
    assert result.data["body"]["code"] == "QUOTA_EXCEEDED"
    assert result.data["body"]["detail"] == f"{quota_name} limit exceeded"
    evidence = result.evidence_document()
    assert evidence["response"]["status"] == 429
    assert evidence["response"]["body"]["code"] == "QUOTA_EXCEEDED"


@pytest.mark.parametrize("invalid", [0, -1, True, "60"])
def test_capabilities_rejects_defaulted_or_malformed_quota_values(invalid: Any) -> None:
    quotas = dict(QUOTAS)
    quotas["ops_per_minute"] = invalid
    result = _execute(
        "capabilities",
        lambda _request: httpx.Response(200, json=_admin_body(quotas=quotas)),
    )
    assert not result.ok
    assert result.exit_code == acceptance.EXIT_CONTRACT
    assert result.error is not None
    assert result.error.code == "quotas_evidence_invalid"


def test_invoke_uses_closed_typed_route_and_bounded_file_payload(tmp_path: Path) -> None:
    payload = tmp_path / "cache-get.json"
    payload.write_text('{"key":"cache-key"}', encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"decision": "allow", "data": {"value": "cached"}})

    result = _execute(
        "invoke",
        handler,
        operation="cache.get",
        payload_file=payload,
        max_payload_bytes=64,
    )
    assert result.ok, result.console_document()
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/capabilities/v1/cache/get"
    assert requests[0].content == b'{"key":"cache-key"}'
    assert result.payload is not None
    assert result.payload["size_bytes"] == len(requests[0].content)
    assert len(result.payload["sha256"]) == 64
    # Payload contents are not copied into evidence; only bounded metadata is.
    assert "cache-key" not in json.dumps(result.evidence_document())


def test_payload_limit_fails_before_http(tmp_path: Path) -> None:
    payload = tmp_path / "large.json"
    payload.write_text('{"key":"payload-that-is-too-large"}', encoding="utf-8")
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"decision": "allow", "data": {}})

    result = _execute(
        "invoke",
        handler,
        operation="cache.get",
        payload_file=payload,
        max_payload_bytes=8,
    )
    assert not result.ok
    assert result.exit_code == acceptance.EXIT_PAYLOAD
    assert result.error is not None
    assert result.error.code == "payload_too_large"
    assert calls == 0


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        (b'{"key":', "malformed_payload_json"),
        (b'["not", "an", "object"]', "payload_must_be_object"),
        (b'{"key":"a","key":"b"}', "malformed_payload_json"),
        (b'{"key":NaN}', "malformed_payload_json"),
    ],
)
def test_malformed_payload_fails_before_http(
    tmp_path: Path, raw: bytes, expected_code: str
) -> None:
    payload = tmp_path / "bad.json"
    payload.write_bytes(raw)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"decision": "allow", "data": {}})

    result = _execute(
        "invoke", handler, operation="cache.get", payload_file=payload, max_payload_bytes=128
    )
    assert not result.ok
    assert result.exit_code == acceptance.EXIT_PAYLOAD
    assert result.error is not None
    assert result.error.code == expected_code
    assert calls == 0


@pytest.mark.parametrize("status", [400, 403, 429, 500, 503])
def test_http_errors_have_nonzero_exit_and_preservable_evidence(
    tmp_path: Path, status: int
) -> None:
    result = _execute(
        "status",
        lambda _request: httpx.Response(
            status,
            json={"code": "EXPECTED_TEST_FAILURE", "detail": "expected"},
            headers={"x-test-evidence": "preserved"},
        ),
        retry=True,
    )
    assert not result.ok
    assert result.exit_code == acceptance.EXIT_HTTP
    assert result.http_status == status
    # 503 alone gets the single explicit retry; 4xx and non-retryable 500 do not.
    assert len(result.attempts) == (2 if status == 503 else 1)
    evidence_path = acceptance.write_evidence(result, tmp_path / f"http-{status}.json")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["response"]["status"] == status
    assert evidence["response"]["headers"]["x-test-evidence"] == "preserved"
    assert evidence["error"]["code"] == "http_status"


def test_success_with_malformed_json_is_contract_failure_and_redacted() -> None:
    result = _execute(
        "status",
        lambda _request: httpx.Response(
            200,
            content=f"<html>Bearer {TOKEN} {DSN}</html>".encode(),
            headers={"content-type": "text/html"},
        ),
    )
    assert not result.ok
    assert result.exit_code == acceptance.EXIT_CONTRACT
    assert result.error is not None
    assert result.error.code == "malformed_response_json"
    raw = json.dumps(result.evidence_document())
    assert TOKEN not in raw
    assert DSN not in raw
    assert result.attempts[0]["response"]["malformed_json"] is True


def test_timeout_is_nonzero_and_has_attempt_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("unit-test timeout", request=request)

    result = _execute("status", handler)
    assert not result.ok
    assert result.exit_code == acceptance.EXIT_TRANSPORT
    assert result.error is not None
    assert result.error.code == "transport_timeout"
    assert result.http_status is None
    assert len(result.attempts) == 1
    assert result.attempts[0]["response"] is None
    assert result.attempts[0]["error"]["code"] == "transport_timeout"


def test_retry_is_exactly_once_and_can_recover() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"detail": "waking"})
        return httpx.Response(200, json=_admin_body())

    result = _execute("status", handler, retry=True)
    assert result.ok, result.console_document()
    assert calls == 2
    assert len(result.attempts) == 2
    assert result.attempts[0]["response"]["status"] == 503
    assert result.attempts[1]["response"]["status"] == 200


def test_no_retry_flag_means_one_request_only() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"detail": "unavailable"})

    result = _execute("status", handler)
    assert not result.ok
    assert result.exit_code == acceptance.EXIT_HTTP
    assert calls == 1


def test_insecure_warns_on_stderr_and_disables_only_tls_verification() -> None:
    captured: dict[str, Any] = {}
    stderr = io.StringIO()

    def factory(**kwargs: Any) -> httpx.Client:
        captured.update(kwargs)
        return httpx.Client(**kwargs)

    result = _execute(
        "status",
        lambda _request: httpx.Response(200, json=_admin_body()),
        insecure=True,
        client_factory=factory,
        stderr=stderr,
    )
    assert result.ok, result.console_document()
    assert captured["verify"] is False
    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False
    warning = stderr.getvalue()
    assert "WARNING" in warning
    assert "--insecure" in warning
    assert TOKEN not in warning


def test_service_token_is_environment_only_bearer_credential() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json=_admin_body())

    env = {
        acceptance.API_BASE_URL_ENV: "https://plugins.test",
        acceptance.SERVICE_TOKEN_ENV: SERVICE_TOKEN,
    }
    result = _execute("status", handler, environ=env)
    assert result.ok, result.console_document()
    assert seen == [f"Bearer {SERVICE_TOKEN}"]
    assert SERVICE_TOKEN not in json.dumps(result.console_document())
    assert SERVICE_TOKEN not in json.dumps(result.evidence_document())


@pytest.mark.parametrize(
    ("environ", "expected_code"),
    [
        ({}, "missing_api_base_url"),
        ({acceptance.API_BASE_URL_ENV: "https://plugins.test"}, "missing_plugin_credentials"),
        (
            {
                acceptance.API_BASE_URL_ENV: "https://plugins.test",
                acceptance.BEARER_TOKEN_ENV: TOKEN,
                acceptance.SERVICE_TOKEN_ENV: SERVICE_TOKEN,
            },
            "ambiguous_plugin_credentials",
        ),
    ],
)
def test_missing_or_ambiguous_env_is_clean_fail_closed(
    environ: dict[str, str], expected_code: str
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP must not be attempted without valid env")

    result = _execute("status", handler, environ=environ)
    assert not result.ok
    assert result.exit_code == acceptance.EXIT_CONFIG
    assert result.error is not None
    assert result.error.code == expected_code
    assert result.attempts == []
    assert TOKEN not in json.dumps(result.console_document())
    assert SERVICE_TOKEN not in json.dumps(result.console_document())


def test_capability_deny_is_not_acceptance_even_with_http_200(tmp_path: Path) -> None:
    payload = tmp_path / "cache-get.json"
    payload.write_text('{"key":"cache-key"}', encoding="utf-8")
    result = _execute(
        "invoke",
        lambda _request: httpx.Response(200, json={"decision": "deny", "data": {"reason": "grant"}}),
        operation="cache.get",
        payload_file=payload,
    )
    assert not result.ok
    assert result.exit_code == acceptance.EXIT_CAPABILITY_DENIED
    assert result.error is not None
    assert result.error.code == "capability_denied"


def test_evidence_rejects_repository_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(acceptance, "repo_root", lambda: tmp_path)
    result = _execute(
        "status", lambda _request: httpx.Response(200, json=_admin_body())
    )
    docs_output = tmp_path / "docs" / "migration" / "plugin-evidence.json"
    (tmp_path / "docs" / "migration").mkdir(parents=True)
    with pytest.raises(acceptance.PluginAcceptanceError) as caught:
        acceptance.write_evidence(result, docs_output)
    assert caught.value.code == "evidence_in_repo_docs_forbidden"
    assert caught.value.exit_code == acceptance.EXIT_EVIDENCE_IO
    assert not docs_output.exists()


def test_cli_registration_preserves_existing_commands_without_secret_argv() -> None:
    cli_path = REPO_ROOT / "infra" / "ops" / "src" / "prodavan_ops" / "cli.py"
    source = cli_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(cli_path))
    commands: dict[str, set[str]] = {"app": set(), "e2e_app": set(), "plugin_app": set()}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            owner = decorator.func.value
            if (
                decorator.func.attr == "command"
                and isinstance(owner, ast.Name)
                and owner.id in commands
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
            ):
                commands[owner.id].add(str(decorator.args[0].value))

    assert {"version", "validate", "wait", "rollout", "smoke", "dns-check", "heal"} <= commands["app"]
    assert {"run", "cleanup"} <= commands["e2e_app"]
    assert commands["plugin_app"] == {"status", "capabilities", "invoke", "evidence"}
    assert "app.add_typer(plugin_app, name=\"plugin\")" in source
    for forbidden in ('"--token"', '"--bearer-token"', '"--service-token"', '"--api-key"'):
        assert forbidden not in source
