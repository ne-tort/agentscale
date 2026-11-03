"""Typed application contracts for the module capability definition pipeline.

This zone normalizes authoring/import declarations only. It has no persistence,
API, legacy-row writer, or runtime executor.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MODULE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_SCHEMA_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _freeze(value: object) -> object:
    if isinstance(value, MappingABC):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("canonical object keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, StrEnum):
        return value.value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _plain(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, MappingABC):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_digest(value: object) -> str:
    """Return a deterministic content digest independent of object-key order."""
    encoded = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class DefinitionLifecycle(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class VersionLifecycle(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class ScopeKind(StrEnum):
    PLATFORM = "platform"
    TENANT = "tenant"
    COMPANY = "company"
    CABINET = "cabinet"
    PROJECT = "project"


class FieldType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    BOOL = "bool"
    DATETIME = "datetime"
    JSON = "json"
    ENUM = "enum"
    REF = "ref"
    FILE_REF = "file_ref"
    SECRET_REF = "secret_ref"


class ViewKind(StrEnum):
    COLLECTION = "collection"
    FORM = "form"
    HUB = "hub"
    DETAIL = "detail"
    BOARD = "board"
    PROFILE_HUB = "profile_hub"


class MaterializeSource(StrEnum):
    ROW = "row"
    ROWS = "rows"
    STATIC = "static"
    META_DOCUMENT = "meta_document"


class MaterializeFormat(StrEnum):
    RAW = "raw"
    JSON_ROWS = "json_rows"
    JSON_SINGLE = "json_single"
    TEMPLATE = "template"
    COPY_BLOB = "copy_blob"
    MCP_PACKAGE = "mcp_package"
    MERGE_MAPPED_SQLITE = "merge_mapped_sqlite"
    PROMPT_PATHS = "prompt_paths"


@dataclass(frozen=True, slots=True)
class OwnerScope:
    tenant_id: str
    scope_kind: ScopeKind
    scope_id: str
    owner_anchor_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_kind", ScopeKind(self.scope_kind))
        if not all(item.strip() for item in (self.tenant_id, self.scope_id, self.owner_anchor_id)):
            raise ValueError("tenant, typed scope, and durable owner anchor are required")
        if self.scope_kind is ScopeKind.TENANT and self.scope_id != self.tenant_id:
            raise ValueError("tenant scope id must equal tenant id")


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    definition_id: str
    module_id: str
    name: str
    lifecycle: DefinitionLifecycle = DefinitionLifecycle.DRAFT
    owner_scope: OwnerScope | None = None
    current_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "lifecycle", DefinitionLifecycle(self.lifecycle))
        if not self.definition_id.strip() or not _MODULE_ID_RE.fullmatch(self.module_id):
            raise ValueError("definition_id and valid module_id are required")
        if not self.name.strip():
            raise ValueError("definition name is required")
        if self.current_version is not None and not _VERSION_RE.fullmatch(self.current_version):
            raise ValueError("current version pointer must be a semantic version")
        if self.lifecycle is DefinitionLifecycle.PUBLISHED and self.current_version is None:
            raise ValueError("published definition requires an explicit current version")


@dataclass(frozen=True, slots=True)
class SchemaReference:
    """Resolved reference; schema/field identities are first-class, not slug-only."""

    target_schema_id: str
    target_field_id: str

    def __post_init__(self) -> None:
        if not self.target_schema_id.strip() or not self.target_field_id.strip():
            raise ValueError("schema reference requires target schema and field IDs")


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    field_id: str
    schema_id: str
    name: str
    field_type: FieldType
    required: bool = False
    reference: SchemaReference | None = None
    default_value: object = None
    declaration: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "field_type", FieldType(self.field_type))
        object.__setattr__(self, "default_value", _freeze(self.default_value))
        object.__setattr__(self, "declaration", _freeze(self.declaration))
        if not all(item.strip() for item in (self.field_id, self.schema_id, self.name)):
            raise ValueError("field identity and name are required")
        if (self.field_type is FieldType.REF) != (self.reference is not None):
            raise ValueError("ref fields require exactly one resolved schema reference")


@dataclass(frozen=True, slots=True)
class ModuleSchema:
    schema_id: str
    slug: str
    label: object
    fields: tuple[FieldDefinition, ...]
    storage_kind: str = "json_document"
    scope: Mapping[str, object] = field(default_factory=dict)
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "label", _freeze(self.label))
        object.__setattr__(self, "scope", _freeze(self.scope))
        if not isinstance(self.enabled, bool):
            raise ValueError("schema enabled must be boolean")
        if not self.schema_id.strip() or not _SCHEMA_SLUG_RE.fullmatch(self.slug):
            raise ValueError("schema ID and valid slug are required")
        if len({field.field_id for field in self.fields}) != len(self.fields):
            raise ValueError("schema field IDs must be unique")
        if len({field.name for field in self.fields}) != len(self.fields):
            raise ValueError("schema field names must be unique")
        if any(field.schema_id != self.schema_id for field in self.fields):
            raise ValueError("field belongs to a different schema")
        if not self.storage_kind.strip():
            raise ValueError("storage_kind is required")

    def field_by_name(self, name: str) -> FieldDefinition | None:
        return next((field for field in self.fields if field.name == name), None)


@dataclass(frozen=True, slots=True)
class ViewDeclaration:
    view_id: str
    slug: str
    kind: ViewKind
    schema_id: str | None
    enabled: bool
    declaration: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ViewKind(self.kind))
        object.__setattr__(self, "declaration", _freeze(self.declaration))
        if not self.view_id.strip() or not _SCHEMA_SLUG_RE.fullmatch(self.slug):
            raise ValueError("view identity and valid slug are required")


@dataclass(frozen=True, slots=True)
class NavigationDeclaration:
    navigation_id: str
    view_id: str | None
    schema_id: str | None
    order: int
    enabled: bool
    declaration: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "declaration", _freeze(self.declaration))
        if not self.navigation_id.strip() or isinstance(self.order, bool):
            raise ValueError("navigation identity and integer order are required")


@dataclass(frozen=True, slots=True)
class ActionDeclaration:
    action_id: str
    operation_id: str
    enabled: bool
    parameters: Mapping[str, object]
    schema_ids: tuple[str, ...] = ()
    trigger_events: tuple[str, ...] = ()
    declaration: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", _freeze(self.parameters))
        object.__setattr__(self, "schema_ids", tuple(self.schema_ids))
        object.__setattr__(self, "trigger_events", tuple(self.trigger_events))
        object.__setattr__(self, "declaration", _freeze(self.declaration))
        if not self.action_id.strip() or not self.operation_id.strip():
            raise ValueError("action identity and operation ID are required")


@dataclass(frozen=True, slots=True)
class McpServerDeclaration:
    server_id: str
    name: str
    transport: str
    endpoint: str | None
    command: str | None
    arguments: tuple[str, ...]
    environment: Mapping[str, object]
    declaration: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "environment", _freeze(self.environment))
        object.__setattr__(self, "declaration", _freeze(self.declaration))
        if not self.server_id.strip() or not self.name.strip():
            raise ValueError("MCP server identity and name are required")
        if self.transport not in {"stdio", "http", "sse"}:
            raise ValueError("unsupported MCP transport")
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP server requires command")
        if self.transport in {"http", "sse"} and not self.endpoint:
            raise ValueError("HTTP MCP server requires endpoint")


@dataclass(frozen=True, slots=True)
class McpToolDeclaration:
    tool_id: str
    name: str
    kind: str
    enabled: bool
    input_schema: Mapping[str, object]
    implementation: Mapping[str, object]
    schema_id: str | None = None
    implementation_field_ids: Mapping[str, str] = field(default_factory=dict)
    server_id: str | None = None
    declaration: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("input_schema", "implementation", "implementation_field_ids", "declaration"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))
        if not self.tool_id.strip() or not self.name.strip() or not self.kind.strip():
            raise ValueError("MCP tool identity, name, and kind are required")


@dataclass(frozen=True, slots=True)
class McpAliasDeclaration:
    tool_name: str
    label: str
    server_name: str | None = None
    server_id: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.tool_name.strip() or not self.label.strip():
            raise ValueError("MCP alias tool and label are required")


@dataclass(frozen=True, slots=True)
class MaterializeDeclaration:
    rule_id: str
    enabled: bool
    events: tuple[str, ...]
    priority: int
    source: MaterializeSource
    source_schema_id: str | None
    source_document_slug: str | None
    source_filter: Mapping[str, object]
    source_filter_field_ids: Mapping[str, str]
    static_value: object
    target_path: str
    output_format: MaterializeFormat
    target_field_id: str | None
    template: str | None
    declaration: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", MaterializeSource(self.source))
        object.__setattr__(self, "output_format", MaterializeFormat(self.output_format))
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "source_filter", _freeze(self.source_filter))
        object.__setattr__(self, "source_filter_field_ids", _freeze(self.source_filter_field_ids))
        object.__setattr__(self, "static_value", _freeze(self.static_value))
        object.__setattr__(self, "declaration", _freeze(self.declaration))
        if not self.rule_id.strip() or not self.target_path.strip():
            raise ValueError("materialization rule ID and target path are required")
        if isinstance(self.priority, bool):
            raise ValueError("materialization priority must be an integer")
        if (self.source in {MaterializeSource.ROW, MaterializeSource.ROWS}) != (self.source_schema_id is not None):
            raise ValueError("row materialization requires an explicit source schema ID")
        if (self.output_format in {MaterializeFormat.COPY_BLOB, MaterializeFormat.MCP_PACKAGE}) != (self.target_field_id is not None):
            raise ValueError("blob/package materialization requires an explicit target field ID")


@dataclass(frozen=True, slots=True)
class SeedRowDeclaration:
    schema_id: str
    row_id: str
    body: Mapping[str, object]
    field_ids: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "body", _freeze(self.body))
        object.__setattr__(self, "field_ids", _freeze(self.field_ids))
        if not self.schema_id.strip() or not self.row_id.strip():
            raise ValueError("seed row requires schema ID and row ID")


@dataclass(frozen=True, slots=True)
class ModuleMetadata:
    views: tuple[ViewDeclaration, ...] = ()
    navigation: tuple[NavigationDeclaration, ...] = ()
    seed_rows: tuple[SeedRowDeclaration, ...] = ()
    mcp_aliases: tuple[McpAliasDeclaration, ...] = ()
    materialize_roots: tuple[str, ...] = ()
    container_env: tuple[Mapping[str, object], ...] = ()
    container_env_secrets: tuple[Mapping[str, object], ...] = ()
    declarations: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("views", "navigation", "seed_rows", "mcp_aliases", "materialize_roots"):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))
        object.__setattr__(self, "container_env", tuple(_freeze(item) for item in self.container_env))
        object.__setattr__(self, "container_env_secrets", tuple(_freeze(item) for item in self.container_env_secrets))
        object.__setattr__(self, "declarations", _freeze(self.declarations))


@dataclass(frozen=True, slots=True)
class ModuleRelation:
    module_id: str
    version_constraint: str | None = None

    def __post_init__(self) -> None:
        if not _MODULE_ID_RE.fullmatch(self.module_id):
            raise ValueError("relation module_id is invalid")
        if self.version_constraint is not None and not self.version_constraint.strip():
            raise ValueError("version constraint cannot be blank")


@dataclass(frozen=True, slots=True)
class CapabilityVersion:
    definition_id: str
    module_id: str
    version: str
    schema_version: str
    metadata: ModuleMetadata
    schemas: tuple[ModuleSchema, ...]
    actions: tuple[ActionDeclaration, ...]
    mcp_servers: tuple[McpServerDeclaration, ...]
    mcp_tools: tuple[McpToolDeclaration, ...]
    materialize: tuple[MaterializeDeclaration, ...]
    dependencies: tuple[ModuleRelation, ...]
    conflicts: tuple[ModuleRelation, ...]
    content_digest: str
    lifecycle: VersionLifecycle = VersionLifecycle.DRAFT

    def __post_init__(self) -> None:
        object.__setattr__(self, "lifecycle", VersionLifecycle(self.lifecycle))
        for field_name in ("schemas", "actions", "mcp_servers", "mcp_tools", "materialize", "dependencies", "conflicts"):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))
        if not self.definition_id.strip() or not _MODULE_ID_RE.fullmatch(self.module_id):
            raise ValueError("definition and module identity are required")
        if not _VERSION_RE.fullmatch(self.version):
            raise ValueError("version must be semantic x.y.z")
        if not self.schema_version.strip() or not _DIGEST_RE.fullmatch(self.content_digest):
            raise ValueError("schema version and canonical sha256 digest are required")
        if len({schema.schema_id for schema in self.schemas}) != len(self.schemas):
            raise ValueError("schema IDs must be unique")
        if len({schema.slug for schema in self.schemas}) != len(self.schemas):
            raise ValueError("schema slugs must be unique")
        field_ids = [field.field_id for schema in self.schemas for field in schema.fields]
        if len(set(field_ids)) != len(field_ids):
            raise ValueError("field IDs must be globally unique within a capability version")
        if len({server.server_id for server in self.mcp_servers}) != len(self.mcp_servers):
            raise ValueError("MCP server IDs must be unique within a capability version")
        if len({tool.tool_id for tool in self.mcp_tools}) != len(self.mcp_tools):
            raise ValueError("MCP tool IDs must be unique within a capability version")
        if len({item.module_id for item in self.dependencies}) != len(self.dependencies):
            raise ValueError("dependency module IDs must be unique")
        if len({item.module_id for item in self.conflicts}) != len(self.conflicts):
            raise ValueError("conflict module IDs must be unique")
        if len({tool.name for tool in self.mcp_tools}) != len(self.mcp_tools):
            raise ValueError("MCP tool names must be unique within a capability version")
        if len({action.action_id for action in self.actions}) != len(self.actions):
            raise ValueError("action IDs must be unique within a capability version")
        if len({rule.rule_id for rule in self.materialize}) != len(self.materialize):
            raise ValueError("materialization rule IDs must be unique within a capability version")
        if len({(alias.server_id, alias.tool_name) for alias in self.metadata.mcp_aliases}) != len(self.metadata.mcp_aliases):
            raise ValueError("MCP alias identities must be unique within a capability version")
        if canonical_digest(self.content_payload()) != self.content_digest:
            raise ValueError("content digest does not match normalized capability content")

    def content_payload(self) -> Mapping[str, object]:
        """Lifecycle is excluded so publication/deprecation cannot alter the digest."""
        return {
            "definition_id": self.definition_id,
            "module_id": self.module_id,
            "version": self.version,
            "schema_version": self.schema_version,
            "metadata": self.metadata,
            "schemas": self.schemas,
            "actions": self.actions,
            "mcp_servers": self.mcp_servers,
            "mcp_tools": self.mcp_tools,
            "materialize": self.materialize,
            "dependencies": self.dependencies,
            "conflicts": self.conflicts,
        }


@dataclass(frozen=True, slots=True)
class PublishedCapabilitySnapshot:
    definition: CapabilityDefinition
    version: CapabilityVersion
    artifact_digest: str
    owner_evidence_id: str
    version_evidence_id: str
    scope_evidence_id: str
    dependency_graph_evidence_id: str
    dependency_graph_digest: str
    transaction_id: str
    actor_id: str
    decision_owner: str
    writer_generation: int
    correlation_id: str

    def __post_init__(self) -> None:
        if self.definition.lifecycle is not DefinitionLifecycle.PUBLISHED:
            raise ValueError("published snapshot requires a published definition")
        if self.version.lifecycle is not VersionLifecycle.PUBLISHED:
            raise ValueError("published snapshot requires a published version")
        if self.version.definition_id != self.definition.definition_id or self.version.module_id != self.definition.module_id:
            raise ValueError("snapshot definition/version identity mismatch")
        if self.definition.current_version != self.version.version:
            raise ValueError("published snapshot version must equal definition current version")
        if self.artifact_digest != self.version.content_digest:
            raise ValueError("artifact digest must equal immutable version content digest")
        required = (self.owner_evidence_id, self.version_evidence_id, self.scope_evidence_id,
                    self.dependency_graph_evidence_id, self.transaction_id, self.actor_id,
                    self.decision_owner, self.correlation_id)
        if not _DIGEST_RE.fullmatch(self.dependency_graph_digest):
            raise ValueError("dependency graph snapshot digest is required")
        if not all(value.strip() for value in required) or self.writer_generation < 1:
            raise ValueError("complete publication evidence linkage is required")


@dataclass(frozen=True, slots=True)
class PublicationGateEvidence:
    definition_id: str
    module_id: str
    version: str
    artifact_digest: str
    owner_scope: OwnerScope
    owner_evidence_id: str
    version_evidence_id: str
    scope_evidence_id: str
    transaction_id: str
    actor_id: str
    decision_owner: str
    writer_generation: int
    correlation_id: str
    owner_durable: bool
    version_durable: bool
    scope_durable: bool
    artifact_immutable: bool
    dependency_graph_valid: bool
    dependency_graph_evidence_id: str
    dependency_graph_digest: str

    @property
    def ready(self) -> bool:
        required = (self.owner_evidence_id, self.version_evidence_id, self.scope_evidence_id,
                    self.transaction_id, self.actor_id, self.decision_owner, self.correlation_id)
        return (
            all(value.strip() for value in required)
            and self.writer_generation > 0
            and self.owner_durable and self.version_durable and self.scope_durable
            and self.artifact_immutable
            and self.dependency_graph_valid
            and bool(self.dependency_graph_evidence_id.strip())
            and bool(_DIGEST_RE.fullmatch(self.dependency_graph_digest))
            and bool(_DIGEST_RE.fullmatch(self.artifact_digest))
        )

    def matches(self, definition: CapabilityDefinition, version: CapabilityVersion) -> bool:
        return (
            self.definition_id == definition.definition_id
            and self.module_id == definition.module_id == version.module_id
            and self.version == version.version
            and self.artifact_digest == version.content_digest
            and definition.owner_scope == self.owner_scope
        )


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    definition_id: str
    version: str | None
    from_state: str
    to_state: str
    actor_id: str
    reason: str
    evidence_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.definition_id, self.from_state, self.to_state,
                                               self.actor_id, self.reason, self.evidence_id,
                                               self.correlation_id)):
            raise ValueError("lifecycle transition requires explicit actor, reason, and evidence")


@dataclass(frozen=True, slots=True)
class NormalizationIssue:
    code: str
    path: str
    message: str

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.path.strip() or not self.message.strip():
            raise ValueError("normalization issue requires code, path, and reason")


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    candidate: CapabilityVersion | None
    issues: tuple[NormalizationIssue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues))
        if (self.candidate is None) == (not self.issues):
            raise ValueError("normalization must yield either a candidate or quarantine reasons")

    @property
    def accepted(self) -> bool:
        return self.candidate is not None and not self.issues


@dataclass(frozen=True, slots=True)
class DefinitionNormalizationResult:
    definition: CapabilityDefinition | None
    issues: tuple[NormalizationIssue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues))
        if (self.definition is None) == (not self.issues):
            raise ValueError("definition normalization must yield a definition or quarantine reasons")

    @property
    def accepted(self) -> bool:
        return self.definition is not None and not self.issues


@dataclass(frozen=True, slots=True)
class DependencyIssue:
    code: str
    module_id: str
    related_module_id: str
    message: str


__all__ = [
    "ActionDeclaration", "CapabilityDefinition", "CapabilityVersion", "DefinitionLifecycle",
    "DependencyIssue", "DefinitionNormalizationResult", "FieldDefinition", "FieldType", "LifecycleTransition", "MaterializeDeclaration",
    "MaterializeFormat", "MaterializeSource", "McpAliasDeclaration", "McpServerDeclaration", "McpToolDeclaration",
    "ModuleMetadata", "ModuleRelation", "ModuleSchema", "NavigationDeclaration", "NormalizationIssue",
    "NormalizationResult", "OwnerScope", "PublishedCapabilitySnapshot", "PublicationGateEvidence",
    "SchemaReference", "ScopeKind", "SeedRowDeclaration", "VersionLifecycle", "ViewDeclaration",
    "ViewKind", "canonical_digest",
]
