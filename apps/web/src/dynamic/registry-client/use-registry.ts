import { useCallback, useEffect, useRef, useState } from "react";
import type { RequestContextInput } from "../protocol";
import type { RegistryRouteClientRequest, RegistryClientRequest, RegistryClientResolution, DynamicRegistryController } from "./controller";
import type { SignedRegistrySnapshot } from "../registry";
import { routeToSnapshotPointer } from "./route-adapter";

export type DynamicRegistryStatus =
  | "idle"
  | "loading"
  | "resolved"
  | "unknown_app"
  | "unknown_route"
  | "ambiguous_route"
  | "invalid"
  | "denied"
  | "stale"
  | "quarantined";

export interface DynamicRegistryHookState {
  readonly status: DynamicRegistryStatus;
  readonly resolution?: RegistryClientResolution;
  readonly refresh(): void;
}

export interface DynamicRegistryQuery {
  readonly appId: string;
  readonly version: string;
  readonly routeId: string;
  readonly context: RequestContextInput;
  readonly effectiveCapabilities: ReadonlySet<string>;
}

export interface DynamicRegistryRouteQuery {
  readonly pathname: string;
  readonly snapshotHint: SignedRegistrySnapshot | unknown;
  readonly context: RequestContextInput;
  readonly effectiveCapabilities: ReadonlySet<string>;
}

function contextKey(context: RequestContextInput): readonly unknown[] {
  return [context.tenantId, context.cabinetId ?? null, context.projectId ?? null, context.sessionId ?? null, context.traceId, context.requestId ?? null];
}

function hintStamp(hint: unknown): readonly [string | null, string | null] {
  try {
    if (typeof hint !== "object" || hint === null) return [null, null];
    const digest = Object.getOwnPropertyDescriptor(hint, "digest");
    const issuedAt = Object.getOwnPropertyDescriptor(hint, "issuedAt");
    return [
      digest && "value" in digest && typeof digest.value === "string" ? digest.value : null,
      issuedAt && "value" in issuedAt && typeof issuedAt.value === "string" ? issuedAt.value : null,
    ];
  } catch {
    return [null, null];
  }
}

function queryKey(query: DynamicRegistryQuery | DynamicRegistryRouteQuery): string {
  const capabilities = [...query.effectiveCapabilities].sort();
  if ("appId" in query) {
    return JSON.stringify(["app", query.appId, query.version, query.routeId, contextKey(query.context), capabilities]);
  }
  const pointer = routeToSnapshotPointer(query.snapshotHint, query.pathname);
  return JSON.stringify(["route", query.pathname, pointer, hintStamp(query.snapshotHint), contextKey(query.context), capabilities]);
}

function statusOf(result: RegistryClientResolution): DynamicRegistryStatus {
  return result.state;
}

function useRegistryResult(
  client: DynamicRegistryController,
  key: string | undefined,
  execute: ((signal: AbortSignal) => Promise<RegistryClientResolution>) | undefined,
): DynamicRegistryHookState {
  const [revision, setRevision] = useState(client.revision);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<Omit<DynamicRegistryHookState, "refresh"> & { readonly queryKey?: string; readonly revision?: number; readonly attempt?: number; readonly client?: DynamicRegistryController }>({ status: "idle" });
  const sequence = useRef(0);
  const executeRef = useRef(execute);
  executeRef.current = execute;

  useEffect(() => {
    setRevision(client.revision);
    return client.subscribe((nextRevision) => setRevision(nextRevision));
  }, [client]);

  useEffect(() => {
    const current = ++sequence.current;
    if (!key || !executeRef.current) {
      setState({ status: "idle" });
      return;
    }
    const abort = new AbortController();
    setState({ status: "loading", queryKey: key, revision, attempt, client });
    void executeRef.current(abort.signal).then((resolution) => {
      if (sequence.current !== current || abort.signal.aborted) return;
      setState({ status: statusOf(resolution), resolution, queryKey: key, revision, attempt, client });
    }).catch(() => {
      if (sequence.current !== current || abort.signal.aborted) return;
      setState({ status: "quarantined", queryKey: key, revision, attempt, client });
    });
    return () => {
      abort.abort();
      sequence.current += 1;
    };
  }, [client, key, revision, attempt]);

  const refresh = useCallback(() => setAttempt((value) => value + 1), []);
  const visibleState = state.queryKey === key && state.revision === revision && state.attempt === attempt && state.client === client
    ? state
    : { status: key ? "loading" as const : "idle" as const };
  return { ...visibleState, refresh };
}

export function useDynamicRegistry(client: DynamicRegistryController, query?: DynamicRegistryQuery): DynamicRegistryHookState {
  const key = query ? queryKey(query) : undefined;
  const execute = query ? (signal: AbortSignal) => client.resolve({ ...query, signal } satisfies RegistryClientRequest) : undefined;
  return useRegistryResult(client, key, execute);
}

export function useDynamicRegistryRoute(client: DynamicRegistryController, query?: DynamicRegistryRouteQuery): DynamicRegistryHookState {
  const key = query ? queryKey(query) : undefined;
  const execute = query ? (signal: AbortSignal) => client.resolveRoute({ ...query, signal } satisfies RegistryRouteClientRequest) : undefined;
  return useRegistryResult(client, key, execute);
}