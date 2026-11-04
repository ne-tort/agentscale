import { createElement, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, ReactNode } from "react";
import type { CapabilityName, JsonValue, UiNode } from "../../../../../packages/schemas/declarative-app/v1";
import { constraintsAllow } from "../../capabilities/boundary";
import type { CapabilityBoundary, CapabilityGateway } from "../../capabilities/types";
import type { BrokerPolicy } from "../broker";
import { mountIsolatedPlugin } from "../plugin-host";
import type { IsolatedPluginHandle } from "../plugin-host";
import type { RequestContextInput } from "../protocol";
import { validateDeclarativeManifest } from "../schema";
import type { RegistryClientResolution, DynamicRegistryController } from "../registry-client/controller";
import { useDynamicRegistryRoute } from "../registry-client/use-registry";

export interface DeclarativeTableBinding {
  readonly capability: CapabilityName;
  readonly method: string;
  readonly columns: readonly { readonly id: string; readonly label: string }[];
  readonly buildParams: (context: RequestContextInput) => JsonValue;
}
export interface DeclarativeActionBinding {
  readonly capability: CapabilityName;
  readonly method: string;
  readonly inputNodeIds?: readonly string[];
  readonly buildParams: (input: Readonly<Record<string, string>>, context: RequestContextInput) => JsonValue;
}
export interface DeclarativeBindings {
  readonly tables: Readonly<Record<string, DeclarativeTableBinding>>;
  readonly actions: Readonly<Record<string, DeclarativeActionBinding>>;
}
export interface DynamicReactHostProps {
  readonly resolution: RegistryClientResolution;
  readonly gateway: CapabilityGateway;
  readonly boundary?: CapabilityBoundary;
  readonly context: RequestContextInput;
  readonly bindings: DeclarativeBindings;
  readonly enabled?: boolean;
  readonly pageSize?: number;
  readonly labels?: Partial<DynamicHostLabels>;
  readonly onRecover?: () => void;
}
export interface DynamicRouteHostProps extends Omit<DynamicReactHostProps, "resolution"> {
  readonly registry: DynamicRegistryController;
  readonly pathname: string;
  readonly snapshotHint: unknown;
}
export interface DynamicCodePluginHostProps {
  readonly resolution: RegistryClientResolution;
  readonly allowedOrigins: ReadonlySet<string>;
  readonly broker: BrokerPolicy;
  readonly capabilityVersions: ReadonlyMap<string, string>;
  readonly enabled?: boolean;
  readonly onDiagnostic?: (code: string) => void;
}
export interface DynamicHostLabels {
  readonly search: string; readonly clear: string; readonly refresh: string; readonly retry: string;
  readonly previous: string; readonly next: string; readonly cancel: string; readonly disabled: string;
  readonly noData: string; readonly noMatches: string; readonly missingBinding: string;
  readonly actionFailed: string; readonly queryFailed: string; readonly completed: string;
}
const DEFAULT_LABELS: DynamicHostLabels = {
  search: "Search", clear: "Clear search", refresh: "Refresh", retry: "Retry", previous: "Previous", next: "Next",
  cancel: "Cancel", disabled: "Dynamic app disabled by host", noData: "No data", noMatches: "No matching rows",
  missingBinding: "No approved host operation is registered for this control.", actionFailed: "Action failed",
  queryFailed: "Could not load this table", completed: "Action completed.",
};
const MAX_PARAMS_BYTES = 64 * 1024;
const MAX_ROWS = 500;
const ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

