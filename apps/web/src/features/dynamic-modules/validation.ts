/**
 * Inline validation for the dynamic-module CRUD forms and for the manifest text that
 * an operator submits for publication.
 *
 * The manifest validator is a client-side pre-check of the immutable envelope
 * (`dynamic-ui-implementation-contract.md` §3.1/§3.2). It is deliberately closed:
 * unknown top-level fields, unknown capability names, embedded URLs and non-semver
 * versions are rejected before the request leaves the browser. The server remains the
 * authority; a payload that passed here can still be rejected there.
 *
 * Messages are translated where they are produced: a field error is read by the
 * operator, so it has to be in their language and name the field in their words.
 */

import {
  DYNAMIC_MODULE_APP_ID_PATTERN,
  DYNAMIC_MODULE_CAPABILITY_PATTERN,
  DYNAMIC_MODULE_CAPABILITY_VERSION_PATTERN,
  DYNAMIC_MODULE_SCOPES,
  DYNAMIC_MODULE_SEMVER_PATTERN,
  type DynamicModuleKind,
  type DynamicModuleManifest,
  type DynamicModuleScope,
} from "../../api/dynamic";
import type { Translate } from "../../i18n/catalogue";

export interface ValidationIssue {
  readonly path: string;
  readonly message: string;
}

export type ManifestValidation =
  | { readonly ok: true; readonly manifest: DynamicModuleManifest; readonly issues: readonly [] }
  | { readonly ok: false; readonly issues: readonly ValidationIssue[] };

export type FieldErrors = Readonly<Record<string, string>>;

const MANIFEST_ALLOWED_KEYS = [
  "kind",
  "schema_version",
  "app_id",
  "version",
  "artifact_digest",
  "routes",
  "requested_capabilities",
  "limits",
  "data_policy",
  "screens",
  "metadata",
] as const;

const MANIFEST_REQUIRED_KEYS = [
  "kind",
  "schema_version",
  "app_id",
  "version",
  "routes",
  "requested_capabilities",
  "data_policy",
] as const;

const SCHEMA_VERSION = "1.0";

const LIMIT_KEYS = [
  "requests_per_minute",
  "max_concurrent_queries",
  "max_response_bytes",
] as const;

const DATA_POLICY_SCOPE_VALUES = ["current", "optional", "required", "none"] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function fail(path: string, message: string): ValidationIssue {
  return { path, message };
}

function issueText(issues: readonly ValidationIssue[]): string {
  return issues.map((issue) => `${issue.path}: ${issue.message}`).join("; ");
}

export function validationMessage(issues: readonly ValidationIssue[]): string {
  return issueText(issues);
}

