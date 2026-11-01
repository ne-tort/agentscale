"""Exclusive resource/materialization plan adapter zone."""

from prodavan.application.materialization_adapter.contracts import (
    ResourceMaterializationPlan,
    ResourceMaterializationRequest,
    ResourceOperation,
)
from prodavan.application.materialization_adapter.ports import (
    ResourceMaterializationPlanPort,
    ResourceMaterializationSourcePort,
)
from prodavan.application.materialization_adapter.service import ResourceMaterializationAdapter

__all__ = [
    "ResourceMaterializationAdapter",
    "ResourceMaterializationPlan",
    "ResourceMaterializationPlanPort",
    "ResourceMaterializationRequest",
    "ResourceMaterializationSourcePort",
    "ResourceOperation",
]
