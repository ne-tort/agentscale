"""Fake-GitHub acceptance tests for least-privilege source authoring."""

from __future__ import annotations

import json
import logging
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from prodavan.infrastructure.plugins.sources import (
    ApplyPatchRequest,
    CreatePullRequestRequest,
    GitHubAPIError,
    GitHubClientClosed,
    GitHubConflictError,
    GitHubInstallationTokenProfile,
    GitHubOrganizationNotAllowed,
    GitHubResponseError,
    GitHubValidationError,
    PatchFile,
    RepositoryMetadata,
)

from .conftest import (
    DEFAULT_REPO_PAYLOAD,
    INSTALLATION_ID,
    ORGANIZATION,
    MutableClock,
)

BASE_SHA = "a" * 40
BASE_TREE_SHA = "b" * 40
NEW_TREE_SHA = "c" * 40
NEW_COMMIT_SHA = "d" * 40
REPOSITORY_PATH = f"/repos/{ORGANIZATION}/plugin-one"


def _repository(*, default_branch: str = "main", full_name: str | None = None) -> RepositoryMetadata:
    return RepositoryMetadata(
        repo_id=DEFAULT_REPO_PAYLOAD["id"],
        full_name=full_name or DEFAULT_REPO_PAYLOAD["full_name"],
        default_branch=default_branch,
        private=True,
    )


def _patch_request(
    *,
    branch: str = "agents/plugin-one/task-1",
    expected_head_sha: str = BASE_SHA,
    files: tuple[PatchFile, ...] | None = None,
) -> ApplyPatchRequest:
    return ApplyPatchRequest(
        branch=branch,
        expected_head_sha=expected_head_sha,
        commit_message="Apply reviewed agent patch",
        files=files
        or (
            PatchFile(path="AGENTS.md", operation="upsert", content="# Agent instructions\n"),
            PatchFile(path="obsolete.txt", operation="delete"),
        ),
    )


def _pull_request(*, base_branch: str = "main") -> CreatePullRequestRequest:
    return CreatePullRequestRequest(
        title="Review agent changes",
        head_branch="agents/plugin-one/task-1",
        base_branch=base_branch,
        body="Generated without Git credentials in the agent pod.",
    )


def _valid_pull_response() -> dict[str, Any]:
    return {
        "number": 17,
        "html_url": f"https://github.com/{ORGANIZATION}/plugin-one/pull/17",
        "head": {
            "ref": "agents/plugin-one/task-1",
            "sha": NEW_COMMIT_SHA,
            "repo": dict(DEFAULT_REPO_PAYLOAD),
        },
        "base": {
            "ref": "main",
            "sha": BASE_SHA,
            "repo": dict(DEFAULT_REPO_PAYLOAD),
        },
    }


