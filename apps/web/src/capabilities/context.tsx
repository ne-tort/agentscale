import { createContext, useCallback, useContext, useMemo, useState, type PropsWithChildren } from "react";
import type { DeclarativeAppManifest, GrantSet } from "../../../../packages/schemas/declarative-app/v1";
import { createCapabilityBoundary } from "./boundary";
import { createCapabilityGateway } from "./gateway";
import type { CapabilityGateway, CapabilityGatewayTransport, CapabilityProviderValue } from "./types";

const CapabilityContext = createContext<CapabilityProviderValue | undefined>(undefined);

export interface CapabilityProviderProps extends PropsWithChildren {
  manifest?: DeclarativeAppManifest;
  grants?: GrantSet;
  manifestLoader?: () => Promise<DeclarativeAppManifest>;
  transport: CapabilityGatewayTransport;
}

export function CapabilityProvider({ children, manifest: initialManifest, grants, manifestLoader, transport }: CapabilityProviderProps) {
  const [manifest, setManifest] = useState(initialManifest);
  const boundary = useMemo(() => manifest && grants ? createCapabilityBoundary(manifest, grants) : undefined, [grants, manifest]);
  const gateway = useMemo<CapabilityGateway>(() => createCapabilityGateway(transport, () => boundary), [boundary, transport]);
  const reloadManifest = useCallback(async () => {
    if (!manifestLoader) {
      if (!manifest) throw new Error("No manifest loader configured");
      return manifest;
    }
    const next = await manifestLoader();
    setManifest(next);
    return next;
  }, [manifest, manifestLoader]);
  const value = useMemo<CapabilityProviderValue>(() => ({ manifest, grants, boundary, gateway, reloadManifest }), [boundary, gateway, grants, manifest, reloadManifest]);
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
