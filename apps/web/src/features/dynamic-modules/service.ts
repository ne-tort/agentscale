/**
 * Application service for the dynamic-module CRUD screens.
 *
 * Responsibilities, in this order, for every operation:
 *   1. shell capability gate (`platform.queries` for reads, `platform.commands` for
 *      registry mutations) — refused before a request is built;
 *   2. inline validation of operator input;
 *   3. stable in-memory idempotency key per pending command, reused across retries and
 *      dropped only after the server answered conclusively;
 *   4. typed error mapping with a safe code/message and trace id — never a raw payload.
 *
 * No storage API is used: pending command keys live in the service instance for the
 * lifetime of the mounted screen.
 */

import { ApiRequestError } from "../../api/client";
import {
  createDynamicModulesGateway,
  DynamicModulesApiUnavailableError,
  DynamicModulesProtocolError,
  type ArchiveDynamicModuleDefinitionInput,
  type DynamicModuleDefinition,
  type DynamicModuleDefinitionPage,
  type DynamicModuleDefinitionQuery,
  type DynamicModuleDirectoryEntry,
  type DynamicModuleGrant,
  type DynamicModuleGrantQuery,
  type DynamicModuleInstance,
  type DynamicModuleInstanceQuery,
  type DynamicModuleKind,
  type DynamicModuleVersion,
  type DynamicModulesGateway,
  type DynamicModulesRequestOptions,
  type GrantDynamicModuleCapabilityInput,
  type InstallDynamicModuleInstanceInput,
  type PublishDynamicModuleVersionInput,
  type RevokeDynamicModuleGrantInput,
} from "../../api/dynamic";
import type { ApiClient } from "../../api/types";
import { CapabilityDeniedError } from "../../capabilities/boundary";
import { requireDynamicModuleAccess, type DynamicModuleAccess } from "./capability";
import {
  validateArchiveForm,
  validateGrantForm,
  validateInstallForm,
  validatePublishForm,
  validateRevokeForm,
  hasFieldErrors,
  validationMessage,
  parseManifestText,
  type ArchiveFormValues,
  type FieldErrors,
  type GrantFormValues,
  type InstallFormValues,
  type PublishFormValues,
  type RevokeFormValues,
} from "./validation";

export type DynamicModuleErrorCode =
  | "DYNAMIC_MODULES_API_UNAVAILABLE"
  | "DYNAMIC_MODULES_INVALID_RESPONSE"
  | "CAPABILITY_DENIED"
  | "DYNAMIC_MODULES_VALIDATION_FAILED"
  | "DYNAMIC_MODULES_REQUEST_FAILED";

export class DynamicModuleServiceError extends Error {
  constructor(
    readonly code: DynamicModuleErrorCode,
    message: string,
    readonly status?: number,
    readonly traceId?: string,
  ) {
    super(message);
    this.name = "DynamicModuleServiceError";
  }
}

export class DynamicModuleValidationError extends DynamicModuleServiceError {
  constructor(readonly fieldErrors: FieldErrors) {
    super("DYNAMIC_MODULES_VALIDATION_FAILED", validationMessage(
      Object.entries(fieldErrors).map(([path, message]) => ({ path, message })),
    ));
    this.name = "DynamicModuleValidationError";
  }
}

const UNAVAILABLE_MESSAGE =
  "The dynamic module API is not exposed by this shell build yet. No request was sent and nothing was changed.";

function isConflict(error: unknown): boolean {
  return error instanceof ApiRequestError
    && (error.error.status === 409 || /CONFLICT|STALE|PRECONDITION/i.test(error.error.code));
}

/** Maps any thrown value to a typed, redacted service error. */
export function toDynamicModuleError(error: unknown): DynamicModuleServiceError {
  if (error instanceof DynamicModuleServiceError) return error;
  if (error instanceof CapabilityDeniedError) {
    return new DynamicModuleServiceError("CAPABILITY_DENIED", `Capability is not granted: ${error.capability}`);
  }
  if (error instanceof DynamicModulesApiUnavailableError) {
    return new DynamicModuleServiceError(error.code, UNAVAILABLE_MESSAGE);
  }
  if (error instanceof DynamicModulesProtocolError) {
    return new DynamicModuleServiceError(error.code, `The registry returned an unexpected payload (${error.field}). No state was applied.`);
  }
  if (error instanceof ApiRequestError) {
    const payload = error.error;
    return new DynamicModuleServiceError(
      "DYNAMIC_MODULES_REQUEST_FAILED",
      payload.detail ?? payload.title ?? "The dynamic module request failed.",
      payload.status,
      typeof payload.trace_id === "string" ? payload.trace_id : undefined,
    );
  }
  if (error instanceof DOMException && error.name === "AbortError") {
    return new DynamicModuleServiceError("DYNAMIC_MODULES_REQUEST_FAILED", "The request was cancelled.");
  }
  return new DynamicModuleServiceError(
    "DYNAMIC_MODULES_REQUEST_FAILED",
    error instanceof Error && error.message ? error.message : "The dynamic module request failed.",
  );
}