class AuthoringGitHub:
    """Exact fake routes for installation tokens, Git Data, metadata and PRs."""

    def __init__(self, clock: MutableClock) -> None:
        self.clock = clock
        self.requests: list[httpx.Request] = []
        self.token_payloads: list[dict[str, Any]] = []
        self.profile_tokens: dict[str, list[str]] = {
            "provisioning": [],
            "authoring": [],
            "registry_read": [],
        }
        self.blob_payloads: list[dict[str, Any]] = []
        self.tree_payload: dict[str, Any] | None = None
        self.commit_payload: dict[str, Any] | None = None
        self.ref_payload: dict[str, Any] | None = None
        self.pull_payload: dict[str, Any] | None = None
        self.default_head_sha = BASE_SHA
        self.default_ref_status = 200
        self.blob_status = 201
        self.create_ref_status = 201
        self.pull_status = 201
        self.error_payload: dict[str, Any] = {"message": "synthetic failure"}
        self.pull_response: dict[str, Any] | None = None
        self.grant_extra_admin = False

    @property
    def calls(self) -> list[tuple[str, str]]:
        return [(request.method, request.url.path) for request in self.requests]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        method = request.method
        path = request.url.path

        if method == "POST" and path == f"/app/installations/{INSTALLATION_ID}/access_tokens":
            token_payload = json.loads(request.content)
            self.token_payloads.append(token_payload)
            permissions = dict(token_payload["permissions"])
            if "administration" in permissions:
                profile = "provisioning"
            elif "packages" in permissions:
                profile = "registry_read"
            else:
                profile = "authoring"
            token = f"ghs_{profile}_{len(self.profile_tokens[profile]) + 1:02d}"
            self.profile_tokens[profile].append(token)
            granted_permissions = dict(permissions)
            if self.grant_extra_admin and profile == "authoring":
                granted_permissions["administration"] = "write"
            expires_at = datetime.fromtimestamp(self.clock.now + 3600, tz=UTC)
            return httpx.Response(
                201,
                json={
                    "token": token,
                    "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
                    "permissions": granted_permissions,
                },
            )

        if method == "GET" and path == REPOSITORY_PATH:
            return httpx.Response(200, json=DEFAULT_REPO_PAYLOAD)

        if method == "GET" and path == f"{REPOSITORY_PATH}/git/ref/heads/main":
            if self.default_ref_status != 200:
                return httpx.Response(self.default_ref_status, json=self.error_payload)
            return httpx.Response(
                200,
                json={
                    "ref": "refs/heads/main",
                    "object": {"type": "commit", "sha": self.default_head_sha},
                },
            )

        if method == "GET" and path == f"{REPOSITORY_PATH}/git/commits/{BASE_SHA}":
            return httpx.Response(
                200,
                json={"sha": BASE_SHA, "tree": {"sha": BASE_TREE_SHA}},
            )

        if method == "POST" and path == f"{REPOSITORY_PATH}/git/blobs":
            if self.blob_status != 201:
                return httpx.Response(self.blob_status, json=self.error_payload)
            self.blob_payloads.append(json.loads(request.content))
            return httpx.Response(201, json={"sha": str(len(self.blob_payloads)) * 40})

        if method == "POST" and path == f"{REPOSITORY_PATH}/git/trees":
            self.tree_payload = json.loads(request.content)
            return httpx.Response(201, json={"sha": NEW_TREE_SHA})

        if method == "POST" and path == f"{REPOSITORY_PATH}/git/commits":
            self.commit_payload = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "sha": NEW_COMMIT_SHA,
                    "tree": {"sha": NEW_TREE_SHA},
                    "parents": [{"sha": self.commit_payload["parents"][0]}],
                },
            )

        if method == "POST" and path == f"{REPOSITORY_PATH}/git/refs":
            self.ref_payload = json.loads(request.content)
            if self.create_ref_status != 201:
                return httpx.Response(self.create_ref_status, json=self.error_payload)
            return httpx.Response(
                201,
                json={
                    "ref": self.ref_payload["ref"],
                    "object": {"type": "commit", "sha": self.ref_payload["sha"]},
                },
            )

        if method == "POST" and path == f"{REPOSITORY_PATH}/pulls":
            self.pull_payload = json.loads(request.content)
            if self.pull_status != 201:
                return httpx.Response(self.pull_status, json=self.error_payload)
            return httpx.Response(201, json=self.pull_response or _valid_pull_response())

        return httpx.Response(404, json={"message": "unrouted fake GitHub request"})


