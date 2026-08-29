"""Metrics Kafka consumer handlers."""

from prodavan.application.metrics.consumer.presence_handler import (
    handle_auth_event,
)
from prodavan.application.metrics.consumer.presence_handler import (
    handle_presence_envelope as handle_platform_envelope,
)
from prodavan.application.metrics.consumer.registry import handle_metrics_envelope

__all__ = ["handle_auth_event", "handle_platform_envelope", "handle_metrics_envelope"]
