"""Async GitHub App client for managed repository provisioning and authoring.

Security boundaries:

* private-key material exists only behind an injected async secret provider;
* App JWTs are RS256, short-lived and used only for installation-token minting;
* installation tokens are cached in memory with server TTL and refresh skew;
* HTTP uses no environment proxies, redirects, unbounded bodies or raw errors;
* every repository owner is resolved through an explicit organization config.

The generate API cannot accept SHA pins: the separate template-publication
pipeline must materialize configured pins in the template default branch before
provisioning. Branch protection and OIDC trust are configured and verified
independently; neither patch/PR authoring nor branch settings establish them.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from .errors import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClientClosed,
    GitHubConfigurationError,
    GitHubConflictError,
    GitHubOrganizationNotAllowed,
    GitHubResponseError,
    GitHubResponseTooLarge,
    GitHubSourceError,
    GitHubTransportError,
    GitHubValidationError,
)
from .models import (
    ApplyPatchRequest,
    BranchProtectionPolicy,
    CommitMetadata,
    CreatePrivateRepositoryRequest,
    CreatePullRequestRequest,
    CreatedRepository,
    GitHubInstallationTokenProfile,
    ManagedOrganization,
    PullRequestMetadata,
    RepositoryMetadata,
    RepositoryTemplate,
    is_github_workflow_path,
    split_full_name,
    validate_commit,
    validate_secret_reference,
)
from .ports import (
    GitHubAppSecretProvider,
    GitHubPatchPort,
    GitHubPullRequestPort,
    GitHubRepositorySourcePort,
)

_API_ROOT = "https://api.github.com"
_API_VERSION = "2022-11-28"
MAX_RESPONSE_BYTES = 1_048_576
_TOKEN_REFRESH_SKEW_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class _CachedInstallationToken:
    value: str = field(repr=False)
    expires_at: float


class GitHubAppClient(
    GitHubRepositorySourcePort, GitHubPatchPort, GitHubPullRequestPort
):
    """Manage repositories only in explicitly managed organizations.

    ``organizations`` is trusted, non-secret source configuration. Request input
    selects an entry but can never supply another owner or installation id.
    The optional ``transport`` and ``clock`` are deterministic test seams; the
    HTTP client itself is always constructed with production-safe options.
    """

    def __init__(
        self,
        *,
        app_id: int,
        private_key_secret_ref: str,
        secret_provider: GitHubAppSecretProvider,
        organizations: Iterable[ManagedOrganization],
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        timeout_seconds: float = 10.0,
        platform_owner_allow_workflow_changes: bool = False,
    ) -> None:
        if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id <= 0:
            raise GitHubConfigurationError("app_id must be a positive integer")
        self._app_id = app_id
        self._private_key_secret_ref = validate_secret_reference(private_key_secret_ref)
        if not callable(getattr(secret_provider, "read_secret", None)):
            raise GitHubConfigurationError("secret_provider must implement async read_secret")
        self._secret_provider = secret_provider
        if not callable(clock):
            raise GitHubConfigurationError("clock must be callable")
        self._clock = clock
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            raise GitHubConfigurationError("timeout_seconds must be a finite positive number") from None
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise GitHubConfigurationError("timeout_seconds must be in the (0, 60] range")
        if not isinstance(platform_owner_allow_workflow_changes, bool):
            raise GitHubConfigurationError("platform_owner_allow_workflow_changes must be a boolean")
        self._platform_owner_allow_workflow_changes = platform_owner_allow_workflow_changes

        managed: dict[str, ManagedOrganization] = {}
        for organization in organizations:
            if not isinstance(organization, ManagedOrganization):
                raise GitHubConfigurationError("organizations must contain ManagedOrganization entries")
            key = organization.name.casefold()
            if key in managed:
                raise GitHubConfigurationError("managed organization names must be unique case-insensitively")
            managed[key] = organization
        if not managed:
            raise GitHubConfigurationError("at least one managed organization is required")
        self._organizations = managed

        self._http = httpx.AsyncClient(
            base_url=_API_ROOT,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(
                connect=min(5.0, timeout),
                read=timeout,
                write=timeout,
                pool=min(5.0, timeout),
            ),
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "agentscale-source-provider",
                "X-GitHub-Api-Version": _API_VERSION,
            },
        )
        self._state_lock = asyncio.Lock()
        self._closed = False
        self._token_cache: dict[
            tuple[int, GitHubInstallationTokenProfile], _CachedInstallationToken
        ] = {}
        self._refresh_tasks: dict[
            tuple[int, GitHubInstallationTokenProfile], asyncio.Task[str]
        ] = {}

    async def __aenter__(self) -> GitHubAppClient:
        self._ensure_open()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    async def create_private_repository(
        self, request: CreatePrivateRepositoryRequest
    ) -> CreatedRepository:
        """Create from a verified private template using the official generate API.

        No seed commit is used: template generation is server-side and the
        provisioning token has no contents-write permission. Authoring tokens
        are minted separately only for explicit patch/PR operations.
        """

        if not isinstance(request, CreatePrivateRepositoryRequest):
            raise TypeError("request must be CreatePrivateRepositoryRequest")
        self._ensure_open()
        organization = self._organization(request.organization)
        template = organization.template
        token = await self._installation_token(
            organization.installation_id, GitHubInstallationTokenProfile.PROVISIONING
        )

        template_body = await self._request_json(
            "GET",
            self._repository_path(template.owner, template.name),
            credential=token,
            expected_status=200,
            operation="read_private_template",
        )
        self._validate_template_response(template_body, template)

        payload: dict[str, Any] = {
            "owner": organization.name,
            "name": request.name,
            "private": True,
            "include_all_branches": False,
        }
        if request.description is not None:
            payload["description"] = request.description
        body = await self._request_json(
            "POST",
            f"{self._repository_path(template.owner, template.name)}/generate",
            credential=token,
            expected_status=201,
            operation="create_private_repository",
            payload=payload,
        )
        expected_full_name = f"{organization.name}/{request.name}"
        metadata = self._repository_from_response(
            body,
            operation="create_private_repository",
            expected_full_name=expected_full_name,
            require_private=True,
        )
        return CreatedRepository(
            repo_id=metadata.repo_id,
            full_name=metadata.full_name,
            default_branch=metadata.default_branch,
            private=metadata.private,
            template=template,
        )

    async def get_repository_metadata(self, full_name: str) -> RepositoryMetadata:
        """Read repository identity only through a managed-owner installation."""

        self._ensure_open()
        owner, repository_name = split_full_name(full_name)
        organization = self._organization(owner)
        token = await self._installation_token(
            organization.installation_id, GitHubInstallationTokenProfile.PROVISIONING
        )
        body = await self._request_json(
            "GET",
            self._repository_path(organization.name, repository_name),
            credential=token,
            expected_status=200,
            operation="get_repository_metadata",
        )
        return self._repository_from_response(
            body,
            operation="get_repository_metadata",
            expected_full_name=f"{organization.name}/{repository_name}",
            require_private=False,
        )

    async def protect_default_branch(
        self, repository: RepositoryMetadata, policy: BranchProtectionPolicy
    ) -> None:
        """Require reviewed, checked linear merges on the exact default branch.

        Empty bypass lists and ``restrictions=None`` remove prior user/team/app
        exceptions. Required PR reviews apply to the GitHub App as well, while
        ``enforce_admins`` prevents an administrator bypass. No wildcard rule is
        installed for ``agents/<project>/*``: those working branches remain
        creatable only through the patch port's strict branch/base policy.
        """

        if not isinstance(repository, RepositoryMetadata):
            raise TypeError("repository must be RepositoryMetadata")
        if not isinstance(policy, BranchProtectionPolicy):
            raise TypeError("policy must be BranchProtectionPolicy")
        self._ensure_open()
        owner, repository_name = split_full_name(repository.full_name)
        organization = self._organization(owner)
        token = await self._installation_token(
            organization.installation_id, GitHubInstallationTokenProfile.PROVISIONING
        )
        encoded_branch = quote(repository.default_branch, safe="")
        path = (
            f"{self._repository_path(organization.name, repository_name)}"
            f"/branches/{encoded_branch}/protection"
        )
        await self._request_json(
            "PUT",
            path,
            credential=token,
            expected_status=200,
            operation="protect_default_branch",
            payload=self._branch_protection_payload(policy),
        )

    async def apply_patch(
        self, repository: RepositoryMetadata, request: ApplyPatchRequest
    ) -> CommitMetadata:
        """Create a new agent branch through Git Data, pinned to the default head.

        Blobs, one tree and one commit may become unreachable if the final ref
        creation fails, but no partial branch becomes visible. Existing refs are
        never updated, so a name/base conflict fails closed without force-push.
        """

        if not isinstance(repository, RepositoryMetadata):
            raise TypeError("repository must be RepositoryMetadata")
        if not isinstance(request, ApplyPatchRequest):
            raise TypeError("request must be ApplyPatchRequest")
        self._ensure_open()
        if request.branch == repository.default_branch:
            raise GitHubValidationError("an agent patch cannot target the repository default branch")
        if not self._platform_owner_allow_workflow_changes and any(
            is_github_workflow_path(item.path) for item in request.files
        ):
            raise GitHubValidationError(
                "workflow changes require the separate platform-owner configuration flag"
            )

        owner, repository_name = split_full_name(repository.full_name)
        organization = self._organization(owner)
        token = await self._installation_token(
            organization.installation_id, GitHubInstallationTokenProfile.AUTHORING
        )
        repository_path = self._repository_path(organization.name, repository_name)

        default_ref = f"refs/heads/{repository.default_branch}"
        default_ref_body = await self._request_json(
            "GET",
            f"{repository_path}/git/ref/heads/{quote(repository.default_branch, safe='')}",
            credential=token,
            expected_status=200,
            operation="read_patch_default_ref",
        )
        actual_base_sha = self._commit_sha_from_ref(
            default_ref_body,
            expected_ref=default_ref,
            operation="read_patch_default_ref",
        )
        if actual_base_sha != request.expected_head_sha:
            raise GitHubConflictError("validate_patch_base")

        base_commit_body = await self._request_json(
            "GET",
            f"{repository_path}/git/commits/{request.expected_head_sha}",
            credential=token,
            expected_status=200,
            operation="read_patch_base_commit",
        )
        base_tree_sha = self._base_tree_sha(
            base_commit_body,
            expected_commit_sha=request.expected_head_sha,
            operation="read_patch_base_commit",
        )

        blob_shas: dict[str, str] = {}
        for item in request.files:
            if item.operation == "delete":
                continue
            if item.content is None:
                raise GitHubValidationError("upsert entries require text content")
            blob_body = await self._request_json(
                "POST",
                f"{repository_path}/git/blobs",
                credential=token,
                expected_status=201,
                operation="create_patch_blob",
                payload={"content": item.content, "encoding": "utf-8"},
            )
            blob_shas[item.path] = self._object_sha(blob_body, operation="create_patch_blob")

        tree_entries: list[dict[str, Any]] = []
        for item in request.files:
            tree_entries.append(
                {
                    "path": item.path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_shas[item.path] if item.operation == "upsert" else None,
                }
            )
        tree_body = await self._request_json(
            "POST",
            f"{repository_path}/git/trees",
            credential=token,
            expected_status=201,
            operation="create_patch_tree",
            payload={"base_tree": base_tree_sha, "tree": tree_entries},
        )
        new_tree_sha = self._object_sha(tree_body, operation="create_patch_tree")

        commit_body = await self._request_json(
            "POST",
            f"{repository_path}/git/commits",
            credential=token,
            expected_status=201,
            operation="create_patch_commit",
            payload={
                "message": request.commit_message,
                "tree": new_tree_sha,
                "parents": [request.expected_head_sha],
            },
        )
        new_commit_sha = self._new_commit_sha(
            commit_body,
            expected_parent_sha=request.expected_head_sha,
            expected_tree_sha=new_tree_sha,
            operation="create_patch_commit",
        )

        agent_ref = f"refs/heads/{request.branch}"
        try:
            ref_body = await self._request_json(
                "POST",
                f"{repository_path}/git/refs",
                credential=token,
                expected_status=201,
                operation="create_agent_ref",
                payload={"ref": agent_ref, "sha": new_commit_sha},
            )
        except GitHubAPIError as error:
            if error.status_code in {409, 422}:
                raise GitHubConflictError("create_agent_ref") from None
            raise
        created_sha = self._commit_sha_from_ref(
            ref_body,
            expected_ref=agent_ref,
            operation="create_agent_ref",
        )
        if created_sha != new_commit_sha:
            raise GitHubResponseError("create_agent_ref")
        return CommitMetadata(sha=new_commit_sha)

    async def create_pull_request(
        self, repository: RepositoryMetadata, request: CreatePullRequestRequest
    ) -> PullRequestMetadata:
        """Open a same-repository PR from an agent branch to the default branch."""

        if not isinstance(repository, RepositoryMetadata):
            raise TypeError("repository must be RepositoryMetadata")
        if not isinstance(request, CreatePullRequestRequest):
            raise TypeError("request must be CreatePullRequestRequest")
        self._ensure_open()
        if request.base_branch != repository.default_branch:
            raise GitHubValidationError("pull request base must be the repository default branch")

        owner, repository_name = split_full_name(repository.full_name)
        organization = self._organization(owner)
        token = await self._installation_token(
            organization.installation_id, GitHubInstallationTokenProfile.AUTHORING
        )
        body = await self._request_json(
            "POST",
            f"{self._repository_path(organization.name, repository_name)}/pulls",
            credential=token,
            expected_status=201,
            operation="create_pull_request",
            payload={
                "title": request.title,
                "head": request.head_branch,
                "base": repository.default_branch,
                "body": request.body,
            },
        )
        return self._pull_request_from_response(
            body,
            expected_full_name=f"{organization.name}/{repository_name}",
            expected_head=request.head_branch,
            expected_base=repository.default_branch,
        )

    async def aclose(self) -> None:
        """Cancel in-flight token mints, drop cached tokens and close HTTP."""

        async with self._state_lock:
            self._closed = True
            self._token_cache.clear()
            tasks = tuple(self._refresh_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._http.aclose()

    def _ensure_open(self) -> None:
        if self._closed:
            raise GitHubClientClosed("GitHub App client is closed")

    def _organization(self, name: str) -> ManagedOrganization:
        organization = self._organizations.get(name.casefold())
        if organization is None:
            raise GitHubOrganizationNotAllowed("organization is not in the managed allowlist")
        return organization

    def _now(self) -> float:
        try:
            value = float(self._clock())
        except Exception:
            raise GitHubSourceError("GitHub App clock failed") from None
        if not math.isfinite(value):
            raise GitHubSourceError("GitHub App clock returned a non-finite value")
        return value

    async def installation_token(
        self, installation_id: int, *, profile: str
    ) -> str:
        """Mint through the shared TTL/singleflight path for trusted composition.

        Callers such as the GHCR authorizer receive ``installation_id`` only
        from ``ManagedOrganization``; it is never request or endpoint input.
        """
        typed_profile = GitHubInstallationTokenProfile(profile)
        return await self._installation_token(installation_id, typed_profile)

    async def _installation_token(
        self, installation_id: int, profile: GitHubInstallationTokenProfile
    ) -> str:
        self._ensure_open()
        if not isinstance(profile, GitHubInstallationTokenProfile):
            raise GitHubConfigurationError("a typed installation token profile is required")
        key = (installation_id, profile)
        now = self._now()
        cached = self._token_cache.get(key)
        if cached is not None and now < cached.expires_at - _TOKEN_REFRESH_SKEW_SECONDS:
            return cached.value

        async with self._state_lock:
            self._ensure_open()
            now = self._now()
            cached = self._token_cache.get(key)
            if cached is not None and now < cached.expires_at - _TOKEN_REFRESH_SKEW_SECONDS:
                return cached.value
            task = self._refresh_tasks.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._mint_installation_token(installation_id, profile),
                    name=f"github-app-installation-token-{profile.value}-{installation_id}",
                )
                self._refresh_tasks[key] = task
                task.add_done_callback(
                    lambda completed, cache_key=key: self._refresh_finished(cache_key, completed)
                )
        # A caller cancelling its own request must not cancel a shared mint;
        # aclose() explicitly cancels the underlying task for every waiter.
        return await asyncio.shield(task)

    def _refresh_finished(
        self,
        key: tuple[int, GitHubInstallationTokenProfile],
        task: asyncio.Task[str],
    ) -> None:
        if self._refresh_tasks.get(key) is task:
            self._refresh_tasks.pop(key, None)
        if not task.cancelled():
            # Mark exceptions retrieved even if every waiter was cancelled.
            task.exception()

    async def _mint_installation_token(
        self, installation_id: int, profile: GitHubInstallationTokenProfile
    ) -> str:
        if not isinstance(profile, GitHubInstallationTokenProfile):
            raise GitHubConfigurationError("a typed installation token profile is required")
        permissions = profile.permissions
        app_jwt = await self._create_app_jwt()
        body = await self._request_json(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            credential=app_jwt,
            expected_status=201,
            operation="create_installation_token",
            payload={"permissions": permissions},
        )
        token = body.get("token")
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 8192
            or any(character.isspace() for character in token)
        ):
            raise GitHubResponseError("create_installation_token")
        # Installation tokens cannot exceed App permissions. Requiring the exact
        # granted set also prevents accidental reuse of a broader credential.
        if body.get("permissions") != permissions:
            raise GitHubResponseError("create_installation_token")
        expires_at = self._token_expiry(body.get("expires_at"))
        async with self._state_lock:
            self._ensure_open()
            self._token_cache[(installation_id, profile)] = _CachedInstallationToken(
                token, expires_at
            )
        return token

    def _token_expiry(self, raw_value: object) -> float:
        if not isinstance(raw_value, str) or len(raw_value) > 64:
            raise GitHubResponseError("create_installation_token")
        expires_at: float | None = None
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("timezone required")
            expires_at = parsed.timestamp()
        except (ValueError, OverflowError):
            expires_at = None
        if expires_at is None or not math.isfinite(expires_at) or expires_at <= self._now():
            raise GitHubResponseError("create_installation_token")
        return expires_at

    async def _create_app_jwt(self) -> str:
        now = int(self._now())
        encoded: str | None = None
        try:
            private_key = await self._secret_provider.read_secret(self._private_key_secret_ref)
            if isinstance(private_key, str):
                if not private_key.strip():
                    raise ValueError("empty private key")
            elif isinstance(private_key, bytes):
                if not private_key.strip():
                    raise ValueError("empty private key")
            else:
                raise TypeError("private key must be PEM text or bytes")
            encoded = jwt.encode(
                {"iat": now - 60, "exp": now + 540, "iss": str(self._app_id)},
                private_key,
                algorithm="RS256",
                headers={"typ": "JWT"},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Raise only after leaving this handler so the original exception,
            # including any key-bearing message/context, is fully detached.
            encoded = None
        if not isinstance(encoded, str) or not encoded:
            raise GitHubAuthenticationError("could not create GitHub App JWT")
        return encoded

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        credential: str,
        expected_status: int,
        operation: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_open()
        request_kwargs: dict[str, Any] = {}
        if payload is not None:
            request_kwargs["json"] = payload
        transport_failure: str | None = None
        try:
            async with self._http.stream(
                method,
                path,
                headers={"Authorization": f"Bearer {credential}"},
                **request_kwargs,
            ) as response:
                if response.status_code != expected_status:
                    raise GitHubAPIError(operation, response.status_code)
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError:
                        declared_length = -1
                    if declared_length > MAX_RESPONSE_BYTES:
                        raise GitHubResponseTooLarge(operation)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise GitHubResponseTooLarge(operation)
                    body.extend(chunk)
        except httpx.TimeoutException:
            transport_failure = "timed out"
        except httpx.HTTPError:
            transport_failure = "transport failed"
        if transport_failure is not None:
            raise GitHubTransportError(f"GitHub operation {operation!r} {transport_failure}")

        decoded: Any = None
        invalid_json = False
        try:
            decoded = json.loads(bytes(body))
        except (json.JSONDecodeError, UnicodeDecodeError):
            invalid_json = True
        if invalid_json:
            raise GitHubResponseError(operation)
        if not isinstance(decoded, dict):
            raise GitHubResponseError(operation)
        return decoded

    @staticmethod
    def _object_sha(body: dict[str, Any], *, operation: str) -> str:
        try:
            return validate_commit(body["sha"], field_name="response.sha")
        except (KeyError, TypeError, GitHubValidationError):
            raise GitHubResponseError(operation) from None

    @staticmethod
    def _commit_sha_from_ref(
        body: dict[str, Any], *, expected_ref: str, operation: str
    ) -> str:
        if body.get("ref") != expected_ref:
            raise GitHubResponseError(operation)
        git_object = body.get("object")
        if not isinstance(git_object, dict) or git_object.get("type") != "commit":
            raise GitHubResponseError(operation)
        return GitHubAppClient._object_sha(git_object, operation=operation)

    @staticmethod
    def _base_tree_sha(
        body: dict[str, Any], *, expected_commit_sha: str, operation: str
    ) -> str:
        if body.get("sha") != expected_commit_sha:
            raise GitHubResponseError(operation)
        tree = body.get("tree")
        if not isinstance(tree, dict):
            raise GitHubResponseError(operation)
        return GitHubAppClient._object_sha(tree, operation=operation)

    @staticmethod
    def _new_commit_sha(
        body: dict[str, Any],
        *,
        expected_parent_sha: str,
        expected_tree_sha: str,
        operation: str,
    ) -> str:
        commit_sha = GitHubAppClient._object_sha(body, operation=operation)
        parents = body.get("parents")
        tree = body.get("tree")
        if (
            not isinstance(parents, list)
            or len(parents) != 1
            or not isinstance(parents[0], dict)
            or parents[0].get("sha") != expected_parent_sha
            or not isinstance(tree, dict)
            or tree.get("sha") != expected_tree_sha
        ):
            raise GitHubResponseError(operation)
        return commit_sha

    @staticmethod
    def _pull_request_from_response(
        body: dict[str, Any],
        *,
        expected_full_name: str,
        expected_head: str,
        expected_base: str,
    ) -> PullRequestMetadata:
        operation = "create_pull_request"
        head = body.get("head")
        base = body.get("base")
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise GitHubResponseError(operation)
        head_repository = head.get("repo")
        base_repository = base.get("repo")
        if not isinstance(head_repository, dict) or not isinstance(base_repository, dict):
            raise GitHubResponseError(operation)
        head_full_name = head_repository.get("full_name")
        base_full_name = base_repository.get("full_name")
        if (
            head.get("ref") != expected_head
            or base.get("ref") != expected_base
            or not isinstance(head_full_name, str)
            or not isinstance(base_full_name, str)
            or head_full_name.casefold() != expected_full_name.casefold()
            or base_full_name.casefold() != expected_full_name.casefold()
        ):
            raise GitHubResponseError(operation)
        try:
            metadata = PullRequestMetadata(
                number=body["number"],
                url=body["html_url"],
                head_branch=head["ref"],
                base_branch=base["ref"],
                head_sha=head["sha"],
                base_sha=base["sha"],
            )
        except (KeyError, TypeError, GitHubValidationError):
            raise GitHubResponseError(operation) from None
        expected_url = f"https://github.com/{expected_full_name}/pull/{metadata.number}"
        if metadata.url.casefold() != expected_url.casefold():
            raise GitHubResponseError(operation)
        return metadata

    @staticmethod
    def _repository_path(owner: str, repository_name: str) -> str:
        # Both components passed strict GitHub-name validation before reaching
        # this helper, so they cannot alter path segments or query parameters.
        return f"/repos/{owner}/{repository_name}"

    @staticmethod
    def _validate_template_response(body: dict[str, Any], template: RepositoryTemplate) -> None:
        expected = f"{template.owner}/{template.name}"
        if (
            body.get("private") is not True
            or body.get("is_template") is not True
            or not isinstance(body.get("full_name"), str)
            or body["full_name"].casefold() != expected.casefold()
        ):
            raise GitHubResponseError("read_private_template")

    @staticmethod
    def _repository_from_response(
        body: dict[str, Any],
        *,
        operation: str,
        expected_full_name: str,
        require_private: bool,
    ) -> RepositoryMetadata:
        try:
            metadata = RepositoryMetadata(
                repo_id=body["id"],
                full_name=body["full_name"],
                default_branch=body["default_branch"],
                private=body["private"],
            )
        except (KeyError, TypeError, GitHubValidationError):
            raise GitHubResponseError(operation) from None
        if metadata.full_name.casefold() != expected_full_name.casefold():
            raise GitHubResponseError(operation)
        if require_private and metadata.private is not True:
            raise GitHubResponseError(operation)
        return metadata

    @staticmethod
    def _branch_protection_payload(policy: BranchProtectionPolicy) -> dict[str, Any]:
        return {
            "required_status_checks": {
                "strict": True,
                "contexts": list(policy.required_status_checks),
            },
            "enforce_admins": True,
            "required_pull_request_reviews": {
                "dismiss_stale_reviews": True,
                "require_code_owner_reviews": True,
                "required_approving_review_count": policy.required_approving_review_count,
                "require_last_push_approval": True,
                "bypass_pull_request_allowances": {
                    "users": [],
                    "teams": [],
                    "apps": [],
                },
            },
            # Clear legacy push allowlists, including GitHub App entries.
            "restrictions": None,
            "required_linear_history": True,
            "allow_force_pushes": False,
            "allow_deletions": False,
            "block_creations": False,
            "required_conversation_resolution": True,
            "lock_branch": False,
            "allow_fork_syncing": False,
        }


__all__ = ["GitHubAppClient"]