function own<T>(record: Readonly<Record<string, T>>, key: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(record, key) ? record[key] : undefined;
}
function record(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}
function isPlainRow(value: unknown): value is Record<string, string | number | boolean | null> {
  return record(value) && Object.values(value).every((item) => item === null || typeof item === "string" || typeof item === "boolean" || (typeof item === "number" && Number.isFinite(item)));
}
function tableRows(value: unknown): value is readonly Record<string, string | number | boolean | null>[] {
  if (!Array.isArray(value) || value.length > MAX_ROWS || !value.every(isPlainRow)) return false;
  const ids = value.flatMap((row) => typeof row.id === "string" || typeof row.id === "number" ? [String(row.id)] : []);
  return new Set(ids).size === ids.length;
}
function boundedJson(value: unknown): JsonValue {
  const serialized = JSON.stringify(value);
  if (serialized === undefined || new TextEncoder().encode(serialized).byteLength > MAX_PARAMS_BYTES) throw new TypeError("rpc_payload_too_large_or_invalid");
  return JSON.parse(serialized) as JsonValue;
}
function findNode(root: UiNode, id: string): UiNode | undefined {
  if ("id" in root && root.id === id) return root;
  return "children" in root ? root.children.map((child) => findNode(child, id)).find((node) => node !== undefined) : undefined;
}
function collectInputs(node: UiNode, result = new Map<string, Extract<UiNode, { type: "input" }>>()): Map<string, Extract<UiNode, { type: "input" }>> {
  if (node.type === "input") result.set(node.id, node);
  if ("children" in node) node.children.forEach((child) => collectInputs(child, result));
  return result;
}
function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return "-";
}
function hasGrant(boundary: CapabilityBoundary | undefined, requested: readonly { readonly name: string; readonly constraints?: { readonly methods?: readonly string[] } }[], capability: CapabilityName, method: string): boolean {
  const declaration = requested.find((item) => item.name === capability);
  const effective = boundary?.effective.get(capability);
  if (!boundary || !declaration || !effective) return false;
  const now = Date.now();
  if ((effective.expiresAt !== undefined && Date.parse(effective.expiresAt) <= now) || (effective.revokedAt !== undefined && Date.parse(effective.revokedAt) <= now)) return false;
  if (declaration.constraints?.methods && !declaration.constraints.methods.includes(method)) return false;
  try { return constraintsAllow(boundary, capability, "methods", method); } catch { return false; }
}
function stateMessage(code: string): string {
  return new Set(["CAPABILITY_DENIED", "BROKER_KILLED", "REQUEST_CANCELLED_OR_TIMED_OUT", "REQUEST_LIMIT", "INVALID_INPUT", "INVALID_RESPONSE"])
    .has(code) ? code : "request_failed";
}

function QueryTable({ node, binding, gateway, context, pageSize, enabled, allowed, labels }: {
  node: Extract<UiNode, { type: "table" }>;
  binding?: DeclarativeTableBinding;
  gateway: CapabilityGateway;
  context: RequestContextInput;
  pageSize: number;
  enabled: boolean;
  allowed: boolean;
  labels: DynamicHostLabels;
}) {
  const [rows, setRows] = useState<readonly Record<string, string | number | boolean | null>[]>([]);
  const [queryError, setQueryError] = useState<string>();
  const [loading, setLoading] = useState(Boolean(binding));
  const [revision, setRevision] = useState(0);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  useEffect(() => {
    if (!enabled || !allowed || !binding) {
      setRows([]); setLoading(false); setQueryError(undefined);
      return;
    }
    const controller = new AbortController();
    let current = true;
    setRows([]); setLoading(true); setQueryError(undefined);
    let params: JsonValue;
    try { params = boundedJson(binding.buildParams(context)); }
    catch (error) { setLoading(false); setQueryError(stateMessage(error instanceof Error ? error.message : "invalid_params")); return () => controller.abort(); }
    void gateway.invoke<JsonValue, JsonValue>(binding.method, params, { capability: binding.capability, signal: controller.signal })
      .then((result) => {
        if (!tableRows(result) || new TextEncoder().encode(JSON.stringify(result)).byteLength > MAX_PARAMS_BYTES) throw new TypeError("invalid_table_response");
        if (current && !controller.signal.aborted) setRows(result);
      })
      .catch((error: unknown) => { if (current && !controller.signal.aborted) setQueryError(stateMessage(error instanceof Error ? error.message : "query_failed")); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [allowed, binding, context, enabled, gateway, revision]);
  const filtered = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return needle ? rows.filter((row) => binding?.columns.some((column) => display(row[column.id]).toLocaleLowerCase().includes(needle))) : rows;
  }, [binding, rows, search]);
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, pages - 1);
  const shown = filtered.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  const props = node.props;
  return createElement("section", { "aria-labelledby": `${node.id}-caption` },
    createElement("h2", { id: `${node.id}-caption` }, display(props.label) === "-" ? node.id : display(props.label)),
    !enabled ? createElement("p", { role: "status" }, labels.disabled)
      : !binding || !allowed ? createElement("p", { role: "status" }, labels.missingBinding) : null,
    queryError ? createElement("div", { role: "alert" }, `${labels.queryFailed}: ${queryError}`, createElement("button", { type: "button", onClick: () => setRevision((value) => value + 1), disabled: !enabled }, labels.retry)) : null,
    binding && enabled && allowed ? createElement("div", null,
      createElement("label", { htmlFor: `${node.id}-search` }, labels.search),
      createElement("input", { id: `${node.id}-search`, type: "search", value: search, onChange: (event: ChangeEvent<HTMLInputElement>) => { setSearch(event.currentTarget.value); setPage(0); }, "aria-label": `${labels.search} ${display(props.label)}` }),
      search ? createElement("button", { type: "button", onClick: () => { setSearch(""); setPage(0); }, "aria-label": labels.clear }, labels.clear) : null,
      loading ? createElement("p", { role: "status" }, "Loading") : null,
      createElement("table", null,
        createElement("caption", null, display(props.label)),
        createElement("thead", null, createElement("tr", null, ...binding.columns.map((column) => createElement("th", { key: column.id, scope: "col" }, column.label)))),
        createElement("tbody", null, ...shown.map((row, index) => createElement("tr", { key: String(row.id ?? currentPage * pageSize + index) }, ...binding.columns.map((column) => createElement("td", { key: column.id }, display(row[column.id])))))),
      ),
      !loading && shown.length === 0 ? createElement("p", { role: "status" }, search ? labels.noMatches : labels.noData) : null,
      createElement("div", { role: "group", "aria-label": `${filtered.length === 0 ? "0" : currentPage * pageSize + 1}-${Math.min((currentPage + 1) * pageSize, filtered.length)} of ${filtered.length}` },
        createElement("button", { type: "button", disabled: currentPage === 0, onClick: () => setPage((value) => Math.max(0, value - 1)) }, labels.previous),
        createElement("button", { type: "button", disabled: currentPage >= pages - 1, onClick: () => setPage((value) => Math.min(pages - 1, value + 1)) }, labels.next),
        createElement("button", { type: "button", disabled: !enabled || loading, onClick: () => setRevision((value) => value + 1) }, labels.refresh),
      ),
    ) : null,
  );
}