export function isDynamicModuleApiUnavailable(error: unknown): boolean {
  return toDynamicModuleError(error).code === "DYNAMIC_MODULES_API_UNAVAILABLE";
}

/** Stable, non-secret request key. `crypto.randomUUID` is used when available. */
export function createIdempotencyKey(prefix = "dyn"): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return `${prefix}-${crypto.randomUUID()}`;
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export interface DynamicModuleDefinitionDetail {
  readonly definition: DynamicModuleDefinition;
  readonly versions: readonly DynamicModuleVersion[];
}

export interface PublishVersionRequest {
  readonly definitionId: string;
  readonly appId: string;
  /**
   * Optional because the API reports the definition kind as optional: the definition
   * row has no kind column, so the server may not report one. Requiring it here forced
   * callers to fabricate a kind to satisfy the type -- the manifest itself carries the
   * kind the operator actually submitted, and the server validates it against the
   * definition.
   */
  readonly kind?: DynamicModuleKind;
  readonly values: PublishFormValues;
}

export interface InstallInstanceRequest {
  readonly definitionId: string;
  readonly values: InstallFormValues;
  readonly knownVersionIds: readonly string[];
}

export interface GrantCapabilityRequest {
  readonly definitionId: string;
  readonly values: GrantFormValues;
  readonly instanceId?: string;
}

export interface RevokeGrantRequest {
  readonly definitionId: string;
  readonly grantId: string;
  readonly values: RevokeFormValues;
}

export interface ArchiveDefinitionRequest {
  readonly definitionId: string;
  readonly values: ArchiveFormValues;
  readonly expectedVersion?: string;
}

export interface DynamicModulesService {
  listDefinitions(query?: DynamicModuleDefinitionQuery, options?: DynamicModulesRequestOptions): Promise<DynamicModuleDefinitionPage>;
  loadDefinition(definitionId: string, options?: DynamicModulesRequestOptions): Promise<DynamicModuleDefinitionDetail>;
  listInstances(definitionId: string, options?: DynamicModulesRequestOptions): Promise<readonly DynamicModuleInstance[]>;
  listGrants(definitionId: string, options?: DynamicModulesRequestOptions): Promise<readonly DynamicModuleGrant[]>;
  publishVersion(request: PublishVersionRequest, options?: DynamicModulesRequestOptions): Promise<DynamicModuleVersion>;
  installInstance(request: InstallInstanceRequest, options?: DynamicModulesRequestOptions): Promise<DynamicModuleInstance>;
  grantCapability(request: GrantCapabilityRequest, options?: DynamicModulesRequestOptions): Promise<DynamicModuleGrant>;
  revokeGrant(request: RevokeGrantRequest, options?: DynamicModulesRequestOptions): Promise<DynamicModuleGrant>;
  archiveDefinition(request: ArchiveDefinitionRequest, options?: DynamicModulesRequestOptions): Promise<DynamicModuleDefinition>;
  listCabinets(options?: DynamicModulesRequestOptions): Promise<readonly DynamicModuleDirectoryEntry[]>;
  listProjects(cabinetId: string, options?: DynamicModulesRequestOptions): Promise<readonly DynamicModuleDirectoryEntry[]>;
  /** Test/diagnostics hook: number of retained pending command keys. */
  pendingCommandCount(): number;
}

export interface DynamicModulesServiceOptions {
  readonly api: ApiClient | undefined;
  readonly access: DynamicModuleAccess;
  readonly gateway?: DynamicModulesGateway;
  readonly now?: () => number;
}

function assertValid(errors: FieldErrors): void {
  if (hasFieldErrors(errors)) throw new DynamicModuleValidationError(errors);
}

