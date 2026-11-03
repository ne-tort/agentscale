import type { CapabilityConstraints, CapabilityName } from "../../../../packages/schemas/declarative-app/v1";
import type { ScopeDimension } from "./scope";

/**
 * Canonical capability name <-> constraint contract for declarative-app protocol v1.
 *
 * The v1 protocol defines a capability name and a free-form constraint bag. It does not define
 * which constraint keys are meaningful for which capability, so a grant such as
 * `secrets.read` + `{ methods: [...] }` used to look restricted while the only enforced dimension
 * was ignored. The table below is the client-side mapping the boundary enforces:
 *
 * - a constraint key that the capability does not support is a malformed request/grant (deny);
 * - a capability with `authoritativeScope` may only run with a server-verified scope;
 * - `preview: "denied"` capabilities are not available to preview instances.
 *
 * This mapping is *not* proof that canonical persistence preserves v1 names and constraints. Until
 * the durable mapping exists (T07/T08 blocker), it can only fail closed; it can never widen a grant.
 */

export const CONSTRAINT_KEYS = Object.freeze(["resources", "methods", "domains", "eventTypes"] as const);
export type ConstraintKey = (typeof CONSTRAINT_KEYS)[number];

export interface CapabilityContract {
  readonly name: CapabilityName;
  /** Constraint keys this capability understands; everything else is a mismatch. */
  readonly constraints: readonly ConstraintKey[];
  /** Whether `constraints.maxBytesPerRequest` is meaningful for this capability. */
  readonly maxBytes: boolean;
  /** Empty for UX-only capabilities. Non-empty means an authoritative server scope is required. */
  readonly authoritativeScope: readonly ScopeDimension[];
  readonly mutations: boolean;
  readonly preview: "allowed" | "denied";
}

const TENANT: readonly ScopeDimension[] = Object.freeze(["tenantId"]);
const TENANT_CABINET: readonly ScopeDimension[] = Object.freeze(["tenantId", "cabinetId"]);
const NONE: readonly ScopeDimension[] = Object.freeze([]);

const CONTRACTS: readonly CapabilityContract[] = Object.freeze([
  Object.freeze({ name: "ui.render" as const, constraints: Object.freeze(["resources"] as const), maxBytes: false, authoritativeScope: NONE, mutations: false, preview: "allowed" as const }),
  Object.freeze({ name: "ui.navigation" as const, constraints: Object.freeze(["resources"] as const), maxBytes: false, authoritativeScope: NONE, mutations: false, preview: "allowed" as const }),
  Object.freeze({ name: "storage.kv" as const, constraints: Object.freeze(["resources"] as const), maxBytes: true, authoritativeScope: TENANT, mutations: false, preview: "allowed" as const }),
  Object.freeze({ name: "storage.files" as const, constraints: Object.freeze(["resources"] as const), maxBytes: true, authoritativeScope: TENANT_CABINET, mutations: false, preview: "allowed" as const }),
  Object.freeze({ name: "network.fetch" as const, constraints: Object.freeze(["domains", "methods"] as const), maxBytes: true, authoritativeScope: TENANT, mutations: false, preview: "denied" as const }),
  Object.freeze({ name: "events.subscribe" as const, constraints: Object.freeze(["eventTypes", "resources"] as const), maxBytes: false, authoritativeScope: TENANT, mutations: false, preview: "allowed" as const }),
  Object.freeze({ name: "platform.commands" as const, constraints: Object.freeze(["methods"] as const), maxBytes: false, authoritativeScope: TENANT, mutations: true, preview: "denied" as const }),
  Object.freeze({ name: "platform.queries" as const, constraints: Object.freeze(["methods"] as const), maxBytes: false, authoritativeScope: TENANT, mutations: false, preview: "allowed" as const }),
  Object.freeze({ name: "secrets.read" as const, constraints: Object.freeze(["resources"] as const), maxBytes: false, authoritativeScope: TENANT, mutations: false, preview: "denied" as const }),
  Object.freeze({ name: "identity.read" as const, constraints: Object.freeze(["resources"] as const), maxBytes: false, authoritativeScope: TENANT, mutations: false, preview: "allowed" as const }),
]);

export const CAPABILITY_CONTRACTS: ReadonlyMap<CapabilityName, CapabilityContract> = new Map(
  CONTRACTS.map((contract) => [contract.name, contract] as const),
);

export const CAPABILITY_NAMES: readonly CapabilityName[] = Object.freeze(CONTRACTS.map((contract) => contract.name));

/** Capabilities that are scoped by a method list; an unbounded grant for them is malformed. */
export const METHOD_SCOPED_CAPABILITIES: ReadonlySet<CapabilityName> = new Set<CapabilityName>([
  "platform.commands",
  "platform.queries",
  "network.fetch",
]);

export function isKnownCapability(value: unknown): value is CapabilityName {
  return typeof value === "string" && CAPABILITY_CONTRACTS.has(value as CapabilityName);
}

export function capabilityContract(name: string): CapabilityContract | undefined {
  return CAPABILITY_CONTRACTS.get(name as CapabilityName);
}

export function capabilityConstraintKeys(name: string): readonly ConstraintKey[] {
  return capabilityContract(name)?.constraints ?? [];
}

export function capabilityAcceptsConstraint(name: string, key: ConstraintKey): boolean {
  return capabilityConstraintKeys(name).includes(key);
}

export function capabilityRequiresAuthoritativeScope(name: string): readonly ScopeDimension[] {
  return capabilityContract(name)?.authoritativeScope ?? NONE;
}

export function capabilityIsPreviewAllowed(name: string): boolean {
  return capabilityContract(name)?.preview === "allowed";
}

/**
 * Lists constraint keys present in a request/grant that the capability does not support.
 * A non-empty result is a mismatch and must deny, never be ignored.
 */
export function unsupportedConstraintKeys(name: string, constraints: CapabilityConstraints | undefined): string[] {
  const contract = capabilityContract(name);
  if (!contract) return [...CONSTRAINT_KEYS, "maxBytesPerRequest"];
  if (!constraints) return [];
  const unsupported: string[] = [];
  for (const key of CONSTRAINT_KEYS) {
    if (constraints[key] !== undefined && !contract.constraints.includes(key)) unsupported.push(key);
  }
  if (constraints.maxBytesPerRequest !== undefined && !contract.maxBytes) unsupported.push("maxBytesPerRequest");
  return unsupported;
}

/** Stable reason code for a capability/constraint mismatch, or `undefined` when the pair matches. */
export function capabilityConstraintMismatch(name: string, constraints: CapabilityConstraints | undefined): string | undefined {
  const contract = capabilityContract(name);
  if (!contract) return "unknown_capability";
  const unsupported = unsupportedConstraintKeys(name, constraints);
  if (unsupported.length) return `unsupported_constraint:${unsupported.join(",")}`;
  return undefined;
}