@pytest.mark.asyncio
async def test_apply_patch_uses_exact_git_data_sequence_and_delete_entries(
    make_client,
    clock: MutableClock,
) -> None:
    fake = AuthoringGitHub(clock)
    client = make_client(fake.handler, clock=clock)
    request = _patch_request(
        files=(
            PatchFile(path="AGENTS.md", operation="upsert", content="# Agent\n"),
            PatchFile(path="obsolete.txt", operation="delete"),
            PatchFile(path="docs/usage.md", operation="upsert", content="Use UTF-8: \u2713\n"),
        )
    )

    result = await client.apply_patch(_repository(), request)

    assert result.sha == NEW_COMMIT_SHA
    assert fake.calls == [
        ("POST", f"/app/installations/{INSTALLATION_ID}/access_tokens"),
        ("GET", f"{REPOSITORY_PATH}/git/ref/heads/main"),
        ("GET", f"{REPOSITORY_PATH}/git/commits/{BASE_SHA}"),
        ("POST", f"{REPOSITORY_PATH}/git/blobs"),
        ("POST", f"{REPOSITORY_PATH}/git/blobs"),
        ("POST", f"{REPOSITORY_PATH}/git/trees"),
        ("POST", f"{REPOSITORY_PATH}/git/commits"),
        ("POST", f"{REPOSITORY_PATH}/git/refs"),
    ]
    assert fake.token_payloads == [
        {
            "permissions": {
                "contents": "write",
                "pull_requests": "write",
                "metadata": "read",
            }
        }
    ]
    assert "administration" not in fake.token_payloads[0]["permissions"]
    assert fake.blob_payloads == [
        {"content": "# Agent\n", "encoding": "utf-8"},
        {"content": "Use UTF-8: \u2713\n", "encoding": "utf-8"},
    ]
    assert fake.tree_payload == {
        "base_tree": BASE_TREE_SHA,
        "tree": [
            {"path": "AGENTS.md", "mode": "100644", "type": "blob", "sha": "1" * 40},
            {"path": "obsolete.txt", "mode": "100644", "type": "blob", "sha": None},
            {"path": "docs/usage.md", "mode": "100644", "type": "blob", "sha": "2" * 40},
        ],
    }
    assert fake.commit_payload == {
        "message": "Apply reviewed agent patch",
        "tree": NEW_TREE_SHA,
        "parents": [BASE_SHA],
    }
    assert fake.ref_payload == {
        "ref": "refs/heads/agents/plugin-one/task-1",
        "sha": NEW_COMMIT_SHA,
    }
    assert "force" not in fake.ref_payload


@pytest.mark.asyncio
async def test_token_profiles_are_cached_independently_per_installation(
    make_client,
    clock: MutableClock,
) -> None:
    fake = AuthoringGitHub(clock)
    client = make_client(fake.handler, clock=clock)

    for _ in range(2):
        await client.get_repository_metadata(DEFAULT_REPO_PAYLOAD["full_name"])
        await client.create_pull_request(_repository(), _pull_request())
        registry_token = await client._installation_token(
            INSTALLATION_ID, GitHubInstallationTokenProfile.REGISTRY_READ
        )
        assert registry_token == fake.profile_tokens["registry_read"][0]

    assert fake.token_payloads == [
        {"permissions": {"administration": "write", "metadata": "read"}},
        {
            "permissions": {
                "contents": "write",
                "pull_requests": "write",
                "metadata": "read",
            }
        },
        {"permissions": {"packages": "read", "metadata": "read"}},
    ]
    assert len(fake.profile_tokens["provisioning"]) == 1
    assert len(fake.profile_tokens["authoring"]) == 1
    assert len(fake.profile_tokens["registry_read"]) == 1
    registry_permissions = fake.token_payloads[2]["permissions"]
    assert not {"administration", "contents", "pull_requests"} & registry_permissions.keys()
    assert set(client._token_cache) == {
        (INSTALLATION_ID, GitHubInstallationTokenProfile.PROVISIONING),
        (INSTALLATION_ID, GitHubInstallationTokenProfile.AUTHORING),
        (INSTALLATION_ID, GitHubInstallationTokenProfile.REGISTRY_READ),
    }
    metadata_request = next(
        request
        for request in fake.requests
        if request.method == "GET" and request.url.path == REPOSITORY_PATH
    )
    assert metadata_request.headers["Authorization"] == (
        f"Bearer {fake.profile_tokens['provisioning'][0]}"
    )
    pull_requests = [request for request in fake.requests if request.url.path == f"{REPOSITORY_PATH}/pulls"]
    assert all(
        request.headers["Authorization"] == f"Bearer {fake.profile_tokens['authoring'][0]}"
        for request in pull_requests
    )