export function createDynamicModulesService(options: DynamicModulesServiceOptions): DynamicModulesService {
  const gateway = options.gateway ?? createDynamicModulesGateway(options.api);
  const access = options.access;
  const pendingKeys = new Map<string, string>();

  const retainedKey = (scope: string): string => {
    const existing = pendingKeys.get(scope);
    if (existing) return existing;
    const created = createIdempotencyKey("dyncmd");
    pendingKeys.set(scope, created);
    return created;
  };
  const releaseKey = (scope: string): void => { pendingKeys.delete(scope); };

  const withoutPendingScope = <T>(promise: Promise<T>, scope: string): Promise<T> =>
    promise.then(
      (result) => { releaseKey(scope); return result; },
      (error: unknown) => { if (isConflict(error)) releaseKey(scope); throw error; },
    );

  return {
    pendingCommandCount: () => pendingKeys.size,

    async listDefinitions(query = {}, requestOptions) {
      requireDynamicModuleAccess(access, "read");
      return gateway.listDefinitions(query, requestOptions);
    },

    async loadDefinition(definitionId, requestOptions) {
      requireDynamicModuleAccess(access, "read");
      if (!definitionId) throw new DynamicModuleServiceError("DYNAMIC_MODULES_VALIDATION_FAILED", "A definition id is required.");
      const [definition, versions] = await Promise.all([
        gateway.getDefinition(definitionId, requestOptions),
        gateway.listVersions(definitionId, requestOptions),
      ]);
      return { definition, versions };
    },

    async listInstances(definitionId, requestOptions) {
      requireDynamicModuleAccess(access, "read");
      const query: DynamicModuleInstanceQuery = { definitionId };
      return gateway.listInstances(query, requestOptions);
    },

    async listGrants(definitionId, requestOptions) {
      requireDynamicModuleAccess(access, "read");
      const query: DynamicModuleGrantQuery = { definitionId };
      return gateway.listGrants(query, requestOptions);
    },

    async publishVersion(request, requestOptions) {
      requireDynamicModuleAccess(access, "manage");
      const { definitionId, appId, kind, values } = request;
      assertValid(validatePublishForm(values, { appId, kind }));
      const parsed = parseManifestText(values.manifestText, { appId, kind });
      if (!parsed.ok) throw new DynamicModuleValidationError({ manifest: validationMessage(parsed.issues) });
      const scope = `publish:${definitionId}`;
      const expectedVersion = values.expectedVersion?.trim();
      const input: PublishDynamicModuleVersionInput = {
        manifest: parsed.manifest,
        idempotencyKey: retainedKey(scope),
        ...(values.notes.trim() ? { notes: values.notes.trim() } : {}),
        ...(expectedVersion ? { expectedVersion } : {}),
      };
      return withoutPendingScope(gateway.publishVersion(definitionId, input, requestOptions), scope);
    },

    async installInstance(request, requestOptions) {
      requireDynamicModuleAccess(access, "manage");
      const { definitionId, values, knownVersionIds } = request;
      assertValid(validateInstallForm(values, knownVersionIds));
      const scope = `install:${definitionId}`;
      const input: InstallDynamicModuleInstanceInput = {
        definitionId,
        versionId: values.versionId,
        cabinetId: values.cabinetId,
        ...(values.scope === "project" ? { projectId: values.projectId } : {}),
        scope: values.scope === "project" ? "project" : "cabinet",
        idempotencyKey: retainedKey(scope),
      };
      return withoutPendingScope(gateway.installInstance(input, requestOptions), scope);
    },

    async grantCapability(request, requestOptions) {
      requireDynamicModuleAccess(access, "manage");
      const { definitionId, values, instanceId } = request;
      assertValid(validateGrantForm(values));
      const scope = `grant:${definitionId}`;
      const input: GrantDynamicModuleCapabilityInput = {
        definitionId,
        ...(instanceId ? { instanceId } : {}),
        capabilityId: values.capabilityId.trim(),
        capabilityVersion: values.capabilityVersion.trim(),
        scope: values.scope === "project" ? "project" : "cabinet",
        cabinetId: values.cabinetId,
        ...(values.scope === "project" ? { projectId: values.projectId } : {}),
        ...(values.expiresAt.trim() ? { expiresAt: new Date(values.expiresAt.trim()).toISOString() } : {}),
        ...(values.reason.trim() ? { reason: values.reason.trim() } : {}),
        idempotencyKey: retainedKey(scope),
      };
      return withoutPendingScope(gateway.grantCapability(input, requestOptions), scope);
    },

    async revokeGrant(request, requestOptions) {
      requireDynamicModuleAccess(access, "manage");
      const { definitionId, grantId, values } = request;
      if (!grantId) throw new DynamicModuleValidationError({ grantId: "A grant must be selected before revoking." });
      assertValid(validateRevokeForm(values));
      const scope = `revoke:${grantId}`;
      const input: RevokeDynamicModuleGrantInput = {
        definitionId,
        grantId,
        ...(values.reason.trim() ? { reason: values.reason.trim() } : {}),
        idempotencyKey: retainedKey(scope),
      };
      return withoutPendingScope(gateway.revokeCapability(input, requestOptions), scope);
    },

    async archiveDefinition(request, requestOptions) {
      requireDynamicModuleAccess(access, "manage");
      const { definitionId, values, expectedVersion } = request;
      assertValid(validateArchiveForm(values));
      const scope = `archive:${definitionId}`;
      const input: ArchiveDynamicModuleDefinitionInput = {
        ...(values.reason.trim() ? { reason: values.reason.trim() } : {}),
        ...(expectedVersion === undefined ? {} : { expectedVersion }),
        idempotencyKey: retainedKey(scope),
      };
      return withoutPendingScope(gateway.archiveDefinition(definitionId, input, requestOptions), scope);
    },

    async listCabinets(requestOptions) {
      requireDynamicModuleAccess(access, "read");
      return gateway.listCabinets(requestOptions);
    },

    async listProjects(cabinetId, requestOptions) {
      requireDynamicModuleAccess(access, "read");
      if (!cabinetId) return [];
      return gateway.listProjects(cabinetId, requestOptions);
    },
  };
}

