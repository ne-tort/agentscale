import type { DynamicAppDetail, DynamicAppRegistryPage, DynamicAppRegistryQuery } from "./contracts";

export type DynamicAppsViewState =
  | { kind: "idle"; query: DynamicAppRegistryQuery }
  | { kind: "loading"; query: DynamicAppRegistryQuery; previous?: DynamicAppRegistryPage }
  | { kind: "ready"; query: DynamicAppRegistryQuery; page: DynamicAppRegistryPage }
  | { kind: "detail-loading"; query: DynamicAppRegistryQuery; appId: string; page?: DynamicAppRegistryPage }
  | { kind: "detail-ready"; query: DynamicAppRegistryQuery; page?: DynamicAppRegistryPage; detail: DynamicAppDetail }
  | { kind: "mutating"; query: DynamicAppRegistryQuery; appId: string; page?: DynamicAppRegistryPage; detail?: DynamicAppDetail }
  | { kind: "error"; query: DynamicAppRegistryQuery; message: string; page?: DynamicAppRegistryPage; detail?: DynamicAppDetail };

export type DynamicAppsViewAction =
  | { type: "load"; query: DynamicAppRegistryQuery }
  | { type: "loaded"; page: DynamicAppRegistryPage }
  | { type: "load-detail"; appId: string }
  | { type: "detail-loaded"; detail: DynamicAppDetail }
  | { type: "mutate"; appId: string }
  | { type: "mutation-complete"; detail: DynamicAppDetail }
  | { type: "failed"; message: string };

export function createInitialDynamicAppsState(query: DynamicAppRegistryQuery = {}): DynamicAppsViewState {
  return { kind: "idle", query };
}

function retainedPage(state: DynamicAppsViewState): DynamicAppRegistryPage | undefined {
  if (state.kind === "ready") return state.page;
  if (state.kind === "loading") return state.previous;
  if ("page" in state) return state.page;
  return undefined;
}

function retainedDetail(state: DynamicAppsViewState): DynamicAppDetail | undefined {
  return "detail" in state ? state.detail : undefined;
}

export function reduceDynamicAppsState(state: DynamicAppsViewState, action: DynamicAppsViewAction): DynamicAppsViewState {
  switch (action.type) {
    case "load": return { kind: "loading", query: action.query, previous: retainedPage(state) };
    case "loaded": return { kind: "ready", query: state.query, page: action.page };
    case "load-detail": return { kind: "detail-loading", query: state.query, appId: action.appId, page: retainedPage(state) };
    case "detail-loaded": return { kind: "detail-ready", query: state.query, page: retainedPage(state), detail: action.detail };
    case "mutate": return { kind: "mutating", query: state.query, appId: action.appId, page: retainedPage(state), detail: retainedDetail(state) };
    case "mutation-complete": return { kind: "detail-ready", query: state.query, page: retainedPage(state), detail: action.detail };
    case "failed": return { kind: "error", query: state.query, message: action.message, page: retainedPage(state), detail: retainedDetail(state) };
  }
}
