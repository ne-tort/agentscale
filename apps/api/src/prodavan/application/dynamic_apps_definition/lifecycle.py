"""Explicit lifecycle policy for dynamic-app definitions, versions and grants.

The persisted enumeration in ``infrastructure/persistence/models/dynamic_apps.py`` is
``draft | published | disabled | revoked``. The CRUD contract in
``docs/08-migration/dynamic-app-migration-gaps.md`` (§2) and the current migration task use
the vocabulary ``draft -> published -> archived``. ``archived`` is therefore *not* a new
persisted value: the only representable terminal "no new grants or installs" state in the
existing schema is ``disabled``.

Mapping (single source of truth, never inferred at a call site):

    api "draft"     <-> persisted "draft"
    api "published" <-> persisted "published"
    api "archived"  <-> persisted "disabled"

Adding a real ``archived`` value needs a new CHECK constraint (models + Alembic), which is
outside this task's file zone, so it is recorded as a blocker in the task report.
"""

from __future__ import annotations

from prodavan.domain.dynamic_apps import AppLifecycleStatus
from prodavan.domain.errors import AppError

API_DRAFT = "draft"
API_PUBLISHED = "published"
API_ARCHIVED = "archived"

API_LIFECYCLE = frozenset({API_DRAFT, API_PUBLISHED, API_ARCHIVED})

_ARCHIVED_PERSISTED = AppLifecycleStatus.DISABLED

#: Allowed persisted transitions. Anything not listed here is a hard 409, never a silent
#: no-op: an absent edge must be an explicit rejection (gaps doc §2).
DEFINITION_TRANSITIONS: dict[AppLifecycleStatus, frozenset[AppLifecycleStatus]] = {
    AppLifecycleStatus.DRAFT: frozenset({AppLifecycleStatus.PUBLISHED}),
    AppLifecycleStatus.PUBLISHED: frozenset({_ARCHIVED_PERSISTED}),
    AppLifecycleStatus.DISABLED: frozenset(),
    AppLifecycleStatus.REVOKED: frozenset(),
}

VERSION_TRANSITIONS: dict[AppLifecycleStatus, frozenset[AppLifecycleStatus]] = {
    AppLifecycleStatus.DRAFT: frozenset({AppLifecycleStatus.PUBLISHED}),
    AppLifecycleStatus.PUBLISHED: frozenset(),
    AppLifecycleStatus.DISABLED: frozenset(),
    AppLifecycleStatus.REVOKED: frozenset(),
}


def api_lifecycle(status: AppLifecycleStatus) -> str:
    """Map a persisted status onto the public lifecycle vocabulary."""
    if status is AppLifecycleStatus.DRAFT:
        return API_DRAFT
    if status is AppLifecycleStatus.PUBLISHED:
        return API_PUBLISHED
    if status is _ARCHIVED_PERSISTED:
        return API_ARCHIVED
    # ``revoked`` is a security state that this CRUD surface never enters; surfacing it as
    # ``archived`` would let a client mistake a revoke for a benign archive.
    return AppLifecycleStatus.REVOKED.value


def archived_persisted_status() -> AppLifecycleStatus:
    """Persisted status written by the ``archive`` operation."""
    return _ARCHIVED_PERSISTED


def transition_allowed(
    matrix: dict[AppLifecycleStatus, frozenset[AppLifecycleStatus]],
    *,
    current: AppLifecycleStatus,
    target: AppLifecycleStatus,
) -> bool:
    return target in matrix.get(current, frozenset())


def require_accepts_new_version(definition_status: AppLifecycleStatus) -> None:
    """Publishing a *version* is allowed while the definition is draft or published.

    Publishing a second version of an already published definition only moves
    ``current_version_id``; it is not a definition lifecycle transition. Archived and
    revoked definitions can never gain a new serving version.
    """
    if definition_status in {AppLifecycleStatus.DRAFT, AppLifecycleStatus.PUBLISHED}:
        return
    raise AppError(
        code="INVALID_LIFECYCLE_TRANSITION",
        title="Invalid lifecycle transition",
        status=409,
        detail=f"definition in state {api_lifecycle(definition_status)} cannot be published",
    )


def require_archivable_definition(definition_status: AppLifecycleStatus) -> None:
    if definition_status is _ARCHIVED_PERSISTED:
        raise AppError(
            code="ALREADY_ARCHIVED",
            title="Already archived",
            status=409,
            detail="definition is already archived",
        )
    if not transition_allowed(
        DEFINITION_TRANSITIONS, current=definition_status, target=_ARCHIVED_PERSISTED
    ):
        raise AppError(
            code="INVALID_LIFECYCLE_TRANSITION",
            title="Invalid lifecycle transition",
            status=409,
            detail=(
                "only a published definition can be archived; "
                f"current state is {api_lifecycle(definition_status)}"
            ),
        )


def require_publishable_version(version_status: AppLifecycleStatus) -> None:
    if version_status is AppLifecycleStatus.PUBLISHED:
        raise AppError(
            code="VERSION_ALREADY_PUBLISHED",
            title="Version already published",
            status=409,
            detail="published versions are immutable; publish a new version instead",
        )
    if not transition_allowed(
        VERSION_TRANSITIONS, current=version_status, target=AppLifecycleStatus.PUBLISHED
    ):
        raise AppError(
            code="INVALID_LIFECYCLE_TRANSITION",
            title="Invalid lifecycle transition",
            status=409,
            detail=f"version in state {version_status.value} cannot be published",
        )


def require_installable(definition_status: AppLifecycleStatus) -> None:
    """Installs and new grants are only allowed while the definition is serving."""
    if definition_status is AppLifecycleStatus.PUBLISHED:
        return
    if definition_status is AppLifecycleStatus.DRAFT:
        raise AppError(
            code="APP_NOT_PUBLISHED",
            title="Application not published",
            status=409,
            detail="publish a version before installing the application or issuing grants",
        )
    # ``disabled`` (archived) and anything else is a terminal, non-serving state.
    raise AppError(
        code="APP_ARCHIVED",
        title="Application not installable",
        status=409,
        detail=(
            "application is "
            f"{api_lifecycle(definition_status)}; new instances and grants are denied"
        ),
    )


def require_published_version(version_status: AppLifecycleStatus) -> None:
    if version_status is not AppLifecycleStatus.PUBLISHED:
        raise AppError(
            code="VERSION_NOT_PUBLISHED",
            title="Version not published",
            status=409,
            detail="grants and instances must reference a published immutable version",
        )


__all__ = [
    "API_ARCHIVED",
    "API_DRAFT",
    "API_LIFECYCLE",
    "API_PUBLISHED",
    "DEFINITION_TRANSITIONS",
    "VERSION_TRANSITIONS",
    "api_lifecycle",
    "archived_persisted_status",
    "require_accepts_new_version",
    "require_archivable_definition",
    "require_installable",
    "require_publishable_version",
    "require_published_version",
    "transition_allowed",
]
