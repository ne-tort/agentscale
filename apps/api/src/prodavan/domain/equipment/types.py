"""Stable vocabulary for the equipment migration domain."""

from enum import StrEnum


class IngestionRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OfferStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class CandidateGroupStatus(StrEnum):
    OPEN = "open"
    SELECTED = "selected"
    REJECTED = "rejected"


class TenderStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    EVALUATION = "evaluation"
    AWARDED = "awarded"
    CANCELLED = "cancelled"
    CLOSED = "closed"


class ProcurementStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    ORDERED = "ordered"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class BudgetStatus(StrEnum):
    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"
PY