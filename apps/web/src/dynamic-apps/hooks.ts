import { useCallback, useReducer } from "react";
import type { DynamicAppRegistryContract, DynamicAppRegistryQuery, GrantCapabilityInput, PublishDynamicAppInput, RevokeCapabilityInput } from "./contracts";
import { createInitialDynamicAppsState, reduceDynamicAppsState } from "./state";

export function useDynamicApps(registry: DynamicAppRegistryContract, initialQuery: DynamicAppRegistryQuery = {}) {
  const [state, dispatch] = useReducer(reduceDynamicAppsState, createInitialDynamicAppsState(initialQuery));

  const list = useCallback(async (query: DynamicAppRegistryQuery = initialQuery) => {
    dispatch({ type: "load", query });
    try {
      const page = await registry.list(query);
      dispatch({ type: "loaded", page });
      return page;
    } catch (error) {
      dispatch({ type: "failed", message: error instanceof Error ? error.message : "Unable to load app registry" });
      throw error;
    }
  }, [initialQuery, registry]);

  const open = useCallback(async (appId: string) => {
    dispatch({ type: "load-detail", appId });
    try {
      const detail = await registry.detail(appId);
      dispatch({ type: "detail-loaded", detail });
      return detail;
    } catch (error) {
      dispatch({ type: "failed", message: error instanceof Error ? error.message : "Unable to load app detail" });
      throw error;
    }
  }, [registry]);

  const mutate = useCallback(async (appId: string, operation: () => Promise<Awaited<ReturnType<DynamicAppRegistryContract["detail"]>>>) => {
    dispatch({ type: "mutate", appId });
    try {
      const detail = await operation();
      dispatch({ type: "mutation-complete", detail });
      return detail;
    } catch (error) {
      dispatch({ type: "failed", message: error instanceof Error ? error.message : "Unable to update app" });
      throw error;
    }
  }, []);

  const publish = useCallback((input: PublishDynamicAppInput) => mutate(input.manifest.appId, () => registry.publish(input)), [mutate, registry]);
  const grant = useCallback((input: GrantCapabilityInput) => mutate(input.appId, () => registry.grant(input)), [mutate, registry]);
  const revoke = useCallback((input: RevokeCapabilityInput) => mutate(input.appId, () => registry.revoke(input)), [mutate, registry]);

  return { state, list, open, publish, grant, revoke };
}
