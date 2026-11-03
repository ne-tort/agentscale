/**
 * Host-provided capability access for the dynamic-module screens.
 *
 * The shell does not mount a `CapabilityProvider` today, so the context default is
 * `DENY_DYNAMIC_MODULE_ACCESS`. A dynamic host (registry / declarative runtime) wraps
 * these screens in `DynamicModuleAccessProvider` with the effective boundary it
 * already resolves; the screens never invent a grant on their own.
 */

import { createContext, useContext, type PropsWithChildren } from "react";
import {
  createDynamicModuleAccess,
  DENY_DYNAMIC_MODULE_ACCESS,
  type DynamicModuleAccess,
} from "./capability";
import type { CapabilityBoundary } from "../../capabilities/types";

const DynamicModuleAccessContext = createContext<DynamicModuleAccess>(DENY_DYNAMIC_MODULE_ACCESS);

export interface DynamicModuleAccessProviderProps extends PropsWithChildren {
  /** Effective boundary resolved by the host; omitted means "no context". */
  readonly boundary?: CapabilityBoundary;
  /** Explicit access snapshot, used when the host already projected the boundary. */
  readonly access?: DynamicModuleAccess;
}

export function DynamicModuleAccessProvider({ boundary, access, children }: DynamicModuleAccessProviderProps) {
  const value = access ?? createDynamicModuleAccess(boundary);
  return <DynamicModuleAccessContext.Provider value={value}>{children}</DynamicModuleAccessContext.Provider>;
}

export function useDynamicModuleAccess(): DynamicModuleAccess {
  return useContext(DynamicModuleAccessContext);
}