@pytest.mark.asyncio
async def test_base_sha_mismatch_fails_before_any_git_write(
    make_client,
    clock: MutableClock,
) -> None:
    fake = AuthoringGitHub(clock)
    fake.default_head_sha = "f" * 40
    client = make_client(fake.handler, clock=clock)

    with pytest.raises(GitHubConflictError) as caught:
        await client.apply_patch(_repository(), _patch_request())

    assert caught.value.operation == "validate_patch_base"
    assert fake.calls == [
        ("POST", f"/app/installations/{INSTALLATION_ID}/access_tokens"),
        ("GET", f"{REPOSITORY_PATH}/git/ref/heads/main"),
    ]
    assert fake.blob_payloads == []
    assert fake.ref_payload is None


@pytest.mark.asyncio
async def test_unknown_default_base_fails_closed_before_git_writes(
    make_client,
    clock: MutableClock,
) -> None:
    fake = AuthoringGitHub(clock)
    fake.default_ref_status = 404
    client = make_client(fake.handler, clock=clock)

    with pytest.raises(GitHubAPIError) as caught:
        await client.apply_patch(_repository(), _patch_request())

    assert caught.value.status_code == 404
    assert fake.blob_payloads == []
    assert fake.tree_payload is None
    assert fake.ref_payload is None


@pytest.mark.asyncio
async def test_existing_agent_branch_conflict_never_updates_or_forces_ref(
    make_client,
    clock: MutableClock,
) -> None:
    fake = AuthoringGitHub(clock)
    fake.create_ref_status = 422
    client = make_client(fake.handler, clock=clock)

    with pytest.raises(GitHubConflictError) as caught:
        await client.apply_patch(_repository(), _patch_request())

    assert caught.value.operation == "create_agent_ref"
    assert fake.ref_payload == {
        "ref": "refs/heads/agents/plugin-one/task-1",
        "sha": NEW_COMMIT_SHA,
    }
    assert "force" not in fake.ref_payload
    assert not any(method in {"PATCH", "PUT", "DELETE"} for method, _ in fake.calls)


@pytest.mark.parametrize(
    "branch",
    [
        "main",
        "agents/plugin-one",
        "agents/plugin-one/task/extra",
        "agents/*/task",
        "agents/plugin-one/*",
        "agents/../task",
        "/agents/plugin-one/task",
        "agents/plugin-one/../task",
        "refs/heads/agents/plugin-one/task",
        "agents/plugin-one/" + "a" * 65,
    ],
)
def test_patch_branch_grammar_is_exact_and_bounded(branch: str) -> None:
    with pytest.raises(GitHubValidationError):
        _patch_request(branch=branch)


@pytest.mark.asyncio
async def test_patch_cannot_target_an_agent_shaped_default_branch(
    make_client,
    clock: MutableClock,
) -> None:
    fake = AuthoringGitHub(clock)
    client = make_client(fake.handler, clock=clock)
    branch = "agents/plugin-one/task-1"

    with pytest.raises(GitHubValidationError):
        await client.apply_patch(
            _repository(default_branch=branch), _patch_request(branch=branch)
        )
    assert fake.requests == []


@pytest.mark.parametrize(
    "path",
    [
        "/absolute",
        "C:/absolute",
        "C:drive-relative",
        "\\\\server\\share",
        "../escape",
        "safe/../../escape",
        "safe//file",
        "safe/./file",
        ".git/config",
        "nested/.GIT/config",
    ],
)
def test_patch_paths_reject_traversal_absolute_and_git_internals(path: str) -> None:
    with pytest.raises(GitHubValidationError):
        PatchFile(path=path, operation="upsert", content="x")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/publish.yml",
        ".GITHUB/WORKFLOWS/publish.yml",
        ".github/workflows/nested/config.txt",
    ],
)
async def test_workflow_changes_require_platform_owner_flag(
    make_client,
    clock: MutableClock,
    path: str,
) -> None:
    fake = AuthoringGitHub(clock)
    client = make_client(fake.handler, clock=clock)
    request = _patch_request(
        files=(PatchFile(path=path, operation="upsert", content="name: unsafe\n"),)
    )

    with pytest.raises(GitHubValidationError):
        await client.apply_patch(_repository(), request)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_platform_owner_flag_does_not_add_admin_to_authoring_token(
    make_client,
    clock: MutableClock,
) -> None:
    fake = AuthoringGitHub(clock)
    client = make_client(
        fake.handler,
        clock=clock,
        platform_owner_allow_workflow_changes=True,
    )
    request = _patch_request(
        files=(
            PatchFile(
                path=".github/workflows/reviewed.yml",
                operation="upsert",
                content="name: reviewed\n",
            ),
        )
    )

    result = await client.apply_patch(_repository(), request)

    assert result.sha == NEW_COMMIT_SHA
    assert fake.token_payloads[0]["permissions"] == {
        "contents": "write",
        "pull_requests": "write",
        "metadata": "read",
    }


