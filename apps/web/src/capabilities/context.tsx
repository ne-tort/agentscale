import { createContext, useCallback, useContext, useMemo, useState, type PropsWithChildren } from "react";
import type { DeclarativeAppManifest, GrantSet } from "../../../../packages/schemas/declarative-app/v1";
import { createCapabilityBoundary } from "./boundary";
import { createCapabilityGateway } from "./gateway";
import { shellScope, type ScopeInput, type ScopedContext } from "./scope";
import type { CapabilityGateway, CapabilityGatewayTransport, CapabilityProviderValue } from "./types";

const CapabilityContext = createContext<CapabilityProviderValue | undefined>(undefined);

export interface CapabilityProviderProps extends PropsWithChildren {
  manifest?: DeclarativeAppManifest;
  grants?: GrantSet;
  manifestLoader?: () => Promise<DeclarativeAppManifest>;
  transport: CapabilityGatewayTransport;
  /**
   * Server-attested scope for this app instance. When absent, the provider derives an
   * `unverified` scope from `shellContext`, so every capability that needs authoritative tenant
   * scope is refused instead of trusting React state.
   */
  scope?: ScopedContext;
  /** Shell-selected (UX) scope, e.g. `useWorkContext()`. Never treated as authorization. */
  shellContext?: ScopeInput;
}

export function CapabilityProvider({ children, manifest: initialManifest, grants, manifestLoader, transport, scope, shellContext }: CapabilityProviderProps) {
  const [manifest, setManifest] = useState(initialManifest);
  const boundary = useMemo(() => manifest && grants ? createCapabilityBoundary(manifest, grants) : undefined, [grants, manifest]);
  const resolvedScope = useMemo<ScopedContext>(
    () => scope ?? shellScope(shellContext),
    [scope, shellContext],
  );
  const gateway = useMemo<CapabilityGateway>(
    () => createCapabilityGateway(transport, () => boundary, { scope: () => resolvedScope }),
    [boundary, resolvedScope, transport],
  );
  const reloadManifest = useCallback(async () => {
    if (!manifestLoader) {
      if (!manifest) throw new Error("No manifest loader configured");
      return manifest;
    }
    const next = await manifestLoader();
    setManifest(next);
    return next;
  }, [manifest, manifestLoader]);
  const value = useMemo<CapabilityProviderValue>(() => ({ manifest, grants, boundary, gateway, scope: resolvedScope, reloadManifest }), [boundary, gateway, grants, manifest, reloadManifest, resolvedScope]);
  return <CapabilityContext.Provider value={value}>{children}</CapabilityContext.Provider>;
}

export function useCapabilities(): CapabilityProviderValue {
  const value = useContext(CapabilityContext);
  if (!value) throw new Error("useCapabilities must be used inside CapabilityProvider");
  return value;
}

export function useCapabilityGateway(): CapabilityGateway {
  return useCapabilities().gateway;
}

/** Scope attached to this app instance; `unverified` until the server bridge attests it. */
export function useCapabilityScope(): ScopedContext {
  return useCapabilities().scope ?? shellScope();
}