function ActionButton({ node, binding, values, inputs, gateway, context, enabled, allowed, labels }: {
  node: Extract<UiNode, { type: "button" }>;
  binding?: DeclarativeActionBinding;
  values: Readonly<Record<string, string>>;
  inputs: ReadonlyMap<string, Extract<UiNode, { type: "input" }>>;
  gateway: CapabilityGateway;
  context: RequestContextInput;
  enabled: boolean;
  allowed: boolean;
  labels: DynamicHostLabels;
}) {
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string>();
  const controller = useRef<AbortController | null>(null);
  useLayoutEffect(() => {
    if (!enabled || !allowed) controller.current?.abort();
  }, [allowed, enabled]);
  useEffect(() => () => controller.current?.abort(), []);
  const inputIds = binding?.inputNodeIds ?? [];
  const inputReady = inputIds.every((id) => inputs.has(id) && (!(inputs.get(id)?.props.required === true) || Boolean(values[id]?.trim())));
  const canRun = Boolean(binding && allowed && enabled && inputReady && !pending);
  const invoke = async (): Promise<void> => {
    if (!binding || !canRun) return;
    const abort = new AbortController(); controller.current = abort; setPending(true); setMessage(undefined);
    try {
      const params = boundedJson(binding.buildParams(Object.fromEntries(inputIds.map((id) => [id, values[id] ?? ""])), context));
      await gateway.invoke<JsonValue, JsonValue>(binding.method, params, { capability: binding.capability, signal: abort.signal });
      if (!abort.signal.aborted) setMessage(labels.completed);
    } catch (error) {
      if (!abort.signal.aborted) setMessage(`${labels.actionFailed}: ${stateMessage(error instanceof Error ? error.message : "operation_failed")}`);
    } finally { if (controller.current === abort) controller.current = null; setPending(false); }
  };
  return createElement("span", null,
    // The manifest node id is the DOM address of the control (the input below keeps it too); dropping
    // it left the rendered action button unaddressable and unable to be labelled by its node.
    createElement("button", { id: node.id, type: "button", disabled: !canRun, title: !binding || !allowed ? labels.missingBinding : undefined, onClick: () => { void invoke(); } }, display(node.props.label) === "-" ? node.id : display(node.props.label)),
    pending ? createElement("button", { type: "button", onClick: () => controller.current?.abort(), "aria-label": labels.cancel }, labels.cancel) : null,
    !inputReady && binding ? createElement("span", { role: "alert" }, "Complete required inputs before continuing.") : null,
    message ? createElement("span", { role: "status" }, message) : null,
  );
}

