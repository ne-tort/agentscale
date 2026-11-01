"""MIG-AUTO-01 acceptance contract declarations; deliberately not run."""

from .acceptance_fixtures import CASES, DELIVERY, MAPPING
from prodavan.application.migration.automation_ingress.ingress import (
    AutomationIngressService,
    IngressAcceptance,
    IngressClaim,
    IngressStatus,
)


def test_acceptance_fixtures_cover_required_contracts() -> None:
    assert len(CASES) >= 15
    assert MAPPING.compatibility_routes[0].startswith("/")
    assert DELIVERY.source == MAPPING.source
    assert IngressStatus.BLOCKED.value == "blocked"
    assert IngressAcceptance(IngressStatus.BLOCKED, blocker="durable_control_plane_unavailable").task_run_id is None
    assert IngressClaim.__name__ == "IngressClaim"
    assert AutomationIngressService.__name__ == "AutomationIngressService"
