import { createRequestContext, type ArtifactError, type RequestContextInput } from "../protocol";
import {
  SignedRegistryResolver,
  type RegistryFetcher,
  type RegistryRequest,
  type RegistryResolution,
  type SignedSnapshotVerifier,
} from "../registry";
import { routeToSnapshotPointer } from "./route-adapter";

export interface RegistryClientRequest extends Omit<RegistryRequest, "context" | "effectiveCapabilities"> {
  readonly context: RequestContextInput;
  /** UX-only visibility input. The server remains the authorization boundary. */
  readonly effectiveCapabilities: ReadonlySet<string>;
}

export interface RegistryRouteClientRequest extends Omit<RegistryClientRequest, "appId" | "version" | "routeId"> {
  readonly pathname: string;
  /** Route lookup data is a hint only; the resolver verifies the authoritative snapshot. */
  readonly snapshotHint: unknown;
}

export type RegistryClientResolution = RegistryResolution | {
  readonly state: "ambiguous_route";
  readonly code: "ambiguous_route";
  readonly traceId: string;
};

export interface RegistryControllerOptions {
  readonly fetchSnapshot: RegistryFetcher;
  readonly verifySnapshot: SignedSnapshotVerifier;
  readonly now?: () => number;
  readonly maxContextCaches?: number;
}

export type RegistryInvalidationReason = "disabled" | "rollback" | "grant_revoked" | "context_changed" | "logout" | "manual";
export type RegistryInvalidationListener = (revision: number, reason: RegistryInvalidationReason) => void;

function contextKey(context: ReturnType<typeof createRequestContext>): string {
  return JSON.stringify([context.tenantId, context.cabinetId ?? null, context.projectId ?? null, context.sessionId ?? null]);
}

function traceId(input: unknown): string {
  try {
    if (typeof input !== "object" || input === null) return "unavailable";
    const contextDescriptor = Object.getOwnPropertyDescriptor(input, "context");
    if (!contextDescriptor || !("value" in contextDescriptor) || typeof contextDescriptor.value !== "object" || contextDescriptor.value === null) return "unavailable";
    const traceDescriptor = Object.getOwnPropertyDescriptor(contextDescriptor.value, "traceId");
    const value = traceDescriptor && "value" in traceDescriptor ? traceDescriptor.value : undefined;
    return typeof value === "string" && value.trim() ? value : "unavailable";
  } catch {
    return "unavailable";
  }
}

function requestContextError(input: unknown): ArtifactError {
  return Object.freeze({ state: "denied", code: "missing_context", traceId: traceId(input) });
}

export class DynamicRegistryController {
  private readonly caches = new Map<string, SignedRegistryResolver>();
  private readonly activeResolvers = new Map<SignedRegistryResolver, number>();
  private readonly listeners = new Set<RegistryInvalidationListener>();
  private revisionValue = 0;
  private readonly maxContextCaches: number;

  constructor(private readonly options: RegistryControllerOptions) {
    const requestedLimit = options.maxContextCaches;
    this.maxContextCaches = Number.isSafeInteger(requestedLimit) && (requestedLimit as number) > 0
      ? Math.min(requestedLimit as number, 256)
      : 32;
  }

  get revision(): number {
    return this.revisionValue;
  }

