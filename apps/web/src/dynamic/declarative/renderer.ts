import type { UiNode } from "../../../../../packages/schemas/declarative-app/v1";
import { validateDeclarativeManifest } from "../schema";

export type DeclarativeAction = (input: { readonly nodeId: string; readonly value?: string }) => void;

export interface TableState {
  readonly state: "loading" | "error" | "empty" | "ready";
  readonly rows?: readonly Readonly<Record<string, string | number | boolean | null>>[];
  readonly columns?: readonly string[];
}

const CONTAINERS = new Set(["stack", "row", "column"]);
const LEAVES = new Set(["text", "button", "input", "table", "slot"]);

function textValue(value: unknown): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function renderNode(document: Document, node: UiNode, onAction: DeclarativeAction, tableStates: Readonly<Record<string, TableState>>): HTMLElement {
  if (!CONTAINERS.has(node.type) && !LEAVES.has(node.type)) throw new TypeError("unsupported_ui_node");
  if (node.type === "stack" || node.type === "row" || node.type === "column") {
    const element = document.createElement("div");
    element.dataset.layout = node.type;
    for (const child of node.children) element.append(renderNode(document, child, onAction, tableStates));
    return element;
  }
  if (node.type === "slot") {
    const element = document.createElement("div");
    element.dataset.slot = node.name;
    return element;
  }
  const props = node.props ?? {};
  if (node.type === "text") {
    const element = document.createElement("span");
    element.textContent = textValue(props.text);
    return element;
  }
  if (node.type === "button") {
    const element = document.createElement("button");
    element.type = "button";
    element.textContent = textValue(props.label);
    element.disabled = true;
    element.setAttribute("aria-disabled", "true");
    return element;
  }
  if (node.type === "input") {
    const element = document.createElement("input");
    element.type = "text";
    element.value = textValue(props.value);
    element.setAttribute("aria-label", textValue(props.label) || node.id);
    return element;
  }
  const element = document.createElement("table");
  const caption = document.createElement("caption");
  caption.textContent = textValue(props.label);
  element.append(caption);
  const state = tableStates[node.id] ?? { state: "empty" as const };
  const body = document.createElement("tbody");
  if (state.state !== "ready") {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.textContent = state.state === "loading" ? "Loading" : state.state === "error" ? "Unavailable" : "No data";
    row.append(cell);
    body.append(row);
  } else {
    const columns = state.columns ?? [];
    const header = document.createElement("thead");
    const headerRow = document.createElement("tr");
    for (const column of columns) {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = column;
      headerRow.append(cell);
    }
    header.append(headerRow);
    element.append(header);
    for (const values of state.rows ?? []) {
      const row = document.createElement("tr");
      for (const column of columns) {
        const cell = document.createElement("td");
        cell.textContent = textValue(values[column]);
        row.append(cell);
      }
      body.append(row);
    }
  }
  element.append(body);
  return element;
}

export function renderDeclarativeApp(container: HTMLElement, manifestInput: unknown, tableStates: Readonly<Record<string, TableState>> = {}): () => void {
  const checked = validateDeclarativeManifest(manifestInput);
  if (!checked.ok || !checked.manifest.entrypoints.ui) throw new TypeError("invalid_declarative_ui");
  const root = renderNode(container.ownerDocument, checked.manifest.entrypoints.ui.root, () => undefined, tableStates);
  container.replaceChildren(root);
  return () => container.replaceChildren();
}