function checkRoute(route: unknown, index: number, issues: ValidationIssue[], t: Translate): void {
  const path = `routes[${index}]`;
  if (!isRecord(route)) {
    issues.push(fail(path, t("dynamicModules.validationRouteObject")));
    return;
  }
  for (const key of Object.keys(route)) {
    if (key !== "path" && key !== "screen") issues.push(fail(`${path}.${key}`, t("dynamicModules.validationRouteUnknownField")));
  }
  if (typeof route.path !== "string" || !route.path.startsWith("/") || route.path.startsWith("//")) {
    issues.push(fail(`${path}.path`, t("dynamicModules.validationRoutePathAbsolute")));
  } else if (/[\\?#\u0000-\u001f\u007f]/.test(route.path) || route.path.split("/").some((segment) => segment === "." || segment === "..")) {
    issues.push(fail(`${path}.path`, t("dynamicModules.validationRoutePathContent")));
  }
  if (typeof route.screen !== "string" || route.screen.trim().length === 0) {
    issues.push(fail(`${path}.screen`, t("dynamicModules.validationRouteScreen")));
  }
}

function checkCapability(capability: unknown, index: number, issues: ValidationIssue[], t: Translate): void {
  const path = `requested_capabilities[${index}]`;
  if (!isRecord(capability)) {
    issues.push(fail(path, t("dynamicModules.validationCapabilityObject")));
    return;
  }
  for (const key of Object.keys(capability)) {
    if (key !== "id" && key !== "version") issues.push(fail(`${path}.${key}`, t("dynamicModules.validationCapabilityUnknownField")));
  }
  if (typeof capability.id !== "string" || !DYNAMIC_MODULE_CAPABILITY_PATTERN.test(capability.id)) {
    issues.push(fail(`${path}.id`, t("dynamicModules.validationCapabilityId")));
  }
  const version = typeof capability.version === "number" ? String(capability.version) : capability.version;
  if (typeof version !== "string" || !DYNAMIC_MODULE_CAPABILITY_VERSION_PATTERN.test(version)) {
    issues.push(fail(`${path}.version`, t("dynamicModules.validationCapabilityVersion")));
  }
}

function checkLimits(limits: unknown, issues: ValidationIssue[], t: Translate): void {
  if (limits === undefined) return;
  if (!isRecord(limits)) {
    issues.push(fail("limits", t("dynamicModules.validationLimitsObject")));
    return;
  }
  for (const key of Object.keys(limits)) {
    if (!(LIMIT_KEYS as readonly string[]).includes(key)) issues.push(fail(`limits.${key}`, t("dynamicModules.validationLimitUnknownField")));
  }
  for (const key of LIMIT_KEYS) {
    const value = limits[key];
    if (value === undefined) continue;
    if (!Number.isInteger(value) || (value as number) <= 0) issues.push(fail(`limits.${key}`, t("dynamicModules.validationLimitPositive")));
  }
}

function checkDataPolicy(policy: unknown, issues: ValidationIssue[], t: Translate): void {
  if (!isRecord(policy)) {
    issues.push(fail("data_policy", t("dynamicModules.validationPolicyObject")));
    return;
  }
  for (const key of Object.keys(policy)) {
    if (key !== "project_scope" && key !== "session_scope") issues.push(fail(`data_policy.${key}`, t("dynamicModules.validationPolicyUnknownField")));
  }
  for (const key of ["project_scope", "session_scope"] as const) {
    const value = policy[key];
    if (value === undefined) continue;
    if (typeof value !== "string" || !(DATA_POLICY_SCOPE_VALUES as readonly string[]).includes(value)) {
      issues.push(fail(`data_policy.${key}`, t("dynamicModules.validationPolicyScope", { values: DATA_POLICY_SCOPE_VALUES.join(", ") })));
    }
  }
}

function checkScreens(screens: unknown, issues: ValidationIssue[], t: Translate): void {
  if (screens === undefined) return;
  if (!Array.isArray(screens)) {
    issues.push(fail("screens", t("dynamicModules.validationScreensArray")));
    return;
  }
  const seen = new Set<string>();
  screens.forEach((screen, index) => {
    if (!isRecord(screen) || typeof screen.id !== "string" || screen.id.trim().length === 0) {
      issues.push(fail(`screens[${index}].id`, t("dynamicModules.validationScreenId")));
      return;
    }
    if (seen.has(screen.id)) issues.push(fail(`screens[${index}].id`, t("dynamicModules.validationScreenDuplicate")));
    seen.add(screen.id);
    if (!isRecord(screen.root)) issues.push(fail(`screens[${index}].root`, t("dynamicModules.validationScreenRoot")));
  });
}

/**
 * Validates an already-parsed manifest against the publication contract for a
 * specific definition. `kind` and `app_id` must match the definition being published:
 * publishing cannot move a definition to another app id.
 */
export function validateManifestValue(
  value: unknown,
  expected: { readonly appId: string; readonly kind?: DynamicModuleKind },
  t: Translate,
): ManifestValidation {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) {
    return { ok: false, issues: [fail("manifest", t("dynamicModules.validationManifestObject"))] };
  }

  for (const key of Object.keys(value)) {
    if (!(MANIFEST_ALLOWED_KEYS as readonly string[]).includes(key)) {
      issues.push(fail(key, t("dynamicModules.validationUnknownField")));
    }
  }
  for (const key of MANIFEST_REQUIRED_KEYS) {
    if (value[key] === undefined) issues.push(fail(key, t("dynamicModules.validationRequiredField")));
  }

  if (value.kind !== undefined && value.kind !== expected.kind) {
    issues.push(fail("kind", t("dynamicModules.validationKindMismatch", { kind: expected.kind ?? "—" })));
  }
  if (value.schema_version !== undefined && value.schema_version !== SCHEMA_VERSION) {
    issues.push(fail("schema_version", t("dynamicModules.validationSchemaVersion", { version: SCHEMA_VERSION })));
  }
  if (value.app_id !== undefined) {
    if (typeof value.app_id !== "string" || !DYNAMIC_MODULE_APP_ID_PATTERN.test(value.app_id)) {
      issues.push(fail("app_id", t("dynamicModules.validationAppIdFormat")));
    } else if (value.app_id !== expected.appId) {
      issues.push(fail("app_id", t("dynamicModules.validationAppIdMismatch")));
    }
  }
  if (value.version !== undefined) {
    if (typeof value.version !== "string" || !DYNAMIC_MODULE_SEMVER_PATTERN.test(value.version)) {
      issues.push(fail("version", t("dynamicModules.validationSemver")));
    }
  }
  if (value.artifact_digest !== undefined) {
    const digest = value.artifact_digest;
    if (typeof digest !== "string" || !/^sha256:[0-9a-f]{64}$/.test(digest)) {
      issues.push(fail("artifact_digest", t("dynamicModules.validationDigest")));
    }
  }
  if (value.metadata !== undefined) {
    if (!isRecord(value.metadata) || Object.values(value.metadata).some((entry) => typeof entry !== "string")) {
      issues.push(fail("metadata", t("dynamicModules.validationMetadata")));
    }
  }

  if (Array.isArray(value.routes)) {
    if (value.routes.length === 0) issues.push(fail("routes", t("dynamicModules.validationRoutesRequired")));
    value.routes.forEach((route, index) => checkRoute(route, index, issues, t));
    const paths = value.routes.filter((route) => isRecord(route) && typeof route.path === "string").map((route) => (route as { path: string }).path);
    if (new Set(paths).size !== paths.length) issues.push(fail("routes", t("dynamicModules.validationRouteDuplicate")));
  } else if (value.routes !== undefined) {
    issues.push(fail("routes", t("dynamicModules.validationRoutesArray")));
  }

  if (Array.isArray(value.requested_capabilities)) {
    value.requested_capabilities.forEach((capability, index) => checkCapability(capability, index, issues, t));
    const ids = value.requested_capabilities
      .filter((capability) => isRecord(capability) && typeof capability.id === "string")
      .map((capability) => `${(capability as { id: string }).id}@${String((capability as { version: unknown }).version)}`);
    if (new Set(ids).size !== ids.length) issues.push(fail("requested_capabilities", t("dynamicModules.validationCapabilityDuplicate")));
  } else if (value.requested_capabilities !== undefined) {
    issues.push(fail("requested_capabilities", t("dynamicModules.validationCapabilitiesArray")));
  }

  if (value.data_policy !== undefined) checkDataPolicy(value.data_policy, issues, t);
  checkLimits(value.limits, issues, t);
  checkScreens(value.screens, issues, t);

  if (issues.length > 0) return { ok: false, issues };
  return { ok: true, manifest: value as DynamicModuleManifest, issues: [] };
}