function AppNode({ node, props, values, setValues }: {
  node: UiNode;
  props: DynamicReactHostProps;
  values: Readonly<Record<string, string>>;
  setValues: (next: (current: Readonly<Record<string, string>>) => Readonly<Record<string, string>>) => void;
}): ReactNode {
  const labels = { ...DEFAULT_LABELS, ...props.labels };
  if (node.type === "stack" || node.type === "row" || node.type === "column") return createElement("div", { "data-layout": node.type, className: `dynamic-layout dynamic-layout--${node.type}` }, ...node.children.map((child) => createElement(AppNode, { key: "id" in child ? child.id : undefined, node: child, props, values, setValues })));
  if (node.type === "text") return createElement("p", null, display(node.props.text) === "-" ? "" : display(node.props.text));
  if (node.type === "slot") return createElement("div", { "data-slot": node.name, "aria-label": node.name });
  if (node.type === "input") return createElement("label", null,
    display(node.props.label) === "-" ? node.id : display(node.props.label),
    createElement("input", { id: node.id, type: node.props.type === "number" ? "number" : "text", value: values[node.id] ?? (display(node.props.value) === "-" ? "" : String(node.props.value)), required: node.props.required === true, disabled: props.enabled === false, onChange: (event: ChangeEvent<HTMLInputElement>) => setValues((current) => ({ ...current, [node.id]: event.currentTarget.value })) }),
  );
  if (node.type === "button") {
    const binding = own(props.bindings.actions, node.id);
    const manifest = props.resolution.state === "resolved" ? props.resolution.manifest : undefined;
    const allowed = Boolean(manifest && binding && hasGrant(props.boundary, manifest.requestedCapabilities, binding.capability, binding.method));
    const screenRoot = manifest && props.resolution.state === "resolved" ? findNode(manifest.entrypoints.ui!.root, props.resolution.route.root) : undefined;
    const inputs = screenRoot ? collectInputs(screenRoot) : new Map();
    return createElement(ActionButton, { node, binding, values, inputs, gateway: props.gateway, context: props.context, enabled: props.enabled !== false, allowed, labels });
  }
  if (node.type === "table") {
    const binding = own(props.bindings.tables, node.id);
    const manifest = props.resolution.state === "resolved" ? props.resolution.manifest : undefined;
    const parametersReady = binding !== undefined && ID.test(binding.method) && binding.columns.length > 0 && binding.columns.length <= 64
      && new Set(binding.columns.map((column) => column.id)).size === binding.columns.length
      && binding.columns.every((column) => ID.test(column.id) && typeof column.label === "string" && column.label.trim().length > 0 && column.label.length <= 160);
    const allowed = Boolean(manifest && binding && parametersReady && hasGrant(props.boundary, manifest.requestedCapabilities, binding.capability, binding.method));
    return createElement(QueryTable, { node, binding, gateway: props.gateway, context: props.context, pageSize: props.pageSize ?? 20, enabled: props.enabled !== false, allowed, labels });
  }
  return null;
}

export function DynamicReactHost(props: DynamicReactHostProps) {
  const resolution = props.resolution;
  if (resolution.state !== "resolved") return createElement("section", { role: resolution.state === "denied" || resolution.state === "quarantined" ? "alert" : "status", "data-dynamic-state": resolution.state },
    createElement("h1", null, `Dynamic app ${resolution.state.replaceAll("_", " ")}`),
    createElement("p", null, `Diagnostic: ${resolution.code}. Reference: ${resolution.traceId}.`),
    props.onRecover ? createElement("button", { type: "button", onClick: props.onRecover }, "Recover") : null,
  );
  const checked = validateDeclarativeManifest(resolution.manifest);
  if (!checked.ok || !checked.manifest.entrypoints.ui) return createElement("section", { role: "alert" }, "Dynamic app UI is invalid. No component was rendered.");
  if (!props.boundary || props.boundary.appId !== checked.manifest.appId || props.boundary.manifestVersion !== checked.manifest.version) return createElement("section", { role: "alert" }, "Capability context does not match this app version.");
  const rootNode = findNode(checked.manifest.entrypoints.ui.root, resolution.route.root);
  if (!rootNode) return createElement("section", { role: "alert" }, "The verified route references a missing UI root.");
  const pageSize = Number.isInteger(props.pageSize) ? Math.max(1, Math.min(props.pageSize!, 100)) : 20;
  return createElement(ResolvedDynamicScreen, { key: `${resolution.digest}:${resolution.route.id}`, props, resolution, rootNode, pageSize });
}

function ResolvedDynamicScreen({ props, resolution, rootNode, pageSize }: {
  readonly props: DynamicReactHostProps;
  readonly resolution: Extract<RegistryClientResolution, { readonly state: "resolved" }>;
  readonly rootNode: UiNode;
  readonly pageSize: number;
}) {
  const [values, setValues] = useState<Readonly<Record<string, string>>>({});
  return createElement("main", { "data-dynamic-app": resolution.manifest.appId, "data-dynamic-route": resolution.route.id },
    createElement("header", null, createElement("h1", null, resolution.route.title), !props.enabled ? createElement("p", { role: "alert" }, props.labels?.disabled ?? DEFAULT_LABELS.disabled, props.onRecover ? createElement("button", { type: "button", onClick: props.onRecover }, "Recover") : null) : null),
    createElement(AppNode, { node: rootNode, props: { ...props, resolution }, values, setValues }),
    createElement("button", { type: "button", disabled: !props.enabled, onClick: () => setValues({}) }, "Reset screen"),
  );
}

