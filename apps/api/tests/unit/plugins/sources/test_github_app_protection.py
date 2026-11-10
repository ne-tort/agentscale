"""Exact default-branch protection and no-bypass contract tests."""

from __future__ import annotations

import pytest

from prodavan.infrastructure.plugins.sources import (
    BranchProtectionPolicy,
    GitHubOrganizationNotAllowed,
    GitHubValidationError,
    RepositoryMetadata,
)

from .conftest import DEFAULT_REPO_PAYLOAD, ORGANIZATION, FakeGitHub, MutableClock


@pytest.mark.asyncio
async def test_default_branch_protection_exact_payload_and_no_app_bypass(
    make_client,
    clock: MutableClock,
) -> None:
    fake = FakeGitHub(clock)
    client = make_client(fake.handler, clock=clock)
    repository = RepositoryMetadata(
        repo_id=DEFAULT_REPO_PAYLOAD["id"],
        full_name=DEFAULT_REPO_PAYLOAD["full_name"],
        default_branch="main",
        private=True,
    )

    await client.protect_default_branch(
        repository,
        BranchProtectionPolicy(
            required_status_checks=("ci/verify", "security/scan"),
            required_approving_review_count=2,
        ),
    )

    assert fake.protection_path == f"/repos/{ORGANIZATION}/plugin-one/branches/main/protection"
    assert fake.protection_payload == {
        "required_status_checks": {
            "strict": True,
            "contexts": ["ci/verify", "security/scan"],
        },
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": True,
            "required_approving_review_count": 2,
            "require_last_push_approval": True,
            "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
        },
        "restrictions": None,
        "required_linear_history": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "block_creations": False,
        "required_conversation_resolution": True,
        "lock_branch": False,
        "allow_fork_syncing": False,
    }
    # Exactly one branch is protected; no agents/* wildcard blocks agent work.
    protection_requests = [request for request in fake.requests if request.method == "PUT"]
    assert len(protection_requests) == 1
    assert "agents" not in fake.protection_path


@pytest.mark.asyncio
async def test_branch_with_slash_is_encoded_as_one_route_parameter(
    make_client,
    clock: MutableClock,
) -> None:
    fake = FakeGitHub(clock)
    client = make_client(fake.handler, clock=clock)
    repository = RepositoryMetadata(
        repo_id=DEFAULT_REPO_PAYLOAD["id"],
        full_name=DEFAULT_REPO_PAYLOAD["full_name"],
        default_branch="release/v1",
        private=True,
    )

    await client.protect_default_branch(
        repository,
        BranchProtectionPolicy(required_status_checks=("ci/verify",)),
    )
    expected_raw_path = (
        f"/repos/{ORGANIZATION}/plugin-one/branches/release%2Fv1/protection"
    )
    assert fake.protection_raw_path is not None
    assert fake.protection_raw_path.decode("ascii") == expected_raw_path
    assert fake.protection_raw_path.count(b"%2F") == 1
    assert b"%252F" not in fake.protection_raw_path
    protection_requests = [request for request in fake.requests if request.method == "PUT"]
    assert len(protection_requests) == 1
    assert protection_requests[0].url.host == "api.github.com"
    assert protection_requests[0].url.raw_path == fake.protection_raw_path


@pytest.mark.asyncio
async def test_protection_cannot_target_an_unmanaged_owner(
    make_client,
    clock: MutableClock,
) -> None:
    fake = FakeGitHub(clock)
    client = make_client(fake.handler, clock=clock)
    repository = RepositoryMetadata(
        repo_id=123,
        full_name="unmanaged/plugin-one",
        default_branch="main",
        private=True,
    )

    with pytest.raises(GitHubOrganizationNotAllowed):
        await client.protect_default_branch(
            repository,
            BranchProtectionPolicy(required_status_checks=("ci/verify",)),
        )
    assert fake.requests == []


@pytest.mark.parametrize("checks", [(), ("",), ("bad\ncontext",)])
def test_required_status_checks_must_be_non_empty_and_valid(checks: tuple[str, ...]) -> None:
    with pytest.raises(GitHubValidationError):
        BranchProtectionPolicy(required_status_checks=checks)
