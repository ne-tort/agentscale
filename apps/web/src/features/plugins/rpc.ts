/**
 * `plugin-rpc/1`: единственный канал общения shell с iframe плагина.
 *
 * Архитектура v4, §5.2/§5.3/§10/§11: плагин рендерится на ОТДЕЛЬНОМ
 * static-origin в песочнице `allow-scripts allow-forms` БЕЗ
 * `allow-same-origin` (иначе classic sandbox bypass). Поэтому:
 *
 * - каждое сообщение проверяется и по `event.origin`, и по `event.source`;
 * - `event.origin` при любом sandbox без `allow-same-origin` равно `"null"`
 *   (opaque origin, см. §5.3) — это ожидаемое значение, но оно не снимает
 *   требование точного совпадения с `expectedOrigin`;
 * - capability token НЕ попадает во frontend и НИКОГДА не передаётся в URL,
 *   в сообщениях или в тексте ошибок: в этом модуле его просто нет;
 * - fail-closed: нет рукопожатия без таймаута, чужой origin/ignore, неверный
 *   протокол, неизвестный `requestId` и повторное использование — отказы.
 *
 * Сообщение с чужим origin ИГНОРИРУЕТСЯ, а не убивает канал: иначе любая
 * вкладка могла бы «выключить» плагин одним `postMessage` (DoS).
 */

import { PluginFrameError } from "./errors";

export const PLUGIN_RPC_PROTOCOL = "plugin-rpc/1" as const;

/** Закрытый набор сообщений протокола; «просто объект» не проходит. */
export type PluginReadyMessage = {
  readonly protocol: typeof PLUGIN_RPC_PROTOCOL;
  readonly type: "plugin.ready";
  readonly plugin_id: string;
  readonly version: string;
  readonly digest?: string;
};
export type HostReadyMessage = {
  readonly protocol: typeof PLUGIN_RPC_PROTOCOL;
  readonly kind: "host.ready";
  readonly instanceId: string;
  readonly pluginId: string;
  readonly version: string;
};
export type PluginRpcRequestMessage = {
  readonly protocol: typeof PLUGIN_RPC_PROTOCOL;
  readonly kind: "request";
  readonly requestId: string;
  readonly method: string;
  readonly params?: unknown;
};
export type PluginRpcResponseMessage = {
  readonly protocol: typeof PLUGIN_RPC_PROTOCOL;
  readonly kind: "response";
  readonly requestId: string;
  readonly ok: boolean;
  readonly result?: unknown;
  readonly error?: { readonly code: string; readonly message?: string };
};
export type PluginErrorMessage = {
  readonly protocol: typeof PLUGIN_RPC_PROTOCOL;
  readonly kind: "error";
  readonly code: string;
};

export type PluginChannelMessage =
  | PluginReadyMessage
  | HostReadyMessage
  | PluginRpcRequestMessage
  | PluginRpcResponseMessage
  | PluginErrorMessage;

export type PluginChannelOptions = {
  /** Ожидаемые `plugin_id` / `version` для сверки с `plugin.ready`. */
  readonly pluginId?: string;
  readonly version?: string;
  readonly timeoutMs?: number;
  readonly requestTimeoutMs?: number;
  /** Куда слушать `message`. По умолчанию — `iframe.ownerDocument.defaultView`. */
  readonly hostWindow?: Window | null;
  /** Генератор `requestId`; в тестах подменяется детерминированным. */
  readonly makeRequestId?: () => string;
  readonly onReady?: (message: PluginReadyMessage) => void;
  readonly onError?: (error: PluginFrameError) => void;
  /** Диагностика отклонённых сообщений; решение от неё не зависит. */
  readonly onRejected?: (reason: "origin" | "source" | "shape") => void;
};

export interface PluginChannel {
  readonly expectedOrigin: string;
  readonly ready: boolean;
  readonly disposed: boolean;
  /** Ждёт `plugin.ready`; отказ при таймауте, неверном протоколе или identity. */
  handshake(): Promise<PluginReadyMessage>;
  request<TResult = unknown>(method: string, params?: unknown): Promise<TResult>;
  /** Идемпотентно: повторный вызов безопасен. */
  dispose(): void;
}