/** Resolves through the existing signed registry client; shared router remains caller-owned. */
export function DynamicRouteHost(props: DynamicRouteHostProps) {
  const query = useMemo(() => ({
    pathname: props.pathname,
    snapshotHint: props.snapshotHint,
    context: props.context,
    effectiveCapabilities: new Set(props.boundary?.effective.keys() ?? []),
  }), [props.boundary, props.context, props.pathname, props.snapshotHint]);
  const result = useDynamicRegistryRoute(props.registry, query);
  if (result.status === "idle" || result.status === "loading" || result.resolution === undefined) {
    return createElement("section", { role: "status", "data-dynamic-state": result.status }, "Resolving signed app route…");
  }
  return createElement(DynamicReactHost, { ...props, resolution: result.resolution });
}

/** Code is hosted only at a registry-approved immutable origin and can call only the supplied broker policy. */
export function DynamicCodePluginHost(props: DynamicCodePluginHostProps) {
  const root = useRef<HTMLDivElement>(null);
  const handle = useRef<IsolatedPluginHandle | null>(null);
  const [diagnostic, setDiagnostic] = useState<string>();
  const [ready, setReady] = useState(false);
  const resolved = props.resolution.state === "resolved" ? props.resolution : undefined;
  const manifest = resolved?.manifest;
  const plugin = manifest?.entrypoints.plugin;
  const artifact = plugin?.artifact;
  const declaredMethods = plugin?.rpc.methods;
  const isAllowed = Boolean(resolved && manifest && artifact && Array.isArray(declaredMethods)
    && declaredMethods.length > 0
    && declaredMethods.every((method) => typeof method.name === "string" && props.broker.methods.has(method.name)
      && props.broker.methods.get(method.name) === method.requiredCapabilities?.[0]
      && (method.requiredCapabilities ?? []).length === 1
      && (method.requiredCapabilities ?? []).every((capability) => props.broker.allowedCapabilities.has(capability)))
    && resolved.digest === `sha256:${artifact.sha256.toLowerCase()}`);

  useLayoutEffect(() => {
    const container = root.current;
    if (!container) return;
    handle.current?.destroy();
    handle.current = null;
    setDiagnostic(undefined);
    setReady(false);
    if (!props.enabled || !resolved || !manifest || !plugin || !artifact || !isAllowed) return;
    try {
      handle.current = mountIsolatedPlugin({
        document: container.ownerDocument,
        container,
        src: artifact.uri,
        allowedOrigins: props.allowedOrigins,
        identity: { pluginId: manifest.appId, version: manifest.version, digest: resolved.digest },
        broker: {
          ...props.broker,
          methods: new Map(declaredMethods!.flatMap((method) => {
            const capability = props.broker.methods.get(method.name);
            return capability ? [[method.name, capability] as const] : [];
          })),
          allowedCapabilities: new Set(declaredMethods!.flatMap((method) => method.requiredCapabilities ?? [])),
        },
        capabilityVersions: props.capabilityVersions,
        onReady: () => setReady(true),
        onFailure: (code) => { setDiagnostic(code); props.onDiagnostic?.(code); },
      });
    } catch {
      setDiagnostic("plugin-host-refused");
      props.onDiagnostic?.("plugin-host-refused");
    }
    return () => {
      handle.current?.destroy();
      handle.current = null;
    };
  }, [artifact, isAllowed, manifest, plugin, props.allowedOrigins, props.broker, props.capabilityVersions, props.enabled, props.onDiagnostic, resolved]);

  return createElement("section", { "data-plugin-state": !props.enabled ? "disabled" : ready ? "ready" : isAllowed ? "starting" : "unavailable" },
    createElement("div", { ref: root }),
    !props.enabled ? createElement("p", { role: "status" }, "Code plugin disabled. The frame and broker were stopped.")
      : !isAllowed ? createElement("p", { role: "alert" }, "Plugin artifact is unavailable, mismatched, or requests capabilities not present in host policy.")
        : null,
    diagnostic ? createElement("p", { role: "alert" }, `Plugin diagnostic: ${diagnostic}`) : null,
    props.enabled && isAllowed ? createElement("button", { type: "button", onClick: () => handle.current?.kill() }, "Stop plugin") : null,
  );
}
