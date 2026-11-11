"""Offline regressions for the real CI -> platform publication gate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from urllib import parse


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github" / "scripts" / "plugin_publication.py").is_file():
            return parent
    raise RuntimeError("plugin CI repository root not found")


ROOT = _repo_root()
SCRIPT = ROOT / ".github" / "scripts" / "plugin_publication.py"
WORKFLOW = ROOT / ".github" / "workflows" / "plugin-artifacts.yml"


def _load() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("plugin_publication_offline_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load plugin_publication.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


publication = _load()
COMMIT = "c" * 40
BUILDER = "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "a" * 40
DIRECT_BUILDER = (
    "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@refs/heads/main"
)
BACKEND_DIGEST = "sha256:" + "b" * 64
BUNDLE_DIGEST = "sha256:" + "f" * 64
BACKEND_REF = "ghcr.io/ne-tort/plugins/hello_plugin/backend@" + BACKEND_DIGEST
BUNDLE_REF = "ghcr.io/ne-tort/plugins/hello_plugin/bundle@" + BUNDLE_DIGEST
# Exact record names emitted by plugin_verify.py; deliberately independent of
# publication.REQUIRED_RELEASE_CHECKS so the regression tests detect contract drift.
EXPECTED_RELEASE_CHECKS = frozenset(
    {
        "digest_pinned_refs",
        "cosign_verify:oci-artifact",
        "cosign_verify:backend-image",
        "canonical_manifest_config_and_blob",
        "backend_bundle_link",
        "attestation:backend:sbom",
        "attestation:backend:provenance",
        "attestation:bundle:sbom",
        "attestation:bundle:provenance",
        "gate:semver",
        "gate:protocol",
        "gate:capability_allowlist",
        "gate:platform_version",
        "gate:capability_grant",
        "gate:min_platform_version",
    }
)
SCANNER_IMAGE = "aquasec/trivy:0.58.2@sha256:" + "9" * 64
CSP = (
    "default-src 'self'; base-uri 'none'; object-src 'none'; "
    "frame-ancestors https://agentscale.example.test"
)
STATIC_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _source() -> dict:
    return {
        "source_id": "psrc_0123456789abcdef",
        "company_id": "company_1",
        "plugin_id": "hello_plugin",
        "repository": "ne-tort/example",
        "repository_id": 123456,
        "ref": "refs/heads/main",
        "trusted_workflow_ref": BUILDER,
        "static_origin": {
            "url_template": (
                "https://plugins.example.test/{plugin_id}/{version}/"
                "{bundle_digest}/index.html"
            ),
            "plugin_shell_origin": "https://agentscale.example.test",
            "security_headers": dict(STATIC_HEADERS),
        },
    }


def _config() -> dict:
    return {
        "schema_version": 1,
        "publication_base_url": "https://platform.example.test/base",
        "scanner_image": SCANNER_IMAGE,
        "sources": [_source()],
    }


def _environment(config: dict | None = None) -> dict[str, str]:
    return {
        publication.CONFIG_ENV: json.dumps(config or _config()),
        "GITHUB_REPOSITORY": "ne-tort/example",
        "GITHUB_REPOSITORY_ID": "123456",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": COMMIT,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "ACTIONS_ID_TOKEN_REQUEST_URL": (
            "https://pipelinesghubeus23.actions.githubusercontent.com/"
            "jobs/oidctoken?api-version=2.0"
        ),
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "github-request-secret",
        "DOCKER_CONFIG_PATH": "",
    }


def _direct_config() -> dict:
    document = _config()
    document["sources"][0]["repository"] = "ne-tort/agentscale"
    document["sources"][0]["trusted_workflow_ref"] = DIRECT_BUILDER
    return document


def _release_environment(*, event_name: str, direct: bool) -> dict[str, str]:
    env = _environment()
    if direct:
        env["GITHUB_REPOSITORY"] = "ne-tort/agentscale"
    env.update(
        {
            "GITHUB_EVENT_NAME": event_name,
            "EXPECTED_REPOSITORY": env["GITHUB_REPOSITORY"],
            "EXPECTED_REPOSITORY_ID": env["GITHUB_REPOSITORY_ID"],
            "EXPECTED_REF": "refs/heads/main",
            "DEFAULT_BRANCH": "main",
            "INPUT_WORKFLOW_REF": "" if direct else BUILDER,
            "WORKFLOW_SHA": "a" * 40,
        }
    )
    return env


def _oidc_claims(trust: publication.ReleaseTrust) -> dict:
    return {
        "iss": "https://token.actions.githubusercontent.com",
        "repository": trust.expected_repository,
        "repository_id": trust.expected_repository_id,
        "ref": trust.source_ref,
        "sha": trust.source_sha,
        "event_name": trust.event_name,
        "job_workflow_ref": trust.builder_identity,
        "ref_protected": "true",
        "aud": "agentscale-plugin-ci",
    }


def _manifest() -> dict:
    return {
        "schemaVersion": 2,
        "annotations": {
            "plugin_id": "hello_plugin",
            "manifest_version": "1.2.0",
            "backend_digest": BACKEND_DIGEST,
            "com.agentscale.plugin.backend_image_ref": BACKEND_REF,
            "com.agentscale.plugin.source_repository": "ne-tort/example",
            "com.agentscale.plugin.source_sha": COMMIT,
        },
    }


def _decision() -> dict:
    return {
        "decision": "PUBLISH_ALLOWED",
        "publish_allowed": True,
        "code": "OK",
        "artifact_ref": BUNDLE_REF,
        "backend_ref": BACKEND_REF,
        "backend_digest": BACKEND_DIGEST,
        "manifest_summary": {
            "plugin_id": "hello_plugin",
            "version": "1.2.0",
            "manifest_content_verified": True,
            "backend_image_ref": BACKEND_REF,
            "backend_digest": BACKEND_DIGEST,
        },
        "checks": [
            {"name": name, "ok": True}
            for name in sorted(EXPECTED_RELEASE_CHECKS)
        ],
    }


def _scan_report() -> dict:
    return {
        "SchemaVersion": 2,
        "ArtifactName": BACKEND_REF,
        "ArtifactType": "container_image",
        "Metadata": {"RepoDigests": [BACKEND_REF]},
        "Results": [{"Target": "backend", "Vulnerabilities": []}],
    }


def _payload_response(*, status: str = "PUBLISHED", code: str = "PUBLISHED") -> dict:
    return {
        "status": status,
        "code": code,
        "plugin_id": "hello_plugin",
        "version": "1.2.0",
        "bundle_digest": BUNDLE_DIGEST,
        "backend_digest": BACKEND_DIGEST,
        "source_id": "psrc_0123456789abcdef",
    }


def _json_response(status: int, document: dict, headers: dict | None = None) -> object:
    return publication.HttpResponse(
        status=status,
        headers={key.lower(): value for key, value in (headers or {}).items()},
        body=json.dumps(document).encode("utf-8"),
    )


class FakeTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def send(self, method: str, url: str, **kwargs) -> object:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected network call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class PublicationFixture:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.manifest = self.root / "oci.json"
        self.decision = self.root / "release-decision.json"
        self.sbom = self.root / "bundle-sbom.json"
        self.provenance = self.root / "bundle-provenance.json"
        self.scan = self.root / "backend-release-scan.json"
        self.docker_config = self.root / "docker-config.json"
        self.manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
        self.decision.write_text(json.dumps(_decision()), encoding="utf-8")
        self.sbom.write_text('{"spdxVersion":"SPDX-2.3"}', encoding="utf-8")
        self.provenance.write_text('{"buildType":"test"}', encoding="utf-8")
        self.docker_config.write_text("{}", encoding="utf-8")

    def environment(self, config: dict | None = None) -> dict[str, str]:
        env = _environment(config)
        env["DOCKER_CONFIG_PATH"] = str(self.docker_config)
        return env

    def scan_runner(self, test: unittest.TestCase, report: dict | None = None):
        def run(command: list[str]) -> None:
            test.assertEqual(command[-1], BACKEND_REF)
            test.assertIn(SCANNER_IMAGE, command)
            test.assertIn("--pull=always", command)
            test.assertNotIn("plugins-quarantine", " ".join(command))
            self.scan.write_text(json.dumps(report or _scan_report()), encoding="utf-8")

        return run

    def kwargs(self, env: dict[str, str], transport: FakeTransport, runner) -> dict:
        return {
            "environ": env,
            "transport": transport,
            "plugin_id": "hello_plugin",
            "version": "1.2.0",
            "builder_identity": BUILDER,
            "bundle_ref": BUNDLE_REF,
            "bundle_digest": BUNDLE_DIGEST,
            "backend_ref": BACKEND_REF,
            "backend_digest": BACKEND_DIGEST,
            "manifest_path": str(self.manifest),
            "release_decision_path": str(self.decision),
            "scan_report_path": str(self.scan),
            "bundle_sbom_path": str(self.sbom),
            "bundle_provenance_path": str(self.provenance),
            "scan_runner": runner,
        }


def csp_response(*, digest: str = BUNDLE_DIGEST) -> object:
    headers = dict(STATIC_HEADERS)
    headers["X-AgentScale-Bundle-Digest"] = digest
    return publication.HttpResponse(status=200, headers={
        key.lower(): value for key, value in headers.items()
    }, body=b"")


class ProtectedConfigTests(unittest.TestCase):
    def test_missing_protected_config_is_a_typed_blocker(self) -> None:
        env = _environment()
        env[publication.CONFIG_ENV] = ""
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.PublicationConfig.from_environment(env)
        self.assertEqual(denied.exception.code, "PROTECTED_CONFIG_REQUIRED")

    def test_source_mapping_uses_provisioned_company_plugin_repo_identity(self) -> None:
        env = _environment()
        config = publication.PublicationConfig.from_environment(env)
        context = publication.GitHubContext.from_environment(env, builder_identity=BUILDER)
        source = publication.bind_source(config, context, plugin_id="hello_plugin")
        self.assertEqual(source.source_id, "psrc_0123456789abcdef")
        self.assertEqual(source.company_id, "company_1")
        self.assertEqual(source.repository_id, 123456)
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.bind_source(config, context, plugin_id="other_plugin")
        self.assertEqual(denied.exception.code, "SOURCE_BINDING_REQUIRED")

    def test_ambiguous_company_source_mapping_is_fail_closed(self) -> None:
        config_document = _config()
        other = _source()
        other["source_id"] = "psrc_other"
        other["company_id"] = "company_2"
        config_document["sources"].append(other)
        env = _environment(config_document)
        config = publication.PublicationConfig.from_environment(env)
        context = publication.GitHubContext.from_environment(env, builder_identity=BUILDER)
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.bind_source(config, context, plugin_id="hello_plugin")
        self.assertEqual(denied.exception.code, "SOURCE_BINDING_REQUIRED")

    def test_https_userinfo_query_fragment_and_generic_http_are_rejected(self) -> None:
        for endpoint in (
            "http://platform.example.test",
            "https://user:password@platform.example.test",
            "https://platform.example.test?token=secret",
            "https://platform.example.test?",
            "https://platform.example.test/#fragment",
            "https://platform.example.test#",
        ):
            with self.subTest(endpoint=endpoint):
                document = _config()
                document["publication_base_url"] = endpoint
                with self.assertRaises(publication.PublicationBlocked):
                    publication.PublicationConfig.from_environment(_environment(document))

    def test_only_explicit_typed_dev_http_allowlist_enables_http(self) -> None:
        document = _config()
        document["publication_base_url"] = "http://127.0.0.1:8080/internal-api"
        env = _environment(document)
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.PublicationConfig.from_environment(env)
        self.assertEqual(denied.exception.code, "HTTPS_REQUIRED")
        env[publication.DEV_HTTP_ALLOWLIST_ENV] = json.dumps(
            ["http://127.0.0.1:8080/internal-api"]
        )
        config = publication.PublicationConfig.from_environment(env)
        self.assertEqual(
            config.publication_url,
            "http://127.0.0.1:8080/internal-api/internal/plugin-artifacts",
        )

    def test_static_origin_requires_exact_distinct_shell_origin(self) -> None:
        document = _config()
        del document["sources"][0]["static_origin"]["plugin_shell_origin"]
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.PublicationConfig.from_environment(_environment(document))
        self.assertEqual(denied.exception.code, "CSP_CONFIG_INVALID")
        for origin in (
            "",
            "http://agentscale.example.test",
            "https://*.example.test",
            "https://agentscale.example.test/path",
            "https://agentscale.example.test/?token=x",
            "https://plugins.example.test",
        ):
            with self.subTest(origin=origin):
                document = _config()
                document["sources"][0]["static_origin"]["plugin_shell_origin"] = origin
                with self.assertRaises(publication.PublicationBlocked) as denied:
                    publication.PublicationConfig.from_environment(_environment(document))
                self.assertEqual(denied.exception.code, "CSP_CONFIG_INVALID")

    def test_static_security_headers_must_exactly_match_gateway_policy(self) -> None:
        config = publication.PublicationConfig.from_environment(_environment())
        static = config.sources[0].static_origin
        self.assertIsNotNone(static)
        assert static is not None
        self.assertEqual(static.plugin_shell_origin, "https://agentscale.example.test")
        self.assertEqual(static.security_headers["content-security-policy"], CSP)
        for mutate in (
            lambda headers: headers.update({
                "Content-Security-Policy": (
                    "default-src 'self'; base-uri 'none'; object-src 'none'; "
                    "frame-ancestors 'self'"
                )
            }),
            lambda headers: headers.update({
                "Content-Security-Policy": CSP + " https://evil.example.test"
            }),
            lambda headers: headers.update({"Referrer-Policy": "unsafe-url"}),
            lambda headers: headers.pop("Permissions-Policy"),
        ):
            with self.subTest(mutate=mutate):
                document = _config()
                headers = document["sources"][0]["static_origin"]["security_headers"]
                mutate(headers)
                with self.assertRaises(publication.PublicationBlocked) as denied:
                    publication.PublicationConfig.from_environment(_environment(document))
                self.assertEqual(denied.exception.code, "CSP_CONFIG_INVALID")


class ReleaseTrustTests(unittest.TestCase):
    def _bind(self, *, event_name: str, direct: bool) -> publication.SourceBinding:
        document = _direct_config() if direct else _config()
        env = _environment(document)
        env["GITHUB_EVENT_NAME"] = event_name
        builder = DIRECT_BUILDER if direct else BUILDER
        if direct:
            env["GITHUB_REPOSITORY"] = "ne-tort/agentscale"
        config = publication.PublicationConfig.from_environment(env)
        context = publication.GitHubContext.from_environment(env, builder_identity=builder)
        return publication.bind_source(config, context, plugin_id="hello_plugin")

    def test_direct_default_branch_push_and_dispatch_match_provisioned_identity(self) -> None:
        for event_name in ("push", "workflow_dispatch"):
            with self.subTest(event_name=event_name):
                source = self._bind(event_name=event_name, direct=True)
                self.assertEqual(source.ref, "refs/heads/main")
                self.assertEqual(source.trusted_workflow_ref, DIRECT_BUILDER)

    def test_reusable_workflow_call_preserves_full_sha_identity(self) -> None:
        source = self._bind(event_name="workflow_call", direct=False)
        self.assertEqual(source.trusted_workflow_ref, BUILDER)
        self.assertRegex(source.trusted_workflow_ref, r"@[0-9a-f]{40}$")
        canonical_env = _release_environment(event_name="workflow_call", direct=False)
        canonical_env["GITHUB_REPOSITORY"] = "ne-tort/agentscale"
        canonical_env["EXPECTED_REPOSITORY"] = "ne-tort/agentscale"
        trust = publication.ReleaseTrust.from_environment(canonical_env)
        self.assertEqual(trust.builder_identity, BUILDER)

    def test_direct_identity_rejects_wrong_branch_or_workflow(self) -> None:
        env = _environment(_direct_config())
        env["GITHUB_REPOSITORY"] = "ne-tort/agentscale"
        env["GITHUB_EVENT_NAME"] = "workflow_dispatch"
        wrong_identities = (
            DIRECT_BUILDER.replace("refs/heads/main", "refs/heads/release"),
            DIRECT_BUILDER.replace("plugin-artifacts.yml", "other.yml"),
            DIRECT_BUILDER.replace("refs/heads/main", "refs/heads/*"),
            DIRECT_BUILDER + "/feature",
        )
        for builder in wrong_identities:
            with self.subTest(builder=builder), self.assertRaises(
                publication.PublicationBlocked
            ) as denied:
                publication.GitHubContext.from_environment(env, builder_identity=builder)
            self.assertEqual(denied.exception.code, "GITHUB_SOURCE_CONTEXT_INVALID")

        document = _direct_config()
        document["sources"][0]["trusted_workflow_ref"] = DIRECT_BUILDER.replace(
            "refs/heads/main", "refs/heads/release"
        )
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.PublicationConfig.from_environment(_environment(document))
        self.assertEqual(denied.exception.code, "SOURCE_CONFIG_INVALID")

        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.GitHubContext.from_environment(
                _environment(), builder_identity=DIRECT_BUILDER
            )
        self.assertEqual(denied.exception.code, "GITHUB_SOURCE_CONTEXT_INVALID")

    def test_protected_source_binding_rejects_wrong_ref_or_reusable_workflow(self) -> None:
        document = _direct_config()
        env = _environment(document)
        env.update(
            {
                "GITHUB_REPOSITORY": "ne-tort/agentscale",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REF": "refs/heads/release",
            }
        )
        context = publication.GitHubContext.from_environment(
            env,
            builder_identity=DIRECT_BUILDER.replace("refs/heads/main", "refs/heads/release"),
        )
        config = publication.PublicationConfig.from_environment(env)
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.bind_source(config, context, plugin_id="hello_plugin")
        self.assertEqual(denied.exception.code, "SOURCE_BINDING_REQUIRED")

        env = _environment()
        env["GITHUB_EVENT_NAME"] = "workflow_call"
        context = publication.GitHubContext.from_environment(
            env,
            builder_identity=BUILDER[:-40] + "b" * 40,
        )
        config = publication.PublicationConfig.from_environment(env)
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.bind_source(config, context, plugin_id="hello_plugin")
        self.assertEqual(denied.exception.code, "SOURCE_BINDING_REQUIRED")

    def test_release_trust_accepts_only_typed_bound_release_modes(self) -> None:
        cases = (
            ("push", True, DIRECT_BUILDER),
            ("workflow_dispatch", True, DIRECT_BUILDER),
            ("workflow_call", False, BUILDER),
        )
        for event_name, direct, expected_builder in cases:
            with self.subTest(event_name=event_name, direct=direct):
                trust = publication.ReleaseTrust.from_environment(
                    _release_environment(event_name=event_name, direct=direct)
                )
                self.assertEqual(trust.builder_identity, expected_builder)
                self.assertEqual(trust.source_ref, "refs/heads/main")
                self.assertEqual(trust.source_sha, COMMIT)
                publication.validate_release_oidc_claims(
                    _oidc_claims(trust), trust=trust
                )

    def test_release_trust_rejects_wrong_event_ref_branch_or_pinned_workflow(self) -> None:
        wrong_event = _release_environment(event_name="push", direct=True)
        wrong_event["GITHUB_EVENT_NAME"] = "pull_request"
        wrong_ref = _release_environment(event_name="workflow_dispatch", direct=True)
        wrong_ref["GITHUB_REF"] = "refs/heads/release"
        wrong_default = _release_environment(event_name="push", direct=True)
        wrong_default["DEFAULT_BRANCH"] = "release"
        wrong_input = _release_environment(event_name="workflow_call", direct=False)
        wrong_input["INPUT_WORKFLOW_REF"] = BUILDER[:-40] + "b" * 40
        mutable_workflow = _release_environment(event_name="workflow_call", direct=False)
        mutable_workflow["WORKFLOW_SHA"] = "main"
        for env in (wrong_event, wrong_ref, wrong_default, wrong_input, mutable_workflow):
            with self.subTest(event=env["GITHUB_EVENT_NAME"]), self.assertRaises(
                publication.PublicationBlocked
            ) as denied:
                publication.ReleaseTrust.from_environment(env)
            self.assertEqual(denied.exception.code, "GITHUB_SOURCE_CONTEXT_INVALID")

    def test_oidc_requires_exact_workflow_source_and_protected_ref(self) -> None:
        for event_name, direct in (
            ("push", True),
            ("workflow_dispatch", True),
            ("workflow_call", False),
        ):
            with self.subTest(event_name=event_name):
                trust = publication.ReleaseTrust.from_environment(
                    _release_environment(event_name=event_name, direct=direct)
                )
                mutations = (
                    ("job_workflow_ref", BUILDER[:-40] + "b" * 40),
                    ("ref", "refs/heads/release"),
                    ("sha", "d" * 40),
                    ("repository_id", "999999"),
                    ("aud", "unexpected-audience"),
                    ("ref_protected", "false"),
                )
                for claim, value in mutations:
                    claims = _oidc_claims(trust)
                    claims[claim] = value
                    with self.subTest(claim=claim), self.assertRaises(
                        publication.PublicationBlocked
                    ) as denied:
                        publication.validate_release_oidc_claims(claims, trust=trust)
                    self.assertEqual(denied.exception.code, "OIDC_CLAIMS_MISMATCH")
                claims = _oidc_claims(trust)
                del claims["ref_protected"]
                with self.assertRaises(publication.PublicationBlocked):
                    publication.validate_release_oidc_claims(claims, trust=trust)


class OidcAndTransportTests(unittest.TestCase):
    def test_oidc_audience_and_bearer_header_without_decoding(self) -> None:
        transport = FakeTransport([_json_response(200, {"value": "h.p.s"})])
        token = publication.fetch_github_oidc(transport, _environment())
        self.assertEqual(token, "h.p.s")
        call = transport.calls[0]
        query = parse.parse_qs(parse.urlsplit(call["url"]).query)
        self.assertEqual(query["audience"], ["agentscale-plugin-ci"])
        self.assertEqual(call["headers"]["Authorization"], "Bearer github-request-secret")
        self.assertNotIn("urlsafe_b64decode", SCRIPT.read_text(encoding="utf-8"))

    def test_transport_failure_redacts_request_and_bearer_secrets(self) -> None:
        transport = FakeTransport([
            publication.TransportFailure("github-request-secret Authorization: Bearer h.p.s")
        ])
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.fetch_github_oidc(transport, _environment())
        self.assertEqual(denied.exception.code, "OIDC_UNAVAILABLE")
        self.assertNotIn("github-request-secret", str(denied.exception))
        self.assertNotIn("h.p.s", str(denied.exception))

    def test_https_redirect_is_rejected_without_following(self) -> None:
        transport = FakeTransport([
            publication.HttpResponse(
                status=302,
                headers={"location": "https://secret.internal/redirect"},
                body=b"internal response",
            )
        ])
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication._send(
                transport,
                "GET",
                "https://platform.example.test/internal/plugin-artifacts",
                headers={},
                unavailable_code="PLATFORM_UNAVAILABLE",
            )
        self.assertEqual(denied.exception.code, "REDIRECT_REJECTED")
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("secret.internal", str(denied.exception))

    def test_oidc_redirect_is_rejected_without_following(self) -> None:
        transport = FakeTransport(
            [
                publication.HttpResponse(
                    status=302,
                    headers={"location": "https://secret.internal/oidc"},
                    body=b"",
                )
            ]
        )
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.fetch_github_oidc(transport, _environment())
        self.assertEqual(denied.exception.code, "REDIRECT_REJECTED")
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("secret.internal", str(denied.exception))

    def test_transport_disables_environment_proxy_and_bounds_responses(self) -> None:
        self.assertIs(publication.UrllibTransport.trust_env, False)
        self.assertEqual(publication.HTTP_TIMEOUT_SECONDS, 15.0)
        self.assertEqual(publication.MAX_RESPONSE_BYTES, 64 * 1024)


class EvidenceTests(unittest.TestCase):
    def test_scan_requires_release_binding_and_clean_pinned_report(self) -> None:
        self.assertIs(
            publication.validate_scan_report(
                _scan_report(), backend_ref=BACKEND_REF, scanner_image=SCANNER_IMAGE
            ),
            True,
        )
        wrong = _scan_report()
        wrong["Metadata"]["RepoDigests"] = [
            BACKEND_REF.replace("/plugins/", "/plugins-quarantine/")
        ]
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.validate_scan_report(
                wrong, backend_ref=BACKEND_REF, scanner_image=SCANNER_IMAGE
            )
        self.assertEqual(denied.exception.code, "SCAN_RELEASE_BINDING_MISMATCH")

    def test_scan_findings_are_quarantine_not_publication(self) -> None:
        report = _scan_report()
        report["Results"][0]["Vulnerabilities"] = [
            {"VulnerabilityID": "CVE-TEST", "Severity": "CRITICAL"}
        ]
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.validate_scan_report(
                report, backend_ref=BACKEND_REF, scanner_image=SCANNER_IMAGE
            )
        self.assertEqual(denied.exception.code, "SCAN_POLICY_FAILED")
        self.assertEqual(denied.exception.status, "QUARANTINED")

    def test_csp_requires_actual_headers_and_exact_bundle_binding(self) -> None:
        env = _environment()
        config = publication.PublicationConfig.from_environment(env)
        source = config.sources[0]
        self.assertIs(
            publication.validate_csp_response(
                csp_response(), source=source, bundle_digest=BUNDLE_DIGEST
            ),
            True,
        )
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.validate_csp_response(
                csp_response(digest="sha256:" + "0" * 64),
                source=source,
                bundle_digest=BUNDLE_DIGEST,
            )
        self.assertEqual(denied.exception.code, "CSP_ARTIFACT_BINDING_MISMATCH")
        bad_headers = csp_response()
        bad_headers.headers["x-content-type-options"] = "unsafe"
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.validate_csp_response(
                bad_headers, source=source, bundle_digest=BUNDLE_DIGEST
            )
        self.assertEqual(denied.exception.code, "CSP_SECURITY_HEADERS_FAILED")
        extra_ancestor = csp_response()
        extra_ancestor.headers["content-security-policy"] += " https://evil.example.test"
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.validate_csp_response(
                extra_ancestor, source=source, bundle_digest=BUNDLE_DIGEST
            )
        self.assertEqual(denied.exception.code, "CSP_SECURITY_HEADERS_FAILED")

    def test_false_or_self_asserted_evidence_is_rejected(self) -> None:
        for key in ("scan_verified", "csp_verified"):
            with self.subTest(key=key):
                report = _scan_report()
                report[key] = False
                with self.assertRaises(publication.PublicationBlocked) as denied:
                    publication.validate_scan_report(
                        report, backend_ref=BACKEND_REF, scanner_image=SCANNER_IMAGE
                    )
                self.assertEqual(denied.exception.code, "SCAN_EVIDENCE_UNTRUSTED")
        document = _config()
        document["scan_verified"] = False
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.PublicationConfig.from_environment(_environment(document))
        self.assertEqual(denied.exception.code, "PROTECTED_CONFIG_INVALID")


class ReleaseDecisionTests(unittest.TestCase):
    def _validate(self, decision: dict) -> None:
        publication.validate_release_decision(
            decision,
            plugin_id="hello_plugin",
            version="1.2.0",
            bundle_ref=BUNDLE_REF,
            bundle_digest=BUNDLE_DIGEST,
            backend_ref=BACKEND_REF,
            backend_digest=BACKEND_DIGEST,
        )

    def _assert_blocked(self, decision: dict) -> None:
        with self.assertRaises(publication.PublicationBlocked) as denied:
            self._validate(decision)
        self.assertEqual(denied.exception.code, "RELEASE_VERIFICATION_REQUIRED")

    def test_verifier_contract_and_complete_valid_decision(self) -> None:
        self.assertEqual(publication.REQUIRED_RELEASE_CHECKS, EXPECTED_RELEASE_CHECKS)
        self.assertIsNone(self._validate(_decision()))

    def test_removing_any_required_check_blocks(self) -> None:
        for name in sorted(EXPECTED_RELEASE_CHECKS):
            with self.subTest(name=name):
                decision = _decision()
                decision["checks"] = [
                    item for item in decision["checks"] if item["name"] != name
                ]
                self._assert_blocked(decision)

    def test_false_ok_on_any_required_check_blocks(self) -> None:
        for name in sorted(EXPECTED_RELEASE_CHECKS):
            with self.subTest(name=name):
                decision = _decision()
                check = next(
                    item for item in decision["checks"] if item["name"] == name
                )
                check["ok"] = False
                self._assert_blocked(decision)

    def test_failed_additional_gate_is_not_filtered_out(self) -> None:
        decision = _decision()
        decision["checks"].append(
            {"name": "gate:additional_policy", "ok": False, "detail": "incompatible"}
        )
        self._assert_blocked(decision)


class PublicationGateTests(unittest.TestCase):
    def test_payload_and_published_response_match_exact_backend_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PublicationFixture(tmp)
            transport = FakeTransport([
                csp_response(),
                _json_response(200, {"value": "h.p.s"}),
                _json_response(201, _payload_response()),
            ])
            env = fixture.environment()
            outcome = publication.publish(
                **fixture.kwargs(env, transport, fixture.scan_runner(self))
            )
            self.assertEqual(outcome.status, "PUBLISHED")
            self.assertEqual([call["method"] for call in transport.calls], ["HEAD", "GET", "POST"])
            head, oidc, post = transport.calls
            self.assertNotIn("Authorization", head["headers"])
            self.assertIn(BUNDLE_DIGEST, head["url"])
            self.assertEqual(parse.urlsplit(head["url"]).hostname, "plugins.example.test")
            self.assertEqual(parse.urlsplit(head["url"]).query, "")
            self.assertEqual(
                post["url"],
                "https://platform.example.test/base/internal/plugin-artifacts",
            )
            self.assertEqual(post["headers"]["Authorization"], "Bearer h.p.s")
            payload = json.loads(post["body"])
            self.assertEqual(
                set(payload),
                {
                    "source_id", "plugin_id", "version", "bundle_ref", "bundle_digest",
                    "backend_ref", "backend_digest", "manifest", "commit_sha", "ref",
                    "run_url", "evidence",
                },
            )
            self.assertNotIn("capabilities", payload)
            self.assertNotIn("approval", payload)
            self.assertNotIn("company_id", payload)
            self.assertEqual(payload["source_id"], "psrc_0123456789abcdef")
            self.assertEqual(
                payload["run_url"],
                "https://github.com/ne-tort/example/actions/runs/123/attempts/2",
            )
            self.assertEqual(
                set(payload["evidence"]),
                {"scan_verified", "csp_verified", "sbom_digest", "provenance_digest"},
            )
            self.assertIs(payload["evidence"]["scan_verified"], True)
            self.assertIs(payload["evidence"]["csp_verified"], True)
            self.assertEqual(
                payload["evidence"]["sbom_digest"],
                "sha256:" + hashlib.sha256(fixture.sbom.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                payload["evidence"]["provenance_digest"],
                "sha256:" + hashlib.sha256(fixture.provenance.read_bytes()).hexdigest(),
            )

    def test_quarantined_missing_approval_is_failed_gate(self) -> None:
        response = _json_response(
            422, _payload_response(status="QUARANTINED", code="APPROVAL_REQUIRED")
        )
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.parse_publication_response(response, {"plugin_id": "hello_plugin"})
        self.assertEqual(denied.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(denied.exception.status, "QUARANTINED")

    def test_http_errors_never_become_success_or_leak_response(self) -> None:
        response = publication.HttpResponse(
            status=503,
            headers={},
            body=b"https://secret.internal/token=secret",
        )
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.parse_publication_response(response, {})
        self.assertEqual(denied.exception.code, "PLATFORM_HTTP_503")
        self.assertNotIn("secret", str(denied.exception))
        published_on_wrong_status = _json_response(200, _payload_response())
        with self.assertRaises(publication.PublicationBlocked) as denied:
            publication.parse_publication_response(published_on_wrong_status, {})
        self.assertEqual(denied.exception.code, "PLATFORM_HTTP_200")

    def test_missing_csp_blocks_before_oidc_or_platform_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PublicationFixture(tmp)
            document = _config()
            document["sources"][0]["static_origin"] = None
            transport = FakeTransport([])
            with self.assertRaises(publication.PublicationBlocked) as denied:
                publication.publish(
                    **fixture.kwargs(
                        fixture.environment(document),
                        transport,
                        fixture.scan_runner(self),
                    )
                )
            self.assertEqual(denied.exception.code, "CSP_EVIDENCE_REQUIRED")
            self.assertEqual(transport.calls, [])

    def test_missing_scan_report_blocks_before_csp_oidc_or_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PublicationFixture(tmp)
            transport = FakeTransport([])
            with self.assertRaises(publication.PublicationBlocked) as denied:
                publication.publish(
                    **fixture.kwargs(
                        fixture.environment(), transport, lambda command: None
                    )
                )
            self.assertEqual(denied.exception.code, "SCAN_EVIDENCE_REQUIRED")
            self.assertEqual(transport.calls, [])

    def test_failed_release_decision_blocks_before_scanner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PublicationFixture(tmp)
            decision = _decision()
            decision["decision"] = "QUARANTINED"
            fixture.decision.write_text(json.dumps(decision), encoding="utf-8")
            transport = FakeTransport([])
            with self.assertRaises(publication.PublicationBlocked) as denied:
                publication.publish(
                    **fixture.kwargs(
                        fixture.environment(),
                        transport,
                        lambda command: self.fail("scanner must not run"),
                    )
                )
            self.assertEqual(denied.exception.code, "RELEASE_VERIFICATION_REQUIRED")
            self.assertEqual(transport.calls, [])


    def test_summary_reports_actual_failure_or_exact_published_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            publication._append_summary(
                str(summary),
                outcome=publication.PublicationOutcome(
                    status="QUARANTINED", code="APPROVAL_REQUIRED"
                ),
                plugin_id="hello_plugin",
                version="1.2.0",
                bundle_ref=BUNDLE_REF,
                backend_ref=BACKEND_REF,
            )
            failed = summary.read_text(encoding="utf-8")
            self.assertIn("Result: QUARANTINED (APPROVAL_REQUIRED)", failed)
            self.assertNotIn("exact matching PUBLISHED response", failed)
            summary.write_text("", encoding="utf-8")
            publication._append_summary(
                str(summary),
                outcome=publication.PublicationOutcome(
                    status="PUBLISHED", code="PUBLISHED"
                ),
                plugin_id="hello_plugin",
                version="1.2.0",
                bundle_ref=BUNDLE_REF,
                backend_ref=BACKEND_REF,
            )
            succeeded = summary.read_text(encoding="utf-8")
            self.assertIn("Result: PUBLISHED (PUBLISHED)", succeeded)
            self.assertIn("exact matching PUBLISHED response", succeeded)


class WorkflowStaticTests(unittest.TestCase):
    def test_real_gate_has_no_disabled_or_unimplemented_blocker(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIsNone(
            re.search(r"^\s*if:\s*(?:false|\$\{\{\s*false\s*\}\})", workflow, re.MULTILINE)
        )
        self.assertNotIn("not implemented", workflow.lower())
        self.assertNotIn("unimplemented platform", workflow.lower())
        self.assertNotIn("backend contract exists", workflow.lower())
        self.assertIn("PLUGIN_PLATFORM_PUBLICATION_CONFIG", workflow)
        self.assertIn("PLUGIN_PLATFORM_DEV_HTTP_ALLOWLIST", workflow)
        self.assertIn("plugin_id: ${{ steps.manifest.outputs.plugin_id }}", workflow)

    def test_release_verify_precedes_release_scan_csp_and_platform_post(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        quarantine = workflow.index("Build and push canonical OCI bundle only to quarantine")
        verify = workflow.index("Verify signatures and all evidence before any release write")
        promote = workflow.index("Promote verified backend and bundle with all referrers by digest")
        release = workflow.index("Reverify actual release refs and copied referrers")
        publish = workflow.index("Verify independent release evidence and publish through platform OIDC")
        self.assertLess(quarantine, verify)
        self.assertLess(verify, promote)
        self.assertLess(promote, release)
        self.assertLess(release, publish)
        self.assertIn('--backend-ref "$RELEASE_BACKEND@$BACKEND_DIGEST"', workflow)
        self.assertIn("--release-decision", workflow)
        self.assertIn("--scan-report", workflow)

    def test_typed_release_modes_use_shared_fail_closed_oidc_transport(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        trust_start = workflow.index("Validate GitHub OIDC source and workflow trust before any write")
        trust_end = workflow.index("Install cosign and ORAS by version plus protected checksum")
        trust_step = workflow[trust_start:trust_end]
        for event_name in ("push", "workflow_dispatch", "workflow_call"):
            self.assertIn(event_name, workflow)
        self.assertIn("ReleaseTrust.from_environment", trust_step)
        self.assertIn("validate_release_oidc_claims", trust_step)
        self.assertIn("fetch_github_oidc", trust_step)
        self.assertIn("UrllibTransport()", trust_step)
        self.assertEqual(
            workflow.count(
                'if [[ "$GITHUB_REPOSITORY" == "ne-tort/agentscale" && -z "$INPUT_WORKFLOW_REF" ]]'
            ),
            2,
        )
        self.assertNotIn("request.urlopen", trust_step)
        self.assertNotIn("from urllib import request", trust_step)
        self.assertIsNone(
            re.search(r"^\s*if:\s*(?:true|\$\{\{\s*true\s*\}\})", workflow, re.MULTILINE)
        )

    def test_no_registry_write_precedes_valid_oidc_and_source_context(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        release = workflow[workflow.index("  release:") :]
        trust = release.index("Validate GitHub OIDC source and workflow trust before any write")
        context = release.index('plugin_publication.py" validate-context')
        login = release.index("Login to GHCR only after trust validation")
        self.assertLess(trust, context)
        self.assertLess(context, login)
        for write in (
            "Build and push backend only to quarantine with buildx evidence",
            "Sign and attest quarantined backend by digest",
            "Build and push canonical OCI bundle only to quarantine",
            "Promote verified backend and bundle with all referrers by digest",
        ):
            self.assertLess(context, release.index(write))

    def test_callers_cannot_supply_source_id_or_capabilities(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        triggers = workflow[: workflow.index("\npermissions:")]
        self.assertNotIn("\n      source_id:", triggers)
        self.assertNotIn("\n      capabilities:", triggers)
        self.assertIn("PLUGIN_GRANTED_CAPABILITIES: ${{ vars.PLUGIN_GRANTED_CAPABILITIES", workflow)


if __name__ == "__main__":
    unittest.main()
