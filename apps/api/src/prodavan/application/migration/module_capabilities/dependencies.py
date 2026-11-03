"""Dependency/conflict validation for normalized module versions."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from .contracts import CapabilityVersion, DependencyIssue


@dataclass(frozen=True, slots=True)
class DependencyValidation:
    valid: bool
    issues: tuple[DependencyIssue, ...] = ()

    @property
    def quarantined(self) -> bool:
        return bool(self.issues)


def validate_module_dependencies(
    *,
    candidate: CapabilityVersion,
    available: Mapping[str, CapabilityVersion],
) -> DependencyValidation:
    """Validate exact module identity, dependency availability, conflicts and cycles.

    `available` is an application-supplied snapshot; this function performs no
    repository lookup and never turns a missing dependency into a wildcard.
    """
    available = {module_id: version for module_id, version in available.items() if version.module_id == module_id}
    issues: list[DependencyIssue] = []
    dependencies = {item.module_id: item for item in candidate.dependencies}
    conflicts = {item.module_id: item for item in candidate.conflicts}
    if candidate.module_id in dependencies:
        issues.append(DependencyIssue("self_dependency", candidate.module_id, candidate.module_id, "module cannot depend on itself"))
    if candidate.module_id in conflicts:
        issues.append(DependencyIssue("self_conflict", candidate.module_id, candidate.module_id, "module cannot conflict with itself"))
    overlap = sorted(set(dependencies) & set(conflicts))
    for module_id in overlap:
        issues.append(DependencyIssue("dependency_conflict_overlap", candidate.module_id, module_id, "module cannot both depend on and conflict with another module"))
    for module_id in sorted(dependencies):
        relation = dependencies[module_id]
        dependency = available.get(module_id)
        if dependency is None:
            issues.append(DependencyIssue("missing_dependency", candidate.module_id, module_id, "required module version is unavailable"))
            continue
        if dependency.lifecycle.value != "published":
            issues.append(DependencyIssue("dependency_not_published", candidate.module_id, module_id, "dependency must be published"))
        if dependency.definition_id == candidate.definition_id:
            issues.append(DependencyIssue("same_definition_dependency", candidate.module_id, module_id, "dependency must belong to another definition"))
        if relation.version_constraint and not version_satisfies(dependency.version, relation.version_constraint):
            issues.append(DependencyIssue("dependency_version_mismatch", candidate.module_id, module_id, "published dependency version does not satisfy constraint"))
    for module_id in sorted(conflicts):
        conflicting = available.get(module_id)
        if conflicting is not None and conflicting.lifecycle.value == "published":
            issues.append(DependencyIssue("published_conflict", candidate.module_id, module_id, "conflicting module is published"))
    if _has_cycle(candidate, available):
        issues.append(DependencyIssue("dependency_cycle", candidate.module_id, candidate.module_id, "dependency graph contains a cycle"))
    return DependencyValidation(not issues, tuple(issues))


def version_satisfies(version: str, constraint: str) -> bool:
    """Evaluate the bounded constraint dialect accepted by migration contracts."""
    match = re.fullmatch(r"(>=|<=|>|<|=|\^|~)?(\d+)\.(\d+)\.(\d+)", constraint.strip())
    current = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version)
    if not match or not current:
        return False
    wanted = tuple(int(match.group(index)) for index in (2, 3, 4))
    actual = tuple(int(current.group(index)) for index in (1, 2, 3))
    operator = match.group(1) or "="
    if operator == "=":
        return actual == wanted
    if operator == ">=":
        return actual >= wanted
    if operator == "<=":
        return actual <= wanted
    if operator == ">":
        return actual > wanted
    if operator == "<":
        return actual < wanted
    if operator == "^":
        if wanted[0] > 0:
            return actual >= wanted and actual[0] == wanted[0]
        if wanted[1] > 0:
            return actual >= wanted and actual[:2] == wanted[:2]
        return actual == wanted
    return actual >= wanted and actual[:2] == wanted[:2]


def _has_cycle(
    candidate: CapabilityVersion,
    available: Mapping[str, CapabilityVersion],
) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module_id: str) -> bool:
        if module_id in visiting:
            return True
        if module_id in visited:
            return False
        visiting.add(module_id)
        version = available.get(module_id)
        if version is not None:
            for relation in version.dependencies:
                if visit(relation.module_id):
                    return True
        visiting.remove(module_id)
        visited.add(module_id)
        return False

    for relation in candidate.dependencies:
        if visit(relation.module_id):
            return True
    return False


__all__ = ["DependencyValidation", "validate_module_dependencies", "version_satisfies"]