@pytest.mark.asyncio
async def test_broader_than_requested_authoring_token_is_rejected(
    make_client,
    clock: MutableClock,
) -> None:
    fake = AuthoringGitHub(clock)
    fake.grant_extra_admin = True
    client = make_client(fake.handler, clock=clock)

    with pytest.raises(GitHubResponseError) as caught:
        await client.apply_patch(_repository(), _patch_request())

    assert caught.value.operation == "create_installation_token"
    assert fake.calls == [("POST", f"/app/installations/{INSTALLATION_ID}/access_tokens")]
    assert client._token_cache == {}


def test_patch_size_count_and_typed_text_bounds() -> None:
    with pytest.raises(GitHubValidationError):
        PatchFile(path="large.txt", operation="upsert", content="x" * (1_048_577))
    with pytest.raises(GitHubValidationError):
        PatchFile(path="binary", operation="upsert", content=b"not text")  # type: ignore[arg-type]
    with pytest.raises(GitHubValidationError):
        _patch_request(
            files=tuple(
                PatchFile(path=f"large-{index}.txt", operation="upsert", content="x" * 1_048_576)
                for index in range(5)
            )
        )
    with pytest.raises(GitHubValidationError):
        _patch_request(
            files=tuple(
                PatchFile(path=f"delete-{index}.txt", operation="delete")
                for index in range(101)
            )
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "x" * 257},
        {"title": "multiline\ntitle"},
        {"body": "x" * 65_537},
        {"body": "NUL\x00body"},
    ],
)
def test_pull_request_title_and_body_are_bounded(changes: dict[str, str]) -> None:
    with pytest.raises(GitHubValidationError):
        replace(_pull_request(), **changes)


@pytest.mark.asyncio
async def test_create_pull_request_payload_and_immutable_sha_result(
    make_client,
    clock: MutableClock,
) -> None:
    fake = AuthoringGitHub(clock)
    client = make_client(fake.handler, clock=clock)

    result = await client.create_pull_request(_repository(), _pull_request())

    assert fake.pull_payload == {
        "title": "Review agent changes",
        "head": "agents/plugin-one/task-1",
        "base": "main",
        "body": "Generated without Git credentials in the agent pod.",
    }
    assert result.number == 17
    assert result.url == f"https://github.com/{ORGANIZATION}/plugin-one/pull/17"
    assert result.head_branch == "agents/plugin-one/task-1"
    assert result.base_branch == "main"
    assert result.head_sha == NEW_COMMIT_SHA
    assert result.base_sha == BASE_SHA
    assert fake.token_payloads[0]["permissions"] == {
        "contents": "write",
        "pull_requests": "write",
        "metadata": "read",
    }
    with pytest.raises(FrozenInstanceError):
        result.number = 18


