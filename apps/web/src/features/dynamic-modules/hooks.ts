import { useCallback, useEffect, useRef, useState } from "react";
import { toDynamicModuleError, type DynamicModuleErrorCode, type DynamicModuleServiceError } from "./service";

export type ResourceState<T> =
  | { readonly status: "loading"; readonly data?: T }
  | { readonly status: "ready"; readonly data: T }
  | { readonly status: "error"; readonly error: DynamicModuleServiceError; readonly data?: T };

export interface ResourceResult<T> {
  readonly state: ResourceState<T>;
  readonly reload: () => void;
}

/**
 * Minimal cancellable async resource.
 *
 * `key` is a primitive snapshot of the request scope (definition id, cabinet, revision…).
 * Changing it aborts the in-flight request and refetches, and the previous scope's data is
 * never presented under the new key — a stale response cannot leak between definitions.
 */
export function useResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  key: string,
  options: { readonly enabled?: boolean } = {},
): ResourceResult<T> {
  const enabled = options.enabled ?? true;
  const loadRef = useRef(load);
  loadRef.current = load;
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ readonly key: string; readonly attempt: number; readonly value: ResourceState<T> }>(
    { key, attempt: 0, value: { status: "loading" } },
  );

  useEffect(() => {
    if (!enabled) return undefined;
    let active = true;
    const controller = new AbortController();
    setState((current) => ({
      key,
      attempt,
      value: { status: "loading", ...(current.value.status === "ready" && current.key === key ? { data: current.value.data } : {}) },
    }));
    void loadRef.current(controller.signal).then(
      (data) => {
        if (!active || controller.signal.aborted) return;
        setState({ key, attempt, value: { status: "ready", data } });
      },
      (error: unknown) => {
        if (!active || controller.signal.aborted) return;
        const mapped = toDynamicModuleError(error);
        if (mapped.code === "DYNAMIC_MODULES_REQUEST_FAILED" && /cancell?ed/i.test(mapped.message)) return;
        setState({ key, attempt, value: { status: "error", error: mapped } });
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [attempt, enabled, key]);

  const reload = useCallback(() => setAttempt((value) => value + 1), []);
  const current = state.key === key && state.attempt === attempt
    ? state.value
    : ({ status: "loading" } as ResourceState<T>);
  const visible: ResourceState<T> = enabled ? current : { status: "loading" };
  return { state: visible, reload };
}

/** True for the two codes that mean "the shell has no capability context/wiring". */
export function isBlockedCode(code: DynamicModuleErrorCode): boolean {
  return code === "CAPABILITY_DENIED" || code === "DYNAMIC_MODULES_API_UNAVAILABLE";
}

export interface SubmitState {
  readonly busy: boolean;
  readonly error?: DynamicModuleServiceError;
  readonly notice?: string;
}

export interface SubmitController extends SubmitState {
  run(action: () => Promise<void>): Promise<boolean>;
  reset(): void;
  succeed(message: string): void;
}

/**
 * Shared submit lifecycle for the mutation forms: one in-flight command per form,
 * typed error/notice state, and no silent retries.
 */
export function useSubmitController(): SubmitController {
  const [state, setState] = useState<SubmitState>({ busy: false });
  const generation = useRef(0);

  const run = useCallback(async (action: () => Promise<void>): Promise<boolean> => {
    const current = ++generation.current;
    setState({ busy: true });
    try {
      await action();
      if (generation.current === current) setState((previous) => ({ busy: false, ...(previous.notice ? { notice: previous.notice } : {}) }));
      return true;
    } catch (error) {
      const mapped = toDynamicModuleError(error);
      if (generation.current === current) setState({ busy: false, error: mapped });
      return false;
    }
  }, []);

  const reset = useCallback(() => {
    generation.current += 1;
    setState({ busy: false });
  }, []);

  const succeed = useCallback((message: string) => {
    setState((previous) => ({
      busy: previous.busy,
      notice: message,
      ...(previous.error ? { error: previous.error } : {}),
    }));
  }, []);

  return { ...state, run, reset, succeed };
}
