"""Source-control metadata ModuleSource (ADR-009).

Модуль не знает о GitHub token'ах и не разрешает секреты. Это НЕ второй
runtime/grant registry: здесь только неизменяемая привязка исходника и trust-
конфигурация direct/reusable workflow для CI publication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from prodavan.domain.plugins.helpers import plugin_instance_id_for
from prodavan.domain.plugins.types import PLUGIN_ID_PATTERN

_FULL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_DEFAULT_BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_CANONICAL_WORKFLOW_REPOSITORY = "ne-tort/agentscale"
_CANONICAL_WORKFLOW_PATH = (
    f"{_CANONICAL_WORKFLOW_REPOSITORY}/.github/workflows/plugin-artifacts.yml"
)
_FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_TRUSTED_WORKFLOW_PATTERN = re.compile(
    r"^ne-tort/agentscale/[.]github/workflows/plugin-artifacts[.]yml@"
    r"(?:[0-9a-f]{40}|refs/heads/main)$"
)
_SECRET_REF_PATTERN = re.compile(
    r"^(?:vault://|secret_ref://|secret://|secret_ref:)[A-Za-z0-9][A-Za-z0-9._/-]{2,}$"
)


class ModuleSourceStatus(StrEnum):
    PROVISIONING = "provisioning"
    READY = "ready"
    DISABLED = "disabled"


class ModuleSourceReservationStatus(StrEnum):
    """Lifecycle of the pre-provider distributed reservation, not a source."""

    PROVISIONING = "provisioning"
    READY = "ready"
    DISABLED = "disabled"


def _text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be a non-empty string of at most {maximum} chars")
    if value != value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} contains forbidden whitespace/control characters")
    return value


@dataclass(frozen=True, slots=True)
class ModuleSourceTrustConfig:
    """Server-owned trust refs; the credential REF stays out of API responses."""

    installation_credential_ref: str
    trusted_workflow_ref: str

    def validate(self) -> None:
        _text(
            self.installation_credential_ref,
            field="installation_credential_ref",
            maximum=512,
        )
        if _SECRET_REF_PATTERN.fullmatch(self.installation_credential_ref) is None:
            raise ValueError("installation_credential_ref must be a secret REF, never a token")
        _validate_trusted_workflow_ref(self.trusted_workflow_ref)


def _validate_scope_pair(cabinet_id: object, project_id: object) -> None:
    if cabinet_id is None and project_id is None:
        return
    if cabinet_id is None or project_id is None:
        raise ValueError("cabinet_id and project_id must be bound together")
    _text(cabinet_id, field="cabinet_id", maximum=40)
    _text(project_id, field="project_id", maximum=40)


def _validate_default_branch(value: object) -> None:
    _text(value, field="default_branch", maximum=255)
    if (
        not isinstance(value, str)
        or _DEFAULT_BRANCH_PATTERN.fullmatch(value) is None
        or ".." in value
        or "//" in value
        or value.endswith(("/", ".", "@{"))
        or (value.startswith("refs/") and not value.startswith("refs/heads/"))
    ):
        raise ValueError("default_branch must be a plain Git branch name")


def _expected_ref(default_branch: str) -> str:
    if default_branch.startswith("refs/heads/"):
        return default_branch
    return f"refs/heads/{default_branch}"


def _validate_trusted_workflow_ref(value: object) -> None:
    _text(value, field="trusted_workflow_ref", maximum=1024)
    if not isinstance(value, str) or _TRUSTED_WORKFLOW_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "trusted_workflow_ref must be the canonical workflow at a full SHA "
            "or protected branch ref"
        )


def _validate_workflow_source_binding(
    *, full_name: str, default_branch: str, trusted_workflow_ref: str
) -> None:
    """Bind a mutable direct identity to its exact canonical protected source ref."""
    workflow_ref = trusted_workflow_ref.rsplit("@", 1)[1]
    if _FULL_SHA_PATTERN.fullmatch(workflow_ref) is not None:
        return
    expected_direct_ref = f"{_CANONICAL_WORKFLOW_PATH}@{_expected_ref(default_branch)}"
    if full_name != _CANONICAL_WORKFLOW_REPOSITORY or trusted_workflow_ref != expected_direct_ref:
        raise ValueError(
            "direct trusted_workflow_ref requires the canonical repository and "
            "its exact protected default branch"
        )


@dataclass(frozen=True, slots=True)
class ModuleSourceProvisioningReservation:
    """Short SQL reservation made before GitHub knows the numeric repo id.

    This is deliberately a separate type: ``repo_id`` is not knowable yet and no
    placeholder can accidentally become a persisted ModuleSource identity.
    ``attempt_id`` fences create retry/release operations across workers.
    """

    id: str
    company_id: str
    plugin_id: str
    full_name: str
    private: bool
    default_branch: str
    installation_credential_ref: str
    trusted_workflow_ref: str
    attempt_id: str
    created_at: datetime
    updated_at: datetime
    cabinet_id: str | None = None
    project_id: str | None = None
    provider: str = "github_app"
    create_in_progress: bool = True
    status: ModuleSourceReservationStatus = ModuleSourceReservationStatus.PROVISIONING

    def __post_init__(self) -> None:
        _text(self.id, field="id", maximum=80)
        _text(self.company_id, field="company_id", maximum=40)
        _text(self.plugin_id, field="plugin_id", maximum=160)
        if PLUGIN_ID_PATTERN.fullmatch(self.plugin_id) is None:
            raise ValueError("plugin_id must use the canonical plugin id grammar")
        _validate_scope_pair(self.cabinet_id, self.project_id)
        if self.provider != "github_app":
            raise ValueError("only github_app ModuleSource provider is supported")
        _text(self.full_name, field="full_name", maximum=255)
        if _FULL_NAME_PATTERN.fullmatch(self.full_name) is None:
            raise ValueError("full_name must be GitHub owner/repository")
        if self.private is not True:
            raise ValueError("a provisioning reservation must require a private repository")
        _validate_default_branch(self.default_branch)
        ModuleSourceTrustConfig(
            installation_credential_ref=self.installation_credential_ref,
            trusted_workflow_ref=self.trusted_workflow_ref,
        ).validate()
        _validate_workflow_source_binding(
            full_name=self.full_name,
            default_branch=self.default_branch,
            trusted_workflow_ref=self.trusted_workflow_ref,
        )
        _text(self.attempt_id, field="attempt_id", maximum=80)
        if not isinstance(self.create_in_progress, bool):
            raise ValueError("create_in_progress must be a boolean")
        if not isinstance(self.status, ModuleSourceReservationStatus):
            raise ValueError("unknown ModuleSource reservation status")
        if self.status is not ModuleSourceReservationStatus.PROVISIONING and self.create_in_progress:
            raise ValueError("only a provisioning reservation may have an active create attempt")
        for value in (self.created_at, self.updated_at):
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("reservation timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")

    @property
    def plugin_instance_id(self) -> str:
        """Pre-publication installation identity; never persisted separately."""
        return plugin_instance_id_for(self.company_id, self.plugin_id)


@dataclass(frozen=True, slots=True)
class ModuleSourceReservationResult:
    reservation: ModuleSourceProvisioningReservation
    acquired: bool

    def __post_init__(self) -> None:
        if not isinstance(self.reservation, ModuleSourceProvisioningReservation):
            raise TypeError("reservation must be ModuleSourceProvisioningReservation")
        if not isinstance(self.acquired, bool):
            raise TypeError("acquired must be a boolean")


@dataclass(frozen=True, slots=True)
class ModuleSource:
    """Неизменяемый snapshot source-строки.

    repo_id/full_name/company_id/plugin_id нельзя обновить через repository.
    default_branch обычно хранится без refs/heads/; полный refs/heads/...
    тоже принимается и нормализуется expected_ref в точное OIDC-значение.
    """

    id: str
    company_id: str
    plugin_id: str
    repo_id: int
    full_name: str
    private: bool
    default_branch: str
    installation_credential_ref: str
    trusted_workflow_ref: str
    created_at: datetime
    updated_at: datetime
    cabinet_id: str | None = None
    project_id: str | None = None
    provider: str = "github_app"
    status: ModuleSourceStatus = ModuleSourceStatus.PROVISIONING

    def __post_init__(self) -> None:
        _text(self.id, field="id", maximum=80)
        _text(self.company_id, field="company_id", maximum=40)
        _text(self.plugin_id, field="plugin_id", maximum=160)
        if PLUGIN_ID_PATTERN.fullmatch(self.plugin_id) is None:
            raise ValueError("plugin_id must use the canonical plugin id grammar")
        _validate_scope_pair(self.cabinet_id, self.project_id)
        if self.provider != "github_app":
            raise ValueError("only github_app ModuleSource provider is supported")
        if isinstance(self.repo_id, bool) or not isinstance(self.repo_id, int) or self.repo_id <= 0:
            raise ValueError("repo_id must be a positive immutable GitHub repository id")
        _text(self.full_name, field="full_name", maximum=255)
        if _FULL_NAME_PATTERN.fullmatch(self.full_name) is None:
            raise ValueError("full_name must be GitHub owner/repository")
        if not isinstance(self.private, bool):
            raise ValueError("private must be a boolean")
        _validate_default_branch(self.default_branch)
        _text(
            self.installation_credential_ref,
            field="installation_credential_ref",
            maximum=512,
        )
        if _SECRET_REF_PATTERN.fullmatch(self.installation_credential_ref) is None:
            raise ValueError("installation_credential_ref must be a secret REF, never a token")
        _validate_trusted_workflow_ref(self.trusted_workflow_ref)
        _validate_workflow_source_binding(
            full_name=self.full_name,
            default_branch=self.default_branch,
            trusted_workflow_ref=self.trusted_workflow_ref,
        )
        if not isinstance(self.status, ModuleSourceStatus):
            raise ValueError("unknown ModuleSource status")
        for value in (self.created_at, self.updated_at):
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("ModuleSource timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")

    @property
    def plugin_instance_id(self) -> str:
        """Deterministic non-secret id shared with the future initial grant."""
        return plugin_instance_id_for(self.company_id, self.plugin_id)

    @property
    def expected_ref(self) -> str:
        return _expected_ref(self.default_branch)

    @property
    def expected_signer_identity(self) -> str:
        """Exact reusable SHA or direct protected-branch cosign identity."""
        return f"https://github.com/{self.trusted_workflow_ref}"
