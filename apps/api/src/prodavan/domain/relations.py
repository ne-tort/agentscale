"""Relations domain — kinds and subject/object vocabulary."""

from __future__ import annotations

from enum import StrEnum


class RelationKind(StrEnum):
    LINK = "link"
    MEMBERSHIP = "membership"
    ASSIGNMENT = "assignment"
    GRANT = "grant"
    BINDING = "binding"
    OWNERSHIP = "ownership"


class RelationStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class EntityKind(StrEnum):
    COMPANY = "company"
    EMPLOYEE = "employee"
    CABINET = "cabinet"
    PROJECT = "project"
    AI_KEY = "ai_key"
    MODULE = "module"


# Kafka event types on bus relation_event
RELATION_GRANTED = "relation.granted"
RELATION_REVOKED = "relation.revoked"
RELATION_REPLACED = "relation.replaced"
