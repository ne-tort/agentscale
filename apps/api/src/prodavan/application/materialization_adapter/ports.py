"""Ports for resource/materialization plan adaptation."""

from __future__ import annotations

from typing import Protocol

from prodavan.application.materialization_adapter.contracts import (
    ResourceMaterializationPlan,
    ResourceMaterializationRequest,
)


class ResourceMaterializationPlanPort(Protocol):
    """Build an immutable plan without invoking the legacy executor."""

    def build_plan(self, request: ResourceMaterializationRequest) -> ResourceMaterializationPlan: ...


class ResourceMaterializationSourcePort(Protocol):
    """Optional source boundary for callers that expose resource snapshots."""

    def snapshot(self, request: ResourceMaterializationRequest) -> ResourceMaterializationRequest: ...