  subscribe(listener: RegistryInvalidationListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private resolverFor(key: string): SignedRegistryResolver {
    const cached = this.caches.get(key);
    if (cached) {
      this.caches.delete(key);
      this.caches.set(key, cached);
      return cached;
    }

    const resolver = new SignedRegistryResolver(this.options.fetchSnapshot, this.options.verifySnapshot, this.options.now);
    if (this.caches.size < this.maxContextCaches) {
      this.caches.set(key, resolver);
    } else {
      const evictable = [...this.caches].find(([, value]) => !this.activeResolvers.has(value));
      if (evictable) {
        this.caches.delete(evictable[0]);
        this.caches.set(key, resolver);
      }
    }
    return resolver;
  }

  private async resolvePinned(request: RegistryClientRequest & { readonly appId: string; readonly version: string; readonly routeId: string }): Promise<RegistryResolution> {
    let context: ReturnType<typeof createRequestContext>;
    try {
      context = createRequestContext(request.context);
    } catch {
      return requestContextError(request);
    }

    let effectiveCapabilities: Set<string>;
    try {
      if (request.effectiveCapabilities === undefined || request.effectiveCapabilities === null) throw new TypeError("missing_effective_capabilities");
      effectiveCapabilities = new Set(request.effectiveCapabilities);
    } catch {
      return Object.freeze({ state: "invalid", code: "validation_error", traceId: context.traceId });
    }

    const resolver = this.resolverFor(contextKey(context));
    this.activeResolvers.set(resolver, (this.activeResolvers.get(resolver) ?? 0) + 1);
    try {
      return await resolver.resolve(Object.freeze({
        appId: request.appId,
        version: request.version,
        routeId: request.routeId,
        context,
        effectiveCapabilities,
        ...(request.requiresAuthoritativeScope !== undefined
          ? { requiresAuthoritativeScope: request.requiresAuthoritativeScope }
          : {}),
        ...(request.signal ? { signal: request.signal } : {}),
      }));
    } finally {
      const activeCount = this.activeResolvers.get(resolver) ?? 0;
      if (activeCount <= 1) this.activeResolvers.delete(resolver);
      else this.activeResolvers.set(resolver, activeCount - 1);
    }
  }

  async resolve(request: RegistryClientRequest): Promise<RegistryResolution> {
    try {
      return await this.resolvePinned(request);
    } catch {
      return Object.freeze({ state: "quarantined", code: "quarantined", traceId: traceId(request) });
    }
  }

  async resolveRoute(request: RegistryRouteClientRequest): Promise<RegistryClientResolution> {
    try {
      return await this.resolveRouteChecked(request);
    } catch {
      return Object.freeze({ state: "quarantined", code: "quarantined", traceId: traceId(request) });
    }
  }

  private async resolveRouteChecked(request: RegistryRouteClientRequest): Promise<RegistryClientResolution> {
    let context: ReturnType<typeof createRequestContext>;
    try {
      context = createRequestContext(request.context);
    } catch {
      return requestContextError(request);
    }

    const lookup = routeToSnapshotPointer(request.snapshotHint, request.pathname);
    if (lookup.state === "not_found") return Object.freeze({ state: "unknown_route", code: "unknown_route", traceId: context.traceId });
    if (lookup.state === "ambiguous") return Object.freeze({ state: "ambiguous_route", code: "ambiguous_route", traceId: context.traceId });
    if (lookup.state === "invalid_path" || lookup.state === "invalid_snapshot") return Object.freeze({ state: "invalid", code: "validation_error", traceId: context.traceId });

    const resolved = await this.resolvePinned({
      appId: lookup.pointer.appId,
      version: lookup.pointer.version,
      routeId: lookup.pointer.routeId,
      context,
      effectiveCapabilities: request.effectiveCapabilities,
      ...(request.requiresAuthoritativeScope !== undefined
        ? { requiresAuthoritativeScope: request.requiresAuthoritativeScope }
        : {}),
      ...(request.signal ? { signal: request.signal } : {}),
    });
    if (resolved.state === "resolved" && resolved.route.path !== request.pathname) {
      return Object.freeze({ state: "unknown_route", code: "unknown_route", traceId: context.traceId });
    }
    return resolved;
  }

  invalidate(reason: RegistryInvalidationReason = "manual"): void {
    this.revisionValue += 1;
    for (const resolver of new Set([...this.caches.values(), ...this.activeResolvers.keys()])) resolver.invalidate();
    this.caches.clear();
    for (const listener of this.listeners) {
      try { listener(this.revisionValue, reason); } catch { /* An observer cannot block cache invalidation. */ }
    }
  }

  onDisabled(): void { this.invalidate("disabled"); }
  onRollback(): void { this.invalidate("rollback"); }
  onGrantRevoked(): void { this.invalidate("grant_revoked"); }
  onContextChanged(): void { this.invalidate("context_changed"); }
  onLogout(): void { this.invalidate("logout"); }
}

export function createDynamicRegistryController(options: RegistryControllerOptions): DynamicRegistryController {
  return new DynamicRegistryController(options);
}