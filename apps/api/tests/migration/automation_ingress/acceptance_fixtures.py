"""MIG-AUTO-01 acceptance fixtures; not executed during verification freeze."""

from datetime import UTC, datetime

from prodavan.application.migration.automation_ingress.ingress import EndpointMapping, InboundDelivery

NOW = datetime(2026, 11, 1, 14, 25, tzinfo=UTC)
MAPPING = EndpointMapping(
    mapping_id="map-01",
    tenant_id="tenant-01",
    project_id="project-01",
    source="partner-hook",
    endpoint_id="endpoint-01",
    target_id="target-automation-01",
    task_name="automation.execute",
    secret_ref="secret://partner-hook/signing-key",
    compatibility_routes=("/v1/automation/endpoint-01/events", "/v0/hooks/partner-hook"),
)
DELIVERY = InboundDelivery(
    source="partner-hook",
    route="/v1/automation/endpoint-01/events",
    event_id="event-1001",
    body=b'{"action":"refresh"}',
    headers={"X-Webhook-Signature": "fixture-signature"},
    received_at=NOW,
)

CASES = (
    "explicit source+route mapping resolves exactly one endpoint and target; unknown/ambiguous mappings reject",
    "compatibility route maps to the same endpoint/target without creating a second identity namespace",
    "inbound signature is verified before any idempotency claim or task/run intent write",
    "scoped idempotency identity includes tenant, project, endpoint, source and source event ID",
    "same event and same request digest returns original task-run, agent-run and ordered-event linkage",
    "same idempotency identity with changed request digest is a conflict and never overwrites provenance",
    "claim, durable task-run intent, agent_runs linkage and initial ordered event commit atomically",
    "approval request/decision events retain run linkage and approval IDs in acceptance evidence",
    "source event, mapping/endpoint/target IDs and payload/request digests remain immutable provenance",
    "queue outage after durable intent is recovered by outbox delivery, not by a second task creation",
    "missing durable control-plane transaction returns blocked and never falls back to memory or queue-only state",
    "unverified inbound request creates no claim, task run, agent run, event or approval",
    "tenant/project boundary mismatch cannot resolve another target or replay another tenant claim",
    "disabled endpoint mapping rejects current and compatibility routes",
    "acceptance fixtures stay unexecuted until migration coordinator explicitly opens verification gate",
)
