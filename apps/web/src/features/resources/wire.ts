/**
 * Wire → screen DTO adapter for the canonical resource API.
 *
 * The API emits snake_case (`ResourceView.as_payload`, `VersionView.as_payload`,
 * `MaterializationPlanView.as_payload`); the screens read camelCase. Every mapping
 * lives here and nowhere else, so the parity suite in
 * `apps/api/tests/migration/web_contract_parity` can compare one file against the
 * serializers instead of chasing fields through the components.
 *
 * Rules:
 * - a field the API does not send is omitted, never defaulted, except where the DTO
 *   declares it required and the screen cannot render without a value (`digest`,
 *   `status`) — those fall back to an explicit, visibly unknown value;
 * - an unknown enum member is passed through untouched: the screen renders it as a
 *   raw token, which is more honest than mapping it to "draft".
 */

import type {
  MaterializationEntry,
  MaterializationPlan,
  MaterializationProvenance,
  ResourceBinding,
  ResourceLifecycle,
  ResourceProvenance,
  ResourceRecord,
  ResourceScope,
  ResourceVersion,
  ResourceVersionStatus,
} from "./types";

type Wire = Readonly<Record<string, unknown>>;

function isRecord(value: unknown): value is Wire {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function count(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function numbers(value: unknown): number[] {
  return Array.isArray(value) ? value.filter((item): item is number => typeof item === "number") : [];
}

function scope(value: unknown): ResourceScope {
  const raw = isRecord(value) ? value : {};
  return { kind: (text(raw.kind) ?? "company") as ResourceScope["kind"], id: text(raw.id) ?? "" };
}

function provenance(value: unknown): ResourceProvenance {
  const raw = isRecord(value) ? value : {};
  return {
    sourceType: text(raw.source_type) ?? "",
    sourceId: text(raw.source_id) ?? "",
    sourceGeneration: count(raw.source_generation) ?? 0,
    digest: text(raw.digest) ?? "",
    ...(text(raw.source_revision) === undefined ? {} : { sourceRevision: text(raw.source_revision) }),
    ...(text(raw.source_path) === undefined ? {} : { sourcePath: text(raw.source_path) }),
    ...(text(raw.captured_at) === undefined ? {} : { capturedAt: text(raw.captured_at) }),
  };
}

function materializationProvenance(value: unknown): MaterializationProvenance {
  const raw: ResourceProvenance = provenance(value);
  return {
    sourceType: raw.sourceType,
    sourceId: raw.sourceId,
    sourceGeneration: raw.sourceGeneration,
    digest: raw.digest,
    ...(raw.sourceRevision === undefined ? {} : { sourceRevision: raw.sourceRevision }),
  };
}

/**
 * A resource as a screen record.
 *
 * `activeVersionNumber` is `active_version` on the wire; `versionNumbers` is
 * `version_numbers`. `updatedAt` is required by the DTO and is the one field the
 * server may omit for a row that never changed, so an empty string is used rather
 * than an invented timestamp.
 */
export function normalizeResourceRecord(raw: unknown): ResourceRecord {
  const value: Wire = isRecord(raw) ? raw : {};
  const active = count(value.active_version);
  return {
    id: text(value.id) ?? "",
    kind: (text(value.kind) ?? "") as ResourceRecord["kind"],
    slug: text(value.slug) ?? "",
    title: text(value.title) ?? "",
    description: text(value.description) ?? "",
    scope: scope(value.scope),
    lifecycle: (text(value.lifecycle) ?? "draft") as ResourceLifecycle,
    ...(active === undefined ? {} : { activeVersionNumber: active }),
    versionNumbers: numbers(value.version_numbers),
    bindingCount: count(value.binding_count) ?? 0,
    updatedAt: text(value.updated_at) ?? "",
    provenance: provenance(value.provenance),
  };
}

/**
 * An immutable version.
 *
 * A version view carries no `provenance` object — the digest *is* its provenance
 * evidence, and the payload is the immutable body. `provenance` on the DTO is therefore
 * derived from the version itself rather than read from a field the server never sends.
 */
export function normalizeResourceVersion(raw: unknown): ResourceVersion {
  const value: Wire = isRecord(raw) ? raw : {};
  const digest = text(value.digest) ?? "";
  const resourceId = text(value.resource_id) ?? "";
  return {
    id: text(value.id) ?? "",
    resourceId,
    number: count(value.number) ?? 0,
    status: (text(value.status) ?? "draft") as ResourceVersionStatus,
    label: text(value.label) ?? "",
    changeNote: text(value.change_note) ?? "",
    payload: (isRecord(value.payload)
      ? value.payload
      : { kind: "prompt", body: "", variables: [] }) as unknown as ResourceVersion["payload"],
    digest,
    createdAt: text(value.created_at) ?? "",
    createdBy: text(value.created_by) ?? "",
    provenance: {
      sourceType: "resource_version",
      sourceId: resourceId,
      sourceGeneration: count(value.number) ?? 0,
      digest,
    },
  };
}

/** A binding edge. `workspace path` is per-binding and optional. */
export function normalizeResourceBinding(raw: unknown): ResourceBinding {
  const value: Wire = isRecord(raw) ? raw : {};
  const target = scope(value.target);
  return {
    id: text(value.id) ?? "",
    resourceId: text(value.resource_id) ?? "",
    projectId: target.kind === "project" ? target.id : "",
    projectName: "",
    target,
    mode: (text(value.mode) ?? "global") as ResourceBinding["mode"],
    versionNumber: count(value.version_number) ?? 0,
    childMayEdit: value.child_may_edit === true,
    updatedAt: text(value.updated_at) ?? text(value.created_at) ?? "",
  };
}

function entry(raw: unknown): MaterializationEntry {
  const value: Wire = isRecord(raw) ? raw : {};
  return {
    id: text(value.id) ?? "",
    resourceId: text(value.resource_id) ?? "",
    kind: (text(value.kind) ?? "") as MaterializationEntry["kind"],
    target: text(value.target) ?? "",
    required: value.required === true,
    status: (text(value.status) ?? "planned") as MaterializationEntry["status"],
    digest: text(value.digest) ?? "",
    provenance: materializationProvenance(value.provenance),
    ...(text(value.diagnostic) === undefined ? {} : { diagnostic: text(value.diagnostic) }),
  };
}

/**
 * A materialization plan.
 *
 * The plan view has no `profileId`, `revisionId`, `projectName`, `updatedAt` or
 * `provenance`: a plan is identified by its `id`/`digest` and by the project it
 * belongs to. Those DTO fields are therefore left empty rather than fabricated, and
 * `status` comes straight from the server (`ready` | `blocked`).
 */
export function normalizeMaterializationPlan(raw: unknown): MaterializationPlan {
  const value: Wire = isRecord(raw) ? raw : {};
  const applied = count(value.applied_generation);
  return {
    id: text(value.id) ?? "",
    profileId: "",
    projectId: text(value.project_id) ?? "",
    projectName: "",
    revisionId: "",
    status: (text(value.status) ?? "blocked") as MaterializationPlan["status"],
    desiredGeneration: count(value.desired_generation) ?? 0,
    ...(applied === undefined ? {} : { appliedGeneration: applied }),
    digest: text(value.digest) ?? "",
    idempotencyKey: text(value.idempotency_key) ?? "",
    createdAt: text(value.created_at) ?? "",
    updatedAt: text(value.created_at) ?? "",
    entries: Array.isArray(value.entries) ? value.entries.map(entry) : [],
    blockers: Array.isArray(value.blockers) ? value.blockers.filter((item): item is string => typeof item === "string") : [],
    provenance: {
      sourceType: "materialization_plan",
      sourceId: text(value.project_id) ?? "",
      sourceGeneration: count(value.desired_generation) ?? 0,
      digest: text(value.snapshot_digest) ?? text(value.digest) ?? "",
    },
  };
}
