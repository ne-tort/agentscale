"""Async ports for managed repository provisioning and authoring."""

from __future__ import annotations

from typing import Protocol

from .models import (
    ApplyPatchRequest,
    BranchProtectionPolicy,
    CommitMetadata,
    CreatePrivateRepositoryRequest,
    CreatePullRequestRequest,
    CreatedRepository,
    PullRequestMetadata,
    RepositoryMetadata,
)


class GitHubAppSecretProvider(Protocol):
    """Resolve a short-lived private-key value from an external secret store.

    The adapter never accepts an inline private key, persists resolved values,
    or includes them in logs/errors. Implementations should apply their own
    access policy and audit without recording secret material.
    """

    async def read_secret(self, reference: str) -> str | bytes: ...


class GitHubRepositorySourcePort(Protocol):
    """Operations implemented by ``GitHubAppClient`` in this module."""

    async def create_private_repository(
        self, request: CreatePrivateRepositoryRequest
    ) -> CreatedRepository: ...

    async def protect_default_branch(
        self, repository: RepositoryMetadata, policy: BranchProtectionPolicy
    ) -> None: ...

    async def get_repository_metadata(self, full_name: str) -> RepositoryMetadata: ...

    async def aclose(self) -> None: ...


class GitHubPatchPort(Protocol):
    """Atomically create an agent branch from a pinned default-branch SHA."""

    async def apply_patch(
        self, repository: RepositoryMetadata, request: ApplyPatchRequest
    ) -> CommitMetadata: ...


class GitHubPullRequestPort(Protocol):
    """Open a pull request from an exact agent branch to the default branch."""

    async def create_pull_request(
        self, repository: RepositoryMetadata, request: CreatePullRequestRequest
    ) -> PullRequestMetadata: ...


__all__ = [
    "GitHubAppSecretProvider",
    "GitHubPatchPort",
    "GitHubPullRequestPort",
    "GitHubRepositorySourcePort",
]
