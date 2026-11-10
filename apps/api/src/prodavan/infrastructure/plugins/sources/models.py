"""Immutable, non-secret configuration and results for GitHub sources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal
from urllib.parse import urlsplit

from .errors import GitHubConfigurationError, GitHubValidationError

_OWNER_PATTERN = re.compile(r"(?!.*--)[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_BRANCH_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}")
_FULL_NAME_PATTERN = re.compile(r"([^/]+)/([^/]+)")
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_CHECK_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@+ -]{0,199}")
_SECRET_REFERENCE_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,31}://\S{1,500}")
_PATCH_OPERATION = Literal["upsert", "delete"]
_AGENT_SEGMENT_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_MAX_PATCH_FILE_BYTES = 1_048_576
_MAX_PATCH_TOTAL_BYTES = 4_194_304


class GitHubInstallationTokenProfile(str, Enum):
    """Least-privilege permission sets requested for one installation.

    The GitHub App registration must explicitly grant the union of these
    permissions. Requesting an installation token cannot elevate permissions
    beyond that registration; the client also verifies the granted set in the
    token response before using the credential.
    """

    PROVISIONING = "provisioning"
    AUTHORING = "authoring"
    REGISTRY_READ = "registry_read"

    @property
    def permissions(self) -> dict[str, str]:
        if self is GitHubInstallationTokenProfile.PROVISIONING:
            return {"administration": "write", "metadata": "read"}
        if self is GitHubInstallationTokenProfile.AUTHORING:
            return {
                "contents": "write",
                "pull_requests": "write",
                "metadata": "read",
            }
        if self is GitHubInstallationTokenProfile.REGISTRY_READ:
            return {"packages": "read", "metadata": "read"}
        raise GitHubConfigurationError("unsupported GitHub installation token profile")


def validate_owner(value: str, *, field_name: str = "organization") -> str:
    if not isinstance(value, str) or _OWNER_PATTERN.fullmatch(value) is None:
        raise GitHubValidationError(f"{field_name} must be a canonical GitHub organization login")
    return value


def validate_repository_name(value: str, *, field_name: str = "repository") -> str:
    if (
        not isinstance(value, str)
        or _REPOSITORY_PATTERN.fullmatch(value) is None
        or ".." in value
        or value.endswith((".", ".git"))
    ):
        raise GitHubValidationError(f"{field_name} must be a canonical GitHub repository name")
    return value


def validate_branch(value: str, *, field_name: str = "default_branch") -> str:
    if not isinstance(value, str) or _BRANCH_PATTERN.fullmatch(value) is None:
        raise GitHubValidationError(f"{field_name} must be a plain Git branch name")
    components = value.split("/")
    if (
        value.startswith("refs/")
        or ".." in value
        or "//" in value
        or "@{" in value
        or value.endswith(("/", "."))
        or any(
            component in {"", ".", ".."}
            or component.startswith(".")
            or component.endswith(".lock")
            for component in components
        )
    ):
        raise GitHubValidationError(f"{field_name} must be a plain Git branch name")
    return value


def validate_agent_branch(value: str, *, field_name: str = "branch") -> str:
    """Allow exactly ``agents/<project>/<task-slug>`` with bounded segments."""

    validate_branch(value, field_name=field_name)
    components = value.split("/")
    if (
        len(components) != 3
        or components[0] != "agents"
        or any(
            _AGENT_SEGMENT_PATTERN.fullmatch(component) is None
            for component in components[1:]
        )
    ):
        raise GitHubValidationError(
            f"{field_name} must have the agents/<project>/<task-slug> form with bounded lowercase slugs"
        )
    return value


def is_github_workflow_path(path: str) -> bool:
    components = path.casefold().split("/")
    return len(components) >= 2 and components[:2] == [".github", "workflows"]


def validate_commit(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or _COMMIT_PATTERN.fullmatch(value) is None:
        raise GitHubValidationError(f"{field_name} must be a full lowercase Git commit SHA")
    return value


def validate_secret_reference(value: str) -> str:
    """Require a reference, rejecting inline PEM and other plaintext values."""

    if (
        not isinstance(value, str)
        or len(value) > 512
        or _SECRET_REFERENCE_PATTERN.fullmatch(value) is None
        or "PRIVATE KEY" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise GitHubConfigurationError("private_key_secret_ref must be a secret reference, not key material")
    return value


def split_full_name(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise GitHubValidationError("full_name must have the owner/repository form")
    match = _FULL_NAME_PATTERN.fullmatch(value)
    if match is None:
        raise GitHubValidationError("full_name must have the owner/repository form")
    owner, repository = match.groups()
    return validate_owner(owner), validate_repository_name(repository)


def _positive_integer(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GitHubValidationError(f"{field_name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class RepositoryTemplate:
    """A private template selected by trusted source configuration.

    ``agents_revision`` and ``security_revision`` are non-secret provenance
    pins supplied by source configuration. GitHub's generate endpoint always
    reads the template's current default branch and has no revision parameter;
    the template publication process must materialize these pins there before
    provisioning. The pins are returned with ``CreatedRepository`` for audit,
    and are deliberately not sent as unsupported API parameters.
    """

    owner: str
    name: str
    agents_revision: str
    security_revision: str

    def __post_init__(self) -> None:
        validate_owner(self.owner, field_name="template.owner")
        validate_repository_name(self.name, field_name="template.name")
        validate_commit(self.agents_revision, field_name="template.agents_revision")
        validate_commit(self.security_revision, field_name="template.security_revision")


@dataclass(frozen=True, slots=True)
class ManagedOrganization:
    """Explicit owner allowlist entry and its installation/template binding."""

    name: str
    installation_id: int
    template: RepositoryTemplate

    def __post_init__(self) -> None:
        validate_owner(self.name)
        _positive_integer(self.installation_id, field_name="installation_id")
        if not isinstance(self.template, RepositoryTemplate):
            raise GitHubConfigurationError("template must be a RepositoryTemplate")


@dataclass(frozen=True, slots=True)
class CreatePrivateRepositoryRequest:
    """Self-service input; there is intentionally no arbitrary owner field."""

    organization: str
    name: str
    description: str | None = None

    def __post_init__(self) -> None:
        validate_owner(self.organization)
        validate_repository_name(self.name, field_name="name")
        if self.description is not None:
            if (
                not isinstance(self.description, str)
                or len(self.description) > 350
                or self.description != self.description.strip()
                or any(ord(character) < 32 for character in self.description)
            ):
                raise GitHubValidationError("description must be single-line text of at most 350 characters")


@dataclass(frozen=True, slots=True)
class RepositoryMetadata:
    """Immutable repository identity returned by GitHub."""

    repo_id: int
    full_name: str
    default_branch: str
    private: bool

    def __post_init__(self) -> None:
        _positive_integer(self.repo_id, field_name="repo_id")
        split_full_name(self.full_name)
        validate_branch(self.default_branch)
        if not isinstance(self.private, bool):
            raise GitHubValidationError("private must be a boolean")


@dataclass(frozen=True, slots=True)
class CreatedRepository(RepositoryMetadata):
    """Created identity plus non-secret template provenance."""

    template: RepositoryTemplate

    def __post_init__(self) -> None:
        RepositoryMetadata.__post_init__(self)
        if not isinstance(self.template, RepositoryTemplate):
            raise GitHubValidationError("template must be a RepositoryTemplate")


@dataclass(frozen=True, slots=True)
class BranchProtectionPolicy:
    """Required checks are mandatory; an empty strict check set is rejected."""

    required_status_checks: tuple[str, ...]
    required_approving_review_count: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.required_status_checks, tuple) or not self.required_status_checks:
            raise GitHubValidationError("at least one required status check is mandatory")
        normalized: list[str] = []
        seen: set[str] = set()
        for check in self.required_status_checks:
            if (
                not isinstance(check, str)
                or check != check.strip()
                or _CHECK_PATTERN.fullmatch(check) is None
            ):
                raise GitHubValidationError("required status checks contain an invalid context")
            if check not in seen:
                normalized.append(check)
                seen.add(check)
        object.__setattr__(self, "required_status_checks", tuple(normalized))
        if (
            isinstance(self.required_approving_review_count, bool)
            or not isinstance(self.required_approving_review_count, int)
            or not 1 <= self.required_approving_review_count <= 6
        ):
            raise GitHubValidationError("required_approving_review_count must be between 1 and 6")


@dataclass(frozen=True, slots=True)
class PatchFile:
    """One typed text upsert or deletion; content is hidden from repr/logs."""

    path: str
    operation: _PATCH_OPERATION
    content: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.path, str)
            or not self.path
            or len(self.path) > 4096
            or self.path.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", self.path) is not None
            or "\\" in self.path
            or any(ord(character) < 32 or ord(character) == 127 for character in self.path)
        ):
            raise GitHubValidationError("patch path must be a relative POSIX repository path")
        components = self.path.split("/")
        if any(
            component in {"", ".", ".."} or component.casefold() == ".git"
            for component in components
        ):
            raise GitHubValidationError("patch path contains a forbidden path component")
        if not isinstance(self.operation, str) or self.operation not in {"upsert", "delete"}:
            raise GitHubValidationError("patch operation must be upsert or delete")
        if self.operation == "upsert":
            if not isinstance(self.content, str) or "\x00" in self.content:
                raise GitHubValidationError("upsert content must be NUL-free text")
            try:
                encoded = self.content.encode("utf-8")
            except UnicodeEncodeError:
                raise GitHubValidationError("upsert content must be valid UTF-8 text") from None
            if len(encoded) > _MAX_PATCH_FILE_BYTES:
                raise GitHubValidationError("upsert content must be at most 1 MiB as UTF-8")
        elif self.content is not None:
            raise GitHubValidationError("delete patch entries cannot carry content")


@dataclass(frozen=True, slots=True)
class ApplyPatchRequest:
    """Create one agent branch from an exact default-branch commit SHA."""

    branch: str
    expected_head_sha: str
    commit_message: str
    files: tuple[PatchFile, ...]

    def __post_init__(self) -> None:
        validate_agent_branch(self.branch, field_name="branch")
        validate_commit(self.expected_head_sha, field_name="expected_head_sha")
        if (
            not isinstance(self.commit_message, str)
            or not self.commit_message.strip()
            or len(self.commit_message) > 4096
            or "\x00" in self.commit_message
        ):
            raise GitHubValidationError("commit_message must be non-empty text of at most 4096 characters")
        if not isinstance(self.files, tuple) or not self.files or len(self.files) > 100:
            raise GitHubValidationError("a patch must contain between 1 and 100 files")
        if any(not isinstance(item, PatchFile) for item in self.files):
            raise GitHubValidationError("patch files must be PatchFile instances")
        if len({item.path for item in self.files}) != len(self.files):
            raise GitHubValidationError("patch paths must be unique")
        total_bytes = sum(
            len(item.content.encode("utf-8"))
            for item in self.files
            if item.operation == "upsert" and item.content is not None
        )
        if total_bytes > _MAX_PATCH_TOTAL_BYTES:
            raise GitHubValidationError("patch content must be at most 4 MiB as UTF-8 in total")


@dataclass(frozen=True, slots=True)
class CommitMetadata:
    sha: str

    def __post_init__(self) -> None:
        validate_commit(self.sha, field_name="sha")


@dataclass(frozen=True, slots=True)
class CreatePullRequestRequest:
    title: str
    head_branch: str
    base_branch: str
    body: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.title, str)
            or not self.title.strip()
            or self.title != self.title.strip()
            or len(self.title) > 256
            or any(ord(character) < 32 or ord(character) == 127 for character in self.title)
        ):
            raise GitHubValidationError(
                "pull request title must be single-line text of at most 256 characters"
            )
        validate_agent_branch(self.head_branch, field_name="head_branch")
        validate_branch(self.base_branch, field_name="base_branch")
        if self.head_branch == self.base_branch:
            raise GitHubValidationError("pull request head and base branches must differ")
        if not isinstance(self.body, str) or len(self.body) > 65_536 or "\x00" in self.body:
            raise GitHubValidationError(
                "pull request body must be NUL-free text of at most 65536 characters"
            )


@dataclass(frozen=True, slots=True)
class PullRequestMetadata:
    """Immutable PR identity and the exact head/base commits returned by GitHub."""

    number: int
    url: str
    head_branch: str
    base_branch: str
    head_sha: str
    base_sha: str

    def __post_init__(self) -> None:
        _positive_integer(self.number, field_name="number")
        validate_agent_branch(self.head_branch, field_name="head_branch")
        validate_branch(self.base_branch, field_name="base_branch")
        validate_commit(self.head_sha, field_name="head_sha")
        validate_commit(self.base_sha, field_name="base_sha")
        if self.head_branch == self.base_branch:
            raise GitHubValidationError("pull request head and base branches must differ")
        try:
            parsed = urlsplit(self.url)
            path_parts = parsed.path.strip("/").split("/")
            url_number = (
                int(path_parts[3])
                if len(path_parts) == 4 and path_parts[2] == "pull"
                else -1
            )
            if len(path_parts) == 4:
                split_full_name(f"{path_parts[0]}/{path_parts[1]}")
            valid_url = (
                parsed.scheme == "https"
                and parsed.hostname == "github.com"
                and parsed.port is None
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and parsed.path == "/" + "/".join(path_parts)
                and url_number == self.number
            )
        except (TypeError, ValueError, GitHubValidationError):
            valid_url = False
        if not valid_url:
            raise GitHubValidationError("pull request url must be the canonical github.com PR URL")


__all__ = [
    "ApplyPatchRequest",
    "BranchProtectionPolicy",
    "CommitMetadata",
    "CreatePrivateRepositoryRequest",
    "CreatePullRequestRequest",
    "CreatedRepository",
    "GitHubInstallationTokenProfile",
    "ManagedOrganization",
    "PatchFile",
    "PullRequestMetadata",
    "RepositoryMetadata",
    "RepositoryTemplate",
    "split_full_name",
    "is_github_workflow_path",
    "validate_agent_branch",
    "validate_branch",
    "validate_commit",
    "validate_owner",
    "validate_repository_name",
    "validate_secret_reference",
]