/** Opaque origin песочницы без `allow-same-origin` (v4 §5.3, path-based v1). */
export const OPAQUE_ORIGIN = "null";

function isRecord(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

/** Плагин не должен подсунуть объект с proxy, прототипом или лишними полями. */
function exactShape(value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []): boolean {
  const allowed = new Set([...required, ...optional]);
  return Object.getOwnPropertySymbols(value).length === 0
    && Object.getOwnPropertyNames(value).every((key) => {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      return allowed.has(key)
        && !["__proto__", "prototype", "constructor"].includes(key)
        && Boolean(descriptor && "value" in descriptor && descriptor.enumerable);
    })
    && required.every((key) => Object.hasOwn(value, key));
}

function safeJson(value: unknown, depth = 0, budget = { nodes: 0 }): boolean {
  budget.nodes += 1;
  if (budget.nodes > 2_000 || depth > 12) return false;
  if (value === null || typeof value === "boolean") return true;
  if (typeof value === "string") return value.length <= 16_384;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.length <= 2_000 && value.every((item) => safeJson(item, depth + 1, budget));
  return isRecord(value) && Object.keys(value).length <= 2_000
    && Object.entries(value).every(([key, item]) => !["__proto__", "prototype", "constructor"].includes(key) && safeJson(item, depth + 1, budget));
}

function messageBytes(value: unknown): number {
  try { return new TextEncoder().encode(JSON.stringify(value)).byteLength; } catch { return Number.MAX_SAFE_INTEGER; }
}

const MAX_MESSAGE_BYTES = 64 * 1024;
const METHOD_ID = /^[a-z][a-z0-9._-]{0,127}$/;
const DEFAULT_TIMEOUT_MS = 10_000;
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;

function boundedTimeout(value: number | undefined, fallback: number): number {
  return Math.max(100, Math.min(value ?? fallback, 120_000));
}

let requestCounter = 0;
function defaultRequestId(): string {
  requestCounter += 1;
  const bytes = new Uint8Array(12);
  globalThis.crypto?.getRandomValues?.(bytes);
  const suffix = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `rpc-${requestCounter}-${suffix}`;
}

/**
 * Канал, привязанный к конкретному `iframe.contentWindow`.
 * `expectedOrigin` — ЕДИНСТВЕННОЕ допустимое значение `event.origin`.
 * Wildcard запрещён: он превращает проверку в профанацию.
 */
export function createPluginChannel(
  iframe: HTMLIFrameElement,
  expectedOrigin: string,
  options: PluginChannelOptions = {},
): PluginChannel {
  const hostWindow = options.hostWindow === undefined ? iframe.ownerDocument.defaultView : options.hostWindow;
  if (!hostWindow) throw new PluginFrameError("loadError", "plugin_host_window_unavailable");
  if (typeof expectedOrigin !== "string" || expectedOrigin.length === 0 || expectedOrigin === "*") {
    throw new PluginFrameError("originMismatch", "plugin_expected_origin_invalid");
  }

  const handshakeTimeoutMs = boundedTimeout(options.timeoutMs, DEFAULT_TIMEOUT_MS);
  const requestTimeoutMs = boundedTimeout(options.requestTimeoutMs, DEFAULT_REQUEST_TIMEOUT_MS);
  const makeRequestId = options.makeRequestId ?? defaultRequestId;
  const expectedPluginId = options.pluginId ?? iframe.dataset.pluginId ?? "";
  const expectedVersion = options.version ?? iframe.dataset.pluginVersion ?? "";
  // Fail-closed: без известного identity handshake не начинаем.
  if (!expectedPluginId || !expectedVersion) {
    throw new PluginFrameError("loadError", "plugin_identity_unavailable");
  }

  let disposed = false;
  let ready = false;
  let handshakeResolve: ((message: PluginReadyMessage) => void) | undefined;
  let handshakeReject: ((error: PluginFrameError) => void) | undefined;
  let handshakePromise: Promise<PluginReadyMessage> | undefined;
  let handshakeTimer: ReturnType<typeof setTimeout> | undefined;

  type Pending = {
    resolve: (value: unknown) => void;
    reject: (error: PluginFrameError) => void;
    timer: ReturnType<typeof setTimeout>;
  };
  const pending = new Map<string, Pending>();
  const seenResponses = new Set<string>();

  /** Общее закрытие для `dispose()` и для любого фатального отказа. */
  const close = (error: PluginFrameError): void => {
    if (disposed) return;
    disposed = true;
    hostWindow!.removeEventListener("message", onMessage);
    if (handshakeTimer !== undefined) { clearTimeout(handshakeTimer); handshakeTimer = undefined; }
    const rejectHandshake = handshakeReject;
    handshakeResolve = undefined;
    handshakeReject = undefined;
    for (const entry of pending.values()) {
      clearTimeout(entry.timer);
      entry.reject(error);
    }
    pending.clear();
    seenResponses.clear();
    rejectHandshake?.(error);
  };

  const fail = (error: PluginFrameError): void => {
    if (disposed) return;
    close(error);
    try { options.onError?.(error); } catch { /* Диагностика не влияет на изоляцию. */ }
  };

  const send = (message: HostReadyMessage | PluginRpcRequestMessage): void => {
    if (disposed) return;
    // Оpaque-фрейм адресуется только "*" (v4 §5.3); сообщение не несёт токена.
    const targetOrigin = expectedOrigin === OPAQUE_ORIGIN ? "*" : expectedOrigin;
    try { iframe.contentWindow?.postMessage(message, targetOrigin); }
    catch { /* Фрейм, не принявший сообщение, не завершит рукопожатие. */ }
  };

  function onMessage(event: MessageEvent): void {
    if (disposed) return;
    // 1) Источник окна: ровно этот iframe, а не любой фрейм страницы.
    if (event.source !== iframe.contentWindow) {
      try { options.onRejected?.("source"); } catch { /* ignore */ }
      return;
    }
    // 2) Origin: точное совпадение. Чужой origin ИГНОРИРУЕТСЯ, не роняет канал.
    if (event.origin !== expectedOrigin) {
      try { options.onRejected?.("origin"); } catch { /* ignore */ }
      return;
    }
    if (!isRecord(event.data) || messageBytes(event.data) > MAX_MESSAGE_BYTES) {
      try { options.onRejected?.("shape"); } catch { /* ignore */ }
      return;
    }
    const data = event.data;
    // 3) Протокол классифицируется до любых действий с содержимым.
    if (data.protocol !== PLUGIN_RPC_PROTOCOL) {
      fail(new PluginFrameError("unsupportedProtocol", "plugin_rpc_protocol_mismatch"));
      return;
    }
    if (!ready) {
      if (!exactShape(data, ["protocol", "type", "plugin_id", "version"], ["digest"]) || data.type !== "plugin.ready") {
        try { options.onRejected?.("shape"); } catch { /* ignore */ }
        return;
      }
      if (typeof data.plugin_id !== "string" || typeof data.version !== "string"
        || (data.digest !== undefined && typeof data.digest !== "string")) {
        try { options.onRejected?.("shape"); } catch { /* ignore */ }
        return;
      }
      if (data.plugin_id !== expectedPluginId || data.version !== expectedVersion) {
        fail(new PluginFrameError("identityMismatch", "plugin_ready_identity_mismatch"));
        return;
      }
      const readyMessage: PluginReadyMessage = {
        protocol: PLUGIN_RPC_PROTOCOL,
        type: "plugin.ready",
        plugin_id: data.plugin_id,
        version: data.version,
        ...(typeof data.digest === "string" ? { digest: data.digest } : {}),
      };
      ready = true;
      if (handshakeTimer !== undefined) { clearTimeout(handshakeTimer); handshakeTimer = undefined; }
      const resolve = handshakeResolve;
      handshakeResolve = undefined;
      handshakeReject = undefined;
      try { options.onReady?.(readyMessage); } catch { /* Наблюдатель не влияет на изоляцию. */ }
      resolve?.(readyMessage);
      return;
    }
    if (data.kind === "error" && exactShape(data, ["protocol", "kind", "code"], []) && typeof data.code === "string") {
      // Текст и `message` плагина намеренно не используются: только whitelist-код.
      fail(new PluginFrameError("loadError", "plugin_declared_error"));
      return;
    }
    if (data.kind !== "response" || !exactShape(data, ["protocol", "kind", "requestId", "ok"], ["result", "error"])) {
      try { options.onRejected?.("shape"); } catch { /* ignore */ }
      return;
    }
    if (typeof data.requestId !== "string" || typeof data.ok !== "boolean") {
      try { options.onRejected?.("shape"); } catch { /* ignore */ }
      return;
    }
    const requestId = data.requestId;
    if (seenResponses.has(requestId)) return; // Повторный ответ не подменяет результат.
    const entry = pending.get(requestId);
    if (!entry) return; // Ответ на неизвестный/снятый запрос игнорируется.
    pending.delete(requestId);
    seenResponses.add(requestId);
    clearTimeout(entry.timer);
    if (data.ok) {
      if (!safeJson(data.result ?? null) || messageBytes(data.result ?? null) > MAX_MESSAGE_BYTES) {
        entry.reject(new PluginFrameError("loadError", "plugin_rpc_invalid_response"));
        return;
      }
      entry.resolve(data.result);
      return;
    }
    const rawCode = isRecord(data.error) && typeof data.error.code === "string" ? data.error.code : "PLUGIN_RPC_FAILED";
    entry.reject(new PluginFrameError("loadError", rawCode));
  }

  hostWindow.addEventListener("message", onMessage);

  /** Идемпотентное закрытие. Повторный вызов ничего не делает. */
  function dispose(): void {
    close(new PluginFrameError("disposed", "plugin_channel_disposed"));
  }

  return {
    expectedOrigin,
    get ready() { return ready && !disposed; },
    get disposed() { return disposed; },
    handshake(): Promise<PluginReadyMessage> {
      if (disposed) return Promise.reject(new PluginFrameError("disposed", "plugin_channel_disposed"));
      if (ready) {
        return Promise.resolve({
          protocol: PLUGIN_RPC_PROTOCOL,
          type: "plugin.ready",
          plugin_id: expectedPluginId,
          version: expectedVersion,
        });
      }
      if (handshakePromise) return handshakePromise;
      handshakePromise = new Promise<PluginReadyMessage>((resolve, reject) => {
        handshakeResolve = resolve;
        handshakeReject = reject;
        handshakeTimer = setTimeout(() => {
          fail(new PluginFrameError("timeout", "plugin_handshake_timeout"));
        }, handshakeTimeoutMs);
      });
      // `host.ready` инициирует плагин; capability token здесь отсутствует.
      send({
        protocol: PLUGIN_RPC_PROTOCOL,
        kind: "host.ready",
        instanceId: "shell",
        pluginId: expectedPluginId,
        version: expectedVersion,
      });
      return handshakePromise;
    },
    request<TResult = unknown>(method: string, params?: unknown): Promise<TResult> {
      if (disposed) return Promise.reject(new PluginFrameError("disposed", "plugin_channel_disposed"));
      if (!ready) return Promise.reject(new PluginFrameError("notReady", "plugin_channel_not_ready"));
      if (typeof method !== "string" || !METHOD_ID.test(method)) {
        return Promise.reject(new PluginFrameError("loadError", "plugin_rpc_method_invalid"));
      }
      if (params !== undefined && (!safeJson(params) || messageBytes(params) > MAX_MESSAGE_BYTES)) {
        return Promise.reject(new PluginFrameError("loadError", "plugin_rpc_params_invalid"));
      }
      const requestId = makeRequestId();
      if (pending.has(requestId) || seenResponses.has(requestId)) {
        return Promise.reject(new PluginFrameError("loadError", "plugin_rpc_request_id_reused"));
      }
      return new Promise<TResult>((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(requestId);
          reject(new PluginFrameError("timeout", "plugin_rpc_request_timeout"));
        }, requestTimeoutMs);
        pending.set(requestId, { resolve: resolve as (value: unknown) => void, reject, timer });
        send({ protocol: PLUGIN_RPC_PROTOCOL, kind: "request", requestId, method, ...(params !== undefined ? { params } : {}) });
      });
    },
    dispose,
  };
}