@pytest.mark.asyncio
async def test_pull_request_base_must_be_default_and_owner_stays_managed(
    make_client,
    clock: MutableClock,
) -> None:
    fake = AuthoringGitHub(clock)
    client = make_client(fake.handler, clock=clock)

    with pytest.raises(GitHubValidationError):
        await client.create_pull_request(
            _repository(), _pull_request(base_branch="release")
        )
    with pytest.raises(GitHubOrganizationNotAllowed):
        await client.create_pull_request(
            _repository(full_name="unmanaged/plugin-one"), _pull_request()
        )
    with pytest.raises(GitHubOrganizationNotAllowed):
        await client.apply_patch(
            _repository(full_name="unmanaged/plugin-one"), _patch_request()
        )
    assert fake.requests == []


@pytest.mark.asyncio
async def test_pull_request_response_identity_is_fail_closed(
    make_client,
    clock: MutableClock,
) -> None:
    fake = AuthoringGitHub(clock)
    client = make_client(fake.handler, clock=clock)
    external_url = _valid_pull_response()
    external_url["html_url"] = "https://attacker.invalid/managed-plugins/plugin-one/pull/17"
    wrong_head = _valid_pull_response()
    wrong_head["head"]["ref"] = "agents/plugin-one/other"
    wrong_base_repository = _valid_pull_response()
    wrong_base_repository["base"]["repo"]["full_name"] = "attacker/plugin-one"
    invalid_sha = _valid_pull_response()
    invalid_sha["head"]["sha"] = "not-a-sha"

    for response in (external_url, wrong_head, wrong_base_repository, invalid_sha):
        fake.pull_response = response
        with pytest.raises(GitHubResponseError) as caught:
            await client.create_pull_request(_repository(), _pull_request())
        assert caught.value.operation == "create_pull_request"


@pytest.mark.asyncio
async def test_authoring_api_errors_hide_tokens_private_keys_and_payloads(
    make_client,
    clock: MutableClock,
    caplog: pytest.LogCaptureFixture,
    private_key_pem: bytes,
) -> None:
    payload_marker = "source-content-secret-marker"
    private_key_marker = private_key_pem.decode()
    fake = AuthoringGitHub(clock)
    fake.pull_status = 422
    fake.error_payload = {
        "message": payload_marker,
        "private_key": private_key_marker,
        "request_token": "must-not-be-logged",
    }
    client = make_client(fake.handler, clock=clock)

    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        with pytest.raises(GitHubAPIError) as caught:
            await client.create_pull_request(_repository(), _pull_request())

    authoring_token = fake.profile_tokens["authoring"][0]
    error_text = f"{caught.value} / {caught.value!r}"
    for marker in (payload_marker, private_key_marker, authoring_token, "must-not-be-logged"):
        assert marker not in error_text
        assert marker not in caplog.text
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_patch_blob_error_is_sanitized_and_stops_before_tree(
    make_client,
    clock: MutableClock,
) -> None:
    secret_content = "secret-source-content"
    fake = AuthoringGitHub(clock)
    fake.blob_status = 422
    fake.error_payload = {"message": secret_content, "documentation_url": "https://invalid"}
    client = make_client(fake.handler, clock=clock)
    request = _patch_request(
        files=(PatchFile(path="secret.txt", operation="upsert", content=secret_content),)
    )

    with pytest.raises(GitHubAPIError) as caught:
        await client.apply_patch(_repository(), request)

    assert secret_content not in str(caught.value)
    assert "documentation_url" not in str(caught.value)
    assert fake.tree_payload is None
    assert fake.commit_payload is None
    assert fake.ref_payload is None


@pytest.mark.asyncio
async def test_close_clears_profiles_and_rejects_authoring_operations(
    make_client,
    clock: MutableClock,
) -> None:
    fake = AuthoringGitHub(clock)
    client = make_client(fake.handler, clock=clock)
    await client.create_pull_request(_repository(), _pull_request())
    assert client._token_cache
    request_count = len(fake.requests)

    await client.aclose()

    with pytest.raises(GitHubClientClosed):
        await client.apply_patch(_repository(), _patch_request())
    with pytest.raises(GitHubClientClosed):
        await client.create_pull_request(_repository(), _pull_request())
    assert len(fake.requests) == request_count
    assert client._token_cache == {}
    assert client._refresh_tasks == {}
    await client.aclose()