/**
 * Parses the operator's manifest text. `expected` is the definition the manifest is
 * being published for, so a copy/paste of another app's manifest fails here.
 */
export function parseManifestText(
  text: string,
  expected: { readonly appId: string; readonly kind?: DynamicModuleKind },
  t: Translate,
): ManifestValidation {
  if (text.trim().length === 0) {
    return { ok: false, issues: [fail("manifest", t("dynamicModules.validationManifestRequired"))] };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text) as unknown;
  } catch (cause) {
    const detail = cause instanceof Error ? cause.message : "JSON";
    return { ok: false, issues: [fail("manifest", t("dynamicModules.validationInvalidJson", { detail }))] };
  }
  return validateManifestValue(parsed, expected, t);
}

export function manifestDraftText(manifest: DynamicModuleManifest | undefined): string {
  return manifest ? JSON.stringify(manifest, null, 2) : "";
}

/**
 * Editable starter envelope for a new version. It is only a form default: the
 * operator edits it and the same closed validation runs on submit.
 */
export function starterManifestText(input: {
  readonly appId: string;
  /**
   * Optional on purpose: the API reports ``kind`` as optional, because the definition
   * row has no kind column and the server cannot always infer one. A starter template
   * must not invent a kind it did not read, so the field is simply omitted.
   */
  readonly kind?: DynamicModuleKind;
  readonly version: string;
}): string {
  const template = {
    ...(input.kind ? { kind: input.kind } : {}),
    schema_version: SCHEMA_VERSION,
    app_id: input.appId,
    version: input.version,
    routes: [{ path: `/${input.appId}/home`, screen: "home" }],
    requested_capabilities: [{ id: `${input.appId.replace(/-/g, ".")}.read`, version: "1" }],
    limits: { requests_per_minute: 60, max_concurrent_queries: 4, max_response_bytes: 1048576 },
    data_policy: { project_scope: "current" },
    screens: [],
  };
  return JSON.stringify(template, null, 2);
}


/* -------------------------------------------------------------------------- */
/* Form validation                                                            */
/* -------------------------------------------------------------------------- */

export interface PublishFormValues {
  readonly manifestText: string;
  readonly notes: string;
  /** Latest version the operator had on screen; sent as optimistic concurrency. */
  readonly expectedVersion?: string;
}

export interface InstallFormValues {
  readonly cabinetId: string;
  readonly projectId: string;
  readonly scope: DynamicModuleScope | "";
  readonly versionId: string;
}

export interface GrantFormValues {
  readonly cabinetId: string;
  readonly projectId: string;
  readonly scope: DynamicModuleScope | "";
  readonly capabilityId: string;
  readonly capabilityVersion: string;
  readonly expiresAt: string;
  /** Optional audit reason code, never free-form content. */
  readonly reason: string;
}

export interface RevokeFormValues {
  readonly reason: string;
}

export interface ArchiveFormValues {
  readonly reason: string;
}

/**
 * Reason codes, not prose: the audit trail records a stable, redactable identifier and
 * never operator-entered free text (secrets or payload fragments must not reach it).
 */
export const REASON_CODE_PATTERN = /^[a-z][a-z0-9_]{2,63}$/;

const MAX_NOTES_LENGTH = 2000;

function checkReasonCode(value: string, errors: Record<string, string>, t: Translate, field = "reason"): void {
  const trimmed = value.trim();
  if (!trimmed) return;
  if (!REASON_CODE_PATTERN.test(trimmed)) {
    errors[field] = t("dynamicModules.validationReasonCode");
  }
}

export function validatePublishForm(
  values: PublishFormValues,
  expected: { appId: string; kind?: DynamicModuleKind },
  t: Translate,
): FieldErrors {
  const errors: Record<string, string> = {};
  const manifest = parseManifestText(values.manifestText, expected, t);
  if (!manifest.ok) errors.manifest = validationMessage(manifest.issues);
  if (values.notes.trim().length > MAX_NOTES_LENGTH) errors.notes = t("dynamicModules.validationNotesTooLong", { max: MAX_NOTES_LENGTH });
  return errors;
}

export function validateInstallForm(
  values: InstallFormValues,
  knownVersionIds: readonly string[],
  t: Translate,
): FieldErrors {
  const errors: Record<string, string> = {};
  if (!values.versionId) errors.versionId = t("dynamicModules.validationSelectVersion");
  else if (!knownVersionIds.includes(values.versionId)) errors.versionId = t("dynamicModules.validationUnknownVersion");
  if (!values.cabinetId) errors.cabinetId = t("dynamicModules.validationSelectCabinet");
  if (!values.scope) errors.scope = t("dynamicModules.validationSelectScope");
  if (values.scope === "project" && !values.projectId) errors.projectId = t("dynamicModules.validationProjectRequired");
  if (values.scope === "cabinet" && values.projectId) errors.projectId = t("dynamicModules.validationProjectNotForCabinet");
  return errors;
}

export function validateGrantForm(values: GrantFormValues, t: Translate): FieldErrors {
  const errors: Record<string, string> = {};
  if (!values.capabilityId.trim()) errors.capabilityId = t("dynamicModules.validationCapabilityRequired");
  else if (!DYNAMIC_MODULE_CAPABILITY_PATTERN.test(values.capabilityId.trim())) {
    errors.capabilityId = t("dynamicModules.validationCapabilityIdFormat");
  }
  if (!values.capabilityVersion.trim()) errors.capabilityVersion = t("dynamicModules.validationCapabilityVersionRequired");
  else if (!DYNAMIC_MODULE_CAPABILITY_VERSION_PATTERN.test(values.capabilityVersion.trim())) {
    errors.capabilityVersion = t("dynamicModules.validationCapabilityVersionPositive");
  }
  if (!values.cabinetId) errors.cabinetId = t("dynamicModules.validationCabinetRequired");
  if (!values.scope) errors.scope = t("dynamicModules.validationSelectScope");
  else if (!(DYNAMIC_MODULE_SCOPES as readonly string[]).includes(values.scope)) errors.scope = t("dynamicModules.validationScopeInvalid");
  if (values.scope === "project" && !values.projectId) errors.projectId = t("dynamicModules.validationProjectRequired");
  if (values.scope === "cabinet" && values.projectId) errors.projectId = t("dynamicModules.validationProjectNotForCabinet");
  if (values.expiresAt.trim()) {
    const parsed = Date.parse(values.expiresAt.trim());
    if (!Number.isFinite(parsed)) errors.expiresAt = t("dynamicModules.validationExpiryFormat");
    else if (parsed <= Date.now()) errors.expiresAt = t("dynamicModules.validationExpiryFuture");
  }
  checkReasonCode(values.reason, errors, t);
  return errors;
}

export function validateRevokeForm(values: RevokeFormValues, t: Translate): FieldErrors {
  const errors: Record<string, string> = {};
  checkReasonCode(values.reason, errors, t);
  return errors;
}

export function validateArchiveForm(values: ArchiveFormValues, t: Translate): FieldErrors {
  const errors: Record<string, string> = {};
  checkReasonCode(values.reason, errors, t);
  return errors;
}

export function hasFieldErrors(errors: FieldErrors): boolean {
  return Object.keys(errors).length > 0;
}

export function firstFieldError(errors: FieldErrors): string | undefined {
  const key = Object.keys(errors)[0];
  return key === undefined ? undefined : errors[key];
}
