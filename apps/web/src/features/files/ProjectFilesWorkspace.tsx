import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  classifyWorkspaceError,
  sortWorkspaceEntries,
  workspaceErrorMessage,
  type FilesAvailability,
} from "./state";
import {
  joinWorkspacePath,
  normalizeWorkspacePath,
  parentWorkspacePath,
  WorkspaceServiceError,
  type WorkspaceEntry,
  type WorkspaceListing,
  type WorkspaceService,
  type WorkspaceTextPreview,
} from "./contracts";
import "./styles.css";

interface DirectorySnapshot {
  status: "idle" | "loading" | "ready" | "error";
  entries: WorkspaceEntry[];
  error?: unknown;
}

type Notice = { kind: "success" | "error" | "info"; message: string };

type PreviewState =
  | { kind: "idle" }
  | { kind: "loading"; path: string }
  | { kind: "ready"; path: string; preview: WorkspaceTextPreview }
  | { kind: "error"; path: string; error: unknown };

type Dialog =
  | {
      kind: "confirm";
      title: string;
      message: string;
      confirmLabel: string;
      destructive?: boolean;
      onConfirm: () => Promise<void> | void;
    }
  | { kind: "create-file"; name: string; content: string; error?: string }
  | { kind: "create-folder"; name: string; error?: string }
  | {
      kind: "destination";
      operation: "move" | "copy";
      entry: WorkspaceEntry;
      destination: string;
      error?: string;
    };

export interface ProjectFilesWorkspaceProps {
  projectId: string;
  service: WorkspaceService;
  initialPath?: string;
  title?: string;
  className?: string;
}

const EMPTY_DIRECTORY: DirectorySnapshot = { status: "idle", entries: [] };

function workspaceUrl(projectId: string, path: string, selectedPath?: string): string {
  const query = new URLSearchParams();
  if (path) query.set("path", path);
  if (selectedPath) query.set("selected", selectedPath);
  const search = query.toString();
  return `/projects/${encodeURIComponent(projectId)}/files${search ? `?${search}` : ""}`;
}

function workspaceDirectories(path: string): string[] {
  const parts = normalizeWorkspacePath(path).split("/").filter(Boolean);
  return ["", ...parts.map((_, index) => parts.slice(0, index + 1).join("/"))];
}

function safeInitialPath(path: string | undefined): string {
  try {
    return normalizeWorkspacePath(path ?? "");
  } catch {
    return "";
  }
}

function getInitialOffline(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

function sizeLabel(size: number | null): string {
  if (size === null || !Number.isFinite(size)) return "—";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10240 ? 1 : 0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function modifiedLabel(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function availabilityLabel(kind: FilesAvailability): string {
  switch (kind) {
    case "loading":
      return "Loading";
    case "empty":
      return "Empty";
    case "offline":
      return "Offline";
    case "paused":
      return "Paused";
    case "pod-unavailable":
      return "Pod unavailable";
    case "error":
      return "Unavailable";
    default:
      return "Live Pod";
  }
}

function availabilityTitle(kind: FilesAvailability): string {
  switch (kind) {
    case "offline":
      return "Workspace is offline";
    case "paused":
      return "Project is paused";
    case "pod-unavailable":
      return "Pod is not running";
    case "error":
      return "Workspace is unavailable";
    default:
      return "Files unavailable";
  }
}

function availabilityDetail(kind: FilesAvailability): string {
  switch (kind) {
    case "offline":
      return "The live Pod workspace cannot be reached while the browser is offline. Reconnect to try again.";
    case "paused":
      return "Resume this project before opening its live Pod workspace.";
    case "pod-unavailable":
      return "This project Pod is not running or ready. Start the Pod to access files.";
    case "error":
      return "The live Pod workspace returned an error. Try again when the Pod is available.";
    default:
      return "Select a file to view its contents.";
  }
}

function errorNotice(error: unknown): Notice {
  return { kind: "error", message: workspaceErrorMessage(error) };
}

function TreeNode({
  path,
  label,
  depth,
  directories,
  expanded,
  currentPath,
  onToggle,
  onNavigate,
}: {
  path: string;
  label: string;
  depth: number;
  directories: Record<string, DirectorySnapshot>;
  expanded: ReadonlySet<string>;
  currentPath: string;
  onToggle: (path: string) => void;
  onNavigate: (path: string) => void;
}) {
  const snapshot = directories[path];
  const children = snapshot?.entries.filter((entry) => entry.kind === "dir") ?? [];
  const isExpanded = expanded.has(path);
  const hasKnownChildren = snapshot?.status === "ready" && children.length > 0;
  const isCurrent = currentPath === path;

  return (
    <div className="files-tree-node" role="treeitem" aria-expanded={isExpanded} aria-selected={isCurrent}>
      <div className={`files-tree-row${isCurrent ? " is-current" : ""}`} style={{ paddingInlineStart: `${depth * 14 + 4}px` }}>
        <button
          type="button"
          className="files-tree-toggle"
          aria-label={`${isExpanded ? "Collapse" : "Expand"} ${label}`}
          onClick={() => onToggle(path)}
          disabled={snapshot?.status === "loading"}
        >
          {isExpanded ? "▾" : "▸"}
        </button>
        <button type="button" className="files-tree-label" onClick={() => onNavigate(path)}>
          <span aria-hidden="true">{isExpanded ? "▾" : "▸"}</span>
          <span>{label}</span>
          {snapshot?.status === "loading" && <span className="files-inline-spinner" aria-label="Loading" />}
        </button>
      </div>
      {isExpanded && (
        <div className="files-tree-children" role="group">
          {snapshot?.status === "error" && (
            <p className="files-tree-hint" style={{ paddingInlineStart: `${(depth + 1) * 14 + 28}px` }}>
              Unable to load this folder
            </p>
          )}
          {hasKnownChildren &&
            children.map((entry) => (
              <TreeNode
                key={entry.path}
                path={entry.path}
                label={entry.name}
                depth={depth + 1}
                directories={directories}
                expanded={expanded}
                currentPath={currentPath}
                onToggle={onToggle}
                onNavigate={onNavigate}
              />
            ))}
          {snapshot?.status === "ready" && children.length === 0 && (
            <p className="files-tree-hint" style={{ paddingInlineStart: `${(depth + 1) * 14 + 28}px` }}>
              Empty
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function ModalShell({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : undefined;
    const dialog = dialogRef.current;
    const first = dialog?.querySelector<HTMLElement>("[autofocus], button, input, textarea, [tabindex]:not([tabindex='-1'])");
    first?.focus();
    return () => previousFocus?.focus();
  }, []);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])") ?? []);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }

  return (
    <div className="files-modal-backdrop" role="presentation">
      <div ref={dialogRef} className="files-modal" role="dialog" aria-modal="true" aria-labelledby="files-dialog-title" onKeyDown={handleKeyDown}>
        <div className="files-modal-header">
          <h2 id="files-dialog-title">{title}</h2>
          <button type="button" className="files-icon-button" aria-label="Close dialog" onClick={onClose}>
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ProjectFilesWorkspace({
  projectId,
  service,
  initialPath,
  title = "Files",
  className = "",
}: ProjectFilesWorkspaceProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const routeState = useMemo(() => {
    const params = new URLSearchParams(location.search);
    const queryPath = params.get("path");
    const selected = params.get("selected");
    const prefix = `/projects/${encodeURIComponent(projectId)}/files/`;
    const suffix = location.pathname.startsWith(prefix) ? location.pathname.slice(prefix.length) : "";
    let decodedSuffix = "";
    try {
      decodedSuffix = decodeURIComponent(suffix);
    } catch {
      decodedSuffix = suffix;
    }
    return {
      path: queryPath ?? (decodedSuffix ? parentWorkspacePath(safeInitialPath(decodedSuffix)) : undefined),
      selected: selected ?? (decodedSuffix || undefined),
    };
  }, [location.pathname, location.search, projectId]);
  const initial = useMemo(() => safeInitialPath(routeState.path ?? initialPath), [initialPath, routeState.path]);
  const routeSelection = useMemo(() => safeInitialPath(routeState.selected ?? ""), [routeState.selected]);
  const [currentPath, setCurrentPath] = useState(initial);
  const [pathInput, setPathInput] = useState(initial);
  const [directories, setDirectories] = useState<Record<string, DirectorySnapshot>>({});
  const directoriesRef = useRef(directories);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(["", initial]));
  const [selectedPath, setSelectedPath] = useState<string | undefined>(routeSelection || undefined);
  const [preview, setPreview] = useState<PreviewState>({ kind: "idle" });
  const [previewNonce, setPreviewNonce] = useState(0);
  const [editorValue, setEditorValue] = useState("");
  const [dirty, setDirty] = useState(false);
  const [networkOffline, setNetworkOffline] = useState(getInitialOffline);
  const [operationBusy, setOperationBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>();
  const [dialog, setDialog] = useState<Dialog>();
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const previewRequestRef = useRef(0);
  const previewControllerRef = useRef<AbortController | undefined>(undefined);
  const downloadControllerRef = useRef<AbortController | undefined>(undefined);
  const preflightControllerRef = useRef<AbortController | undefined>(undefined);
  const projectIdRef = useRef(projectId);
  const routeKey = `${location.pathname}${location.search}`;
  const routeKeyRef = useRef(routeKey);
  const selectedPathRef = useRef(selectedPath);
  const allowRouteChangeRef = useRef(false);
  const directoryRequestIdsRef = useRef(0);
  const directoryRequestsRef = useRef(new Map<string, { id: number; controller: AbortController }>());

  useEffect(() => {
    directoriesRef.current = directories;
  }, [directories]);

  useLayoutEffect(() => {
    selectedPathRef.current = selectedPath;
  }, [selectedPath]);

  useLayoutEffect(() => {
    const projectChanged = projectIdRef.current !== projectId;
    const routeChanged = routeKeyRef.current !== routeKey;
    if (!projectChanged && !routeChanged) return;
    if ((projectChanged || routeChanged) && dirty && !allowRouteChangeRef.current) {
      setDialog({
        kind: "confirm",
        title: "Discard unsaved changes?",
        message: "Your text changes will be lost if you leave this file.",
        confirmLabel: "Discard changes",
        destructive: true,
        onConfirm: () => {
          allowRouteChangeRef.current = true;
          setDirty(false);
          navigate(routeKey);
        },
      });
      navigate(routeKeyRef.current, { replace: true });
      return;
    }
    allowRouteChangeRef.current = false;
    const retainedDirectoryKeys = new Set(workspaceDirectories(initial).map((path) => `${projectId}\u0000${path}`));
    for (const [key, request] of directoryRequestsRef.current) {
      if (projectChanged || !retainedDirectoryKeys.has(key)) {
        request.controller.abort();
        directoryRequestsRef.current.delete(key);
      }
    }
    if (!projectChanged && currentPath === initial && selectedPath === (routeSelection || undefined)) {
      routeKeyRef.current = routeKey;
      return;
    }
    routeKeyRef.current = routeKey;
    if (projectChanged) {
      projectIdRef.current = projectId;
      for (const request of directoryRequestsRef.current.values()) request.controller.abort();
      directoryRequestsRef.current.clear();
      directoriesRef.current = {};
      setDirectories({});
      setExpanded(new Set(["", initial]));
      setOperationBusy(false);
      setDialog(undefined);
      setNotice(undefined);
    } else {
      for (const [key, request] of directoryRequestsRef.current) {
        if (key === `${projectId}\u0000` || key === `${projectId}\u0000${initial}`) continue;
        request.controller.abort();
        directoryRequestsRef.current.delete(key);
      }
      setDirectories((previous) => {
        const next = { ...previous };
        for (const [path, snapshot] of Object.entries(next)) {
          if (snapshot.status === "loading" && path !== initial && path !== "") {
            next[path] = { status: snapshot.entries.length ? "ready" : "idle", entries: snapshot.entries };
          }
        }
        return next;
      });
    }
    previewControllerRef.current?.abort();
    downloadControllerRef.current?.abort();
    preflightControllerRef.current?.abort();
    previewRequestRef.current += 1;
    setCurrentPath(initial);
    setPathInput(initial);
    setExpanded((previous) => new Set([...previous, ...workspaceDirectories(initial)]));
    setSelectedPath(routeSelection || undefined);
    setPreview({ kind: "idle" });
    setEditorValue("");
    setDirty(false);
  }, [currentPath, dirty, initial, navigate, projectId, routeKey, routeSelection, selectedPath]);

  useEffect(() => () => {
    for (const request of directoryRequestsRef.current.values()) request.controller.abort();
    directoryRequestsRef.current.clear();
    previewControllerRef.current?.abort();
    downloadControllerRef.current?.abort();
    preflightControllerRef.current?.abort();
  }, []);

  const setDirectoryError = useCallback((path: string, error: unknown) => {
    setDirectories((previous) => ({
      ...previous,
      [path]: { status: "error", entries: previous[path]?.entries ?? [], error },
    }));
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedPath(undefined);
    setPreview({ kind: "idle" });
    setEditorValue("");
    setDirty(false);
    navigate(workspaceUrl(projectId, currentPath));
  }, [currentPath, navigate, projectId]);

  const selectPath = useCallback((path?: string) => {
    selectedPathRef.current = path;
    setSelectedPath(path);
    setPreview({ kind: "idle" });
    setEditorValue("");
    setDirty(false);
    navigate(workspaceUrl(projectId, currentPath, path));
  }, [currentPath, navigate, projectId]);

  const markUnavailable = useCallback(
    (error: unknown) => {
      setDirectoryError(currentPath, error);
      setNotice(errorNotice(error));
    },
    [currentPath, setDirectoryError],
  );

  const loadDirectory = useCallback(
    async (path: string, force = false, requireSuccess = false): Promise<WorkspaceListing | undefined> => {
      const normalizedPath = normalizeWorkspacePath(path);
      if (projectIdRef.current !== projectId) {
        if (requireSuccess) throw new Error("Workspace project changed before readback completed");
        return undefined;
      }
      const requestKey = `${projectId}\u0000${normalizedPath}`;
      if (networkOffline) {
        const offline = new WorkspaceServiceError({
          code: "OFFLINE",
          title: "Workspace is offline",
          status: 0,
          detail: "The workspace service could not be reached.",
        });
        setDirectoryError(normalizedPath, offline);
        if (requireSuccess) throw offline;
        return undefined;
      }
      const existing = directoriesRef.current[normalizedPath];
      const activeRequest = directoryRequestsRef.current.get(requestKey);
      if (!force && existing?.status === "ready") return { path: normalizedPath, entries: existing.entries };
      if (!force && activeRequest) return undefined;
      activeRequest?.controller.abort();
      const controller = new AbortController();
      const requestId = ++directoryRequestIdsRef.current;
      directoryRequestsRef.current.set(requestKey, { id: requestId, controller });
      setDirectories((previous) => ({
        ...previous,
        [normalizedPath]: { status: "loading", entries: previous[normalizedPath]?.entries ?? [] },
      }));
      try {
        const listing = await service.listEntries(projectId, normalizedPath, { signal: controller.signal });
        if (projectIdRef.current !== projectId || directoryRequestsRef.current.get(requestKey)?.id !== requestId) return undefined;
        setDirectories((previous) => ({
          ...previous,
          [normalizedPath]: { status: "ready", entries: listing.entries },
        }));
        return listing;
      } catch (error) {
        if (controller.signal.aborted || projectIdRef.current !== projectId || directoryRequestsRef.current.get(requestKey)?.id !== requestId) return undefined;
        if (classifyWorkspaceError(error) === "offline") setNetworkOffline(true);
        setDirectoryError(normalizedPath, error);
        if (requireSuccess) throw error;
        return undefined;
      } finally {
        if (directoryRequestsRef.current.get(requestKey)?.id === requestId) directoryRequestsRef.current.delete(requestKey);
      }
    },
    [networkOffline, projectId, service, setDirectoryError],
  );

  useEffect(() => {
    if (!networkOffline) {
      for (const path of workspaceDirectories(currentPath)) void loadDirectory(path);
    }
  }, [currentPath, loadDirectory, networkOffline]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const handleOffline = () => setNetworkOffline(true);
    const handleOnline = () => setNetworkOffline(false);
    window.addEventListener("offline", handleOffline);
    window.addEventListener("online", handleOnline);
    return () => {
      window.removeEventListener("offline", handleOffline);
      window.removeEventListener("online", handleOnline);
    };
  }, []);

  useEffect(() => {
    if (!selectedPath || networkOffline) {
      if (selectedPath && networkOffline) {
        setPreview({
          kind: "error",
          path: selectedPath,
          error: new WorkspaceServiceError({ code: "OFFLINE", title: "Workspace is offline", status: 0 }),
        });
      }
      return;
    }
    const requestId = ++previewRequestRef.current;
    const controller = new AbortController();
    previewControllerRef.current?.abort();
    previewControllerRef.current = controller;
    setPreview({ kind: "loading", path: selectedPath });
    void service
      .previewText(projectId, selectedPath, { signal: controller.signal })
      .then((result) => {
        if (requestId !== previewRequestRef.current || projectIdRef.current !== projectId) return;
        setPreview({ kind: "ready", path: selectedPath, preview: result });
        setEditorValue(result.content);
        setDirty(false);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || requestId !== previewRequestRef.current || projectIdRef.current !== projectId) return;
        setPreview({ kind: "error", path: selectedPath, error });
        const kind = classifyWorkspaceError(error);
        if (kind === "offline" || kind === "paused" || kind === "pod-unavailable") markUnavailable(error);
      });
    return () => {
      controller.abort();
      previewRequestRef.current += 1;
    };
  }, [markUnavailable, networkOffline, previewNonce, projectId, selectedPath, service]);

  const snapshot = directories[currentPath] ?? EMPTY_DIRECTORY;
  const entries = useMemo(() => sortWorkspaceEntries(snapshot.entries), [snapshot.entries]);
  const selectedEntry = entries.find((entry) => entry.path === selectedPath);
  const currentErrorKind = snapshot.status === "error" ? classifyWorkspaceError(snapshot.error) : undefined;
  const availability: FilesAvailability = networkOffline && snapshot.status !== "ready"
    ? "offline"
    : snapshot.status === "idle" || snapshot.status === "loading"
      ? "loading"
      : snapshot.status === "error"
        ? currentErrorKind === "paused"
          ? "paused"
          : currentErrorKind === "pod-unavailable"
            ? "pod-unavailable"
            : currentErrorKind === "offline"
              ? "offline"
              : "error"
        : snapshot.entries.length === 0
          ? "empty"
          : "ready";
  const unavailable = availability === "offline" || availability === "paused" || availability === "pod-unavailable" || availability === "error";
  const writeDisabled = operationBusy || snapshot.status !== "ready" || networkOffline || unavailable;

  const applyPath = useCallback(
    (path: string) => {
      setCurrentPath(path);
      setPathInput(path);
      setExpanded((previous) => new Set([...previous, ...workspaceDirectories(path)]));
      setSelectedPath(undefined);
      setPreview({ kind: "idle" });
      setEditorValue("");
      setDirty(false);
      setNotice(undefined);
      navigate(workspaceUrl(projectId, path));
    },
    [navigate, projectId],
  );

  const navigateTo = useCallback(
    (path: string) => {
      const normalizedPath = normalizeWorkspacePath(path);
      if (dirty) {
        setDialog({
          kind: "confirm",
          title: "Discard unsaved changes?",
          message: "Your text changes will be lost if you leave this file.",
          confirmLabel: "Discard changes",
          destructive: true,
          onConfirm: () => {
            setDirty(false);
            applyPath(normalizedPath);
          },
        });
        return;
      }
      applyPath(normalizedPath);
    },
    [applyPath, dirty],
  );

  const handleToggleDirectory = useCallback(
    (path: string) => {
      setExpanded((previous) => {
        const next = new Set(previous);
        if (next.has(path)) next.delete(path);
        else next.add(path);
        return next;
      });
      if (!directoriesRef.current[path] || directoriesRef.current[path].status !== "ready") void loadDirectory(path);
    },
    [loadDirectory],
  );

  const handleEntryOpen = useCallback(
    (entry: WorkspaceEntry) => {
      if (entry.kind === "dir") {
        navigateTo(entry.path);
        return;
      }
      if (selectedPath === entry.path) return;
      if (dirty && selectedPath !== entry.path) {
        setDialog({
          kind: "confirm",
          title: "Discard unsaved changes?",
          message: "Your text changes will be lost if you open another file.",
          confirmLabel: "Discard changes",
          destructive: true,
          onConfirm: () => {
            setDirty(false);
            selectPath(entry.path);
          },
        });
        return;
      }
      selectPath(entry.path);
    },
    [dirty, navigateTo, selectPath, selectedPath],
  );

  const handleOperationError = useCallback(
    (error: unknown) => {
      const kind = classifyWorkspaceError(error);
      if (kind === "offline") setNetworkOffline(true);
      if (kind === "offline" || kind === "paused" || kind === "pod-unavailable") markUnavailable(error);
      else setNotice(errorNotice(error));
    },
    [markUnavailable],
  );

  const refreshDirectories = useCallback(
    async (paths: string[]) => {
      const uniquePaths = [...new Set(paths.map(normalizeWorkspacePath))];
      const readbacks = await Promise.all(uniquePaths.map((path) => loadDirectory(path, true, true)));
      if (readbacks.some((readback) => readback === undefined)) throw new Error("Workspace readback could not be confirmed");
      return readbacks as WorkspaceListing[];
    },
    [loadDirectory],
  );

  const runMutation = useCallback(
    async (successMessage: string, mutation: () => Promise<void>, pathsToRefresh: string[] = [currentPath], onSuccess?: () => void, verify?: (readbacks: WorkspaceListing[]) => boolean) => {
      const operationProjectId = projectId;
      const operationRouteKey = routeKeyRef.current;
      setOperationBusy(true);
      setNotice(undefined);
      try {
        await mutation();
        if (projectIdRef.current !== operationProjectId || routeKeyRef.current !== operationRouteKey) return;
        const readbacks = await refreshDirectories(pathsToRefresh);
        if (verify && !verify(readbacks)) throw new Error("Workspace mutation was not confirmed by the server readback. Refresh and retry before assuming it succeeded.");
        if (projectIdRef.current !== operationProjectId) return;
        onSuccess?.();
        setNotice({ kind: "success", message: successMessage });
      } catch (error) {
        if (projectIdRef.current === operationProjectId) handleOperationError(error);
      } finally {
        if (projectIdRef.current === operationProjectId) setOperationBusy(false);
      }
    },
    [currentPath, handleOperationError, projectId, refreshDirectories],
  );

  const handleDownload = useCallback(
    async (entry: WorkspaceEntry) => {
      const requestProjectId = projectId;
      const requestRouteKey = routeKeyRef.current;
      const controller = new AbortController();
      downloadControllerRef.current?.abort();
      downloadControllerRef.current = controller;
      setOperationBusy(true);
      setNotice(undefined);
      try {
        const download = await service.download(projectId, entry.path, { signal: controller.signal });
        if (projectIdRef.current !== requestProjectId || routeKeyRef.current !== requestRouteKey) return;
        if (typeof URL.createObjectURL !== "function") throw new Error("Downloads are not supported by this browser");
        const url = URL.createObjectURL(download.blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = download.filename;
        anchor.rel = "noopener";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 0);
        setNotice({ kind: "success", message: `Downloaded ${download.filename}` });
      } catch (error) {
        if (!controller.signal.aborted && projectIdRef.current === requestProjectId && routeKeyRef.current === requestRouteKey) handleOperationError(error);
      } finally {
        if (projectIdRef.current === requestProjectId) setOperationBusy(false);
      }
    },
    [handleOperationError, projectId, service],
  );

  const handleSave = useCallback(() => {
    if (preview.kind !== "ready" || preview.preview.truncated || !dirty) return;
    const savePath = preview.path;
    const saveName = preview.preview.name;
    const content = editorValue;
    let readback: WorkspaceTextPreview | undefined;
    void runMutation(
      `Saved ${saveName}`,
      async () => {
        await service.upload(projectId, savePath, content);
        readback = await service.previewText(projectId, savePath);
        if (readback.truncated || readback.content !== content) throw new Error("Saved file contents did not match the server readback. Your editor changes are still available.");
      },
      [currentPath],
      () => {
        if (!readback || selectedPathRef.current !== savePath) return;
        setPreview({ kind: "ready", path: savePath, preview: readback });
        setEditorValue(readback.content);
        setDirty(false);
      },
    );
  }, [currentPath, dirty, editorValue, preview, projectId, runMutation, selectedPath, service]);

  const handlePathSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try {
      navigateTo(pathInput);
    } catch (error) {
      setNotice({ kind: "error", message: error instanceof Error ? error.message : "Invalid workspace path" });
    }
  };

  const handleCreateFile = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (dialog?.kind !== "create-file") return;
    const fileDialog = dialog;
    const name = fileDialog.name.trim();
    let target: string;
    try {
      target = joinWorkspacePath(currentPath, name);
    } catch (error) {
      setDialog({ ...dialog, error: error instanceof Error ? error.message : "Enter a valid file name" });
      return;
    }
    let mutationStarted = false;
    const create = (replace: boolean) => {
      setDialog(undefined);
      mutationStarted = true;
      void runMutation(
        `${replace ? "Saved" : "Created"} ${target.split("/").pop() ?? target}`,
        async () => {
          await service.upload(projectId, target, fileDialog.content);
          const readback = await service.previewText(projectId, target);
          if (readback.truncated || readback.content !== fileDialog.content) throw new Error("Created file contents did not match the server readback.");
        },
        [currentPath],
        undefined,
        (readbacks) => readbacks.some((readback) => readback.entries.some((entry) => entry.path === target && entry.kind === "file")),
      );
    };
    const requestProjectId = projectId;
    const requestRouteKey = routeKeyRef.current;
    const controller = new AbortController();
    setOperationBusy(true);
    void service.listEntries(projectId, currentPath, { signal: controller.signal }).then((listing) => {
      if (controller.signal.aborted || projectIdRef.current !== requestProjectId || routeKeyRef.current !== requestRouteKey || dialog !== fileDialog) return;
      const existing = listing.entries.find((entry) => entry.path === target);
      if (existing?.kind === "dir") {
        setDialog({ ...fileDialog, error: `${target} is a folder. Choose a file name.` });
      } else if (existing) {
        setDialog({
          kind: "confirm",
          title: "Replace existing file?",
          message: `${target} already exists. Replacing it will overwrite its contents.`,
          confirmLabel: "Replace file",
          destructive: true,
          onConfirm: () => create(true),
        });
      } else create(false);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted && projectIdRef.current === requestProjectId && routeKeyRef.current === requestRouteKey) {
        setDialog({ ...fileDialog, error: `Could not check for an existing file: ${workspaceErrorMessage(error)}` });
        handleOperationError(error);
      }
    }).finally(() => {
      if (!mutationStarted && projectIdRef.current === requestProjectId) setOperationBusy(false);
    });
  };

  const handleCreateFolder = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (dialog?.kind !== "create-folder") return;
    const folderDialog = dialog;
    const folderName = dialog.name.trim();
    if (!folderName || folderName === "." || folderName === ".." || /[\\/]/.test(folderName)) {
      setDialog({ ...dialog, error: "Enter one folder name without path separators." });
      return;
    }
    let target: string;
    try {
      target = joinWorkspacePath(currentPath, folderName);
    } catch (error) {
      setDialog({ ...dialog, error: error instanceof Error ? error.message : "Enter a valid folder name" });
      return;
    }
    const requestProjectId = projectId;
    const requestRouteKey = routeKeyRef.current;
    const controller = new AbortController();
    preflightControllerRef.current?.abort();
    preflightControllerRef.current = controller;
    let mutationStarted = false;
    setOperationBusy(true);
    void service.listEntries(projectId, currentPath, { signal: controller.signal }).then((listing) => {
      if (controller.signal.aborted || projectIdRef.current !== requestProjectId || routeKeyRef.current !== requestRouteKey || dialog !== folderDialog) return;
      const existing = listing.entries.find((entry) => entry.path === target);
      if (existing) {
        setDialog({
          ...folderDialog,
          error: existing.kind === "dir" ? `${target} is already a folder.` : `${target} is an existing file. Choose another name.`,
        });
        return;
      }
      setDialog(undefined);
      mutationStarted = true;
      void runMutation(
        `Created ${target.split("/").pop() ?? target}`,
        async () => {
          const result = await service.createDirectory(projectId, target);
          if (result.created !== true || result.path !== target) {
            throw new Error("The server did not confirm creation of the requested folder.");
          }
        },
        [currentPath],
        undefined,
        (readbacks) => readbacks.some((readback) => readback.path === currentPath && readback.entries.some((entry) => entry.path === target && entry.kind === "dir")),
      );
    }).catch((error: unknown) => {
      if (!controller.signal.aborted && projectIdRef.current === requestProjectId && routeKeyRef.current === requestRouteKey) {
        setDialog({ ...folderDialog, error: `Could not check for an existing folder: ${workspaceErrorMessage(error)}` });
        handleOperationError(error);
      }
    }).finally(() => {
      if (!mutationStarted && projectIdRef.current === requestProjectId) setOperationBusy(false);
    });
  };

  const handleDestinationSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (dialog?.kind !== "destination") return;
    let destination: string;
    try {
      destination = normalizeWorkspacePath(dialog.destination);
      if (!destination) throw new Error("A destination path is required");
      if (destination === dialog.entry.path) throw new Error("Choose a different destination path");
      if (dialog.entry.kind === "dir" && destination.startsWith(`${dialog.entry.path}/`)) throw new Error("A folder cannot be moved or copied into itself");
    } catch (error) {
      setDialog({ ...dialog, error: error instanceof Error ? error.message : "Enter a valid destination path" });
      return;
    }
    const destinationDialog = dialog;
    const operation = dialog.operation;
    const entry = dialog.entry;
    const showConfirmation = (existingDestination?: WorkspaceEntry) => {
      if (existingDestination?.kind === "dir") {
        setDialog((previous) => previous?.kind === "destination" ? { ...previous, error: "The destination path is an existing folder. Choose a new full path to avoid ambiguous overwrite behavior." } : previous);
        return;
      }
      const destinationExists = existingDestination !== undefined;
      setDialog({
        kind: "confirm",
        title: destinationExists ? "Replace existing destination?" : operation === "move" ? "Move this item?" : "Copy this item?",
        message: destinationExists
          ? `${destination} already exists in the live Pod workspace. Replace it with ${entry.path}?`
          : `${operation === "move" ? "Move" : "Copy"} ${entry.path} to ${destination}?`,
        confirmLabel: destinationExists ? "Replace destination" : operation === "move" ? "Move item" : "Copy item",
        destructive: destinationExists || operation === "move",
        onConfirm: () => {
          void runMutation(
            `${operation === "move" ? "Moved" : "Copied"} ${entry.name}`,
            () => (operation === "move" ? service.moveEntry(projectId, entry.path, destination) : service.copyEntry(projectId, entry.path, destination)).then(() => undefined),
            [currentPath, parentWorkspacePath(entry.path), parentWorkspacePath(destination)],
            operation === "move" && selectedPath && (selectedPath === entry.path || selectedPath.startsWith(`${entry.path}/`)) ? clearSelection : undefined,
            (readbacks) => {
              const sourceParent = readbacks.find((readback) => readback.path === parentWorkspacePath(entry.path));
              const destinationParent = readbacks.find((readback) => readback.path === parentWorkspacePath(destination));
              const sourceExists = sourceParent?.entries.some((item) => item.path === entry.path);
              const destinationExists = destinationParent?.entries.some((item) => item.path === destination);
              return operation === "move" ? sourceExists === false && destinationExists === true : destinationExists === true;
            },
          );
        },
      });
    };
    const lookupProjectId = projectId;
    const lookupRouteKey = routeKeyRef.current;
    const controller = new AbortController();
    preflightControllerRef.current?.abort();
    preflightControllerRef.current = controller;
    setOperationBusy(true);
    void service.listEntries(projectId, parentWorkspacePath(destination), { signal: controller.signal }).then((listing) => {
      if (controller.signal.aborted || projectIdRef.current !== lookupProjectId || routeKeyRef.current !== lookupRouteKey || dialog !== destinationDialog) return;
      showConfirmation(listing.entries.find((item) => item.path === destination));
    }).catch((error: unknown) => {
      if (!controller.signal.aborted && projectIdRef.current === lookupProjectId && routeKeyRef.current === lookupRouteKey) {
        setDialog((previous) => previous?.kind === "destination" ? {
          ...previous,
          error: `Could not check the destination before writing: ${workspaceErrorMessage(error)}`,
        } : previous);
        handleOperationError(error);
      }
    }).finally(() => {
      if (projectIdRef.current === lookupProjectId && routeKeyRef.current === lookupRouteKey) setOperationBusy(false);
    });
  };

  const handleDelete = (entry: WorkspaceEntry) => {
    setDialog({
      kind: "confirm",
      title: `Delete ${entry.kind === "dir" ? "folder" : "file"}?`,
      message: `${entry.path} will be deleted from the live Pod workspace. This action cannot be undone.`,
      confirmLabel: "Delete",
      destructive: true,
      onConfirm: () => {
        void runMutation(
          `Deleted ${entry.name}`,
          () => service.deleteEntry(projectId, entry.path).then(() => undefined),
          [currentPath, ...(parentWorkspacePath(entry.path) === currentPath ? [] : [parentWorkspacePath(entry.path)])],
          selectedPath && (selectedPath === entry.path || selectedPath.startsWith(`${entry.path}/`)) ? clearSelection : undefined,
          (readbacks) => readbacks.every((readback) => !readback.entries.some((item) => item.path === entry.path)),
        );
      },
    });
  };

  const handleUploadInput = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!files.length) return;
    const targets = (() => {
      try {
        return files.map((file) => ({ file, path: joinWorkspacePath(currentPath, file.name) }));
      } catch (error) {
        setNotice({ kind: "error", message: error instanceof Error ? error.message : "Invalid upload file name" });
        return [];
      }
    })();
    if (!targets.length) return;
    let mutationStarted = false;
    const upload = () => {
      setDialog(undefined);
      mutationStarted = true;
      void runMutation(
        `Uploaded ${files.length} file${files.length === 1 ? "" : "s"}`,
        async () => {
          for (const target of targets) {
            await service.upload(projectId, target.path, target.file);
            const readback = await service.download(projectId, target.path);
            const [uploadedBytes, readbackBytes] = await Promise.all([target.file.arrayBuffer(), readback.blob.arrayBuffer()]);
            const expected = new Uint8Array(uploadedBytes);
            const actual = new Uint8Array(readbackBytes);
            if (readback.path !== target.path || expected.length !== actual.length || expected.some((byte, index) => byte !== actual[index])) {
              throw new Error(`Upload readback for ${target.path} did not match the selected file.`);
            }
          }
        },
        [currentPath],
        undefined,
        (readbacks) => targets.every((target) => readbacks.some((readback) => readback.entries.some((entry) => entry.path === target.path && entry.kind === "file"))),
      );
    };
    const requestProjectId = projectId;
    const requestRouteKey = routeKeyRef.current;
    const controller = new AbortController();
    preflightControllerRef.current?.abort();
    preflightControllerRef.current = controller;
    setOperationBusy(true);
    void service.listEntries(projectId, currentPath, { signal: controller.signal }).then((listing) => {
      if (controller.signal.aborted || projectIdRef.current !== requestProjectId || routeKeyRef.current !== requestRouteKey) return;
      const conflicts = targets.filter(({ path }) => listing.entries.some((entry) => entry.path === path));
      if (conflicts.length) {
        setDialog({
          kind: "confirm",
          title: "Replace existing files?",
          message: `${conflicts.map(({ path }) => path).join(", ")} already exist${conflicts.length === 1 ? "s" : ""}. Uploading will overwrite them in the live Pod workspace.`,
          confirmLabel: "Replace and upload",
          destructive: true,
          onConfirm: upload,
        });
      } else upload();
    }).catch((error: unknown) => {
      if (!controller.signal.aborted && projectIdRef.current === requestProjectId && routeKeyRef.current === requestRouteKey) {
        setNotice({ kind: "error", message: `Could not check for upload conflicts: ${workspaceErrorMessage(error)}` });
        handleOperationError(error);
      }
    }).finally(() => {
      if (!mutationStarted && projectIdRef.current === requestProjectId) setOperationBusy(false);
    });
  };

  const openCreateFile = () => setDialog({ kind: "create-file", name: "", content: "" });
  const openCreateFolder = () => {
    setDialog({ kind: "create-folder", name: "" });
  };

  const renderAvailability = () => {
    const kind = availability === "empty" || availability === "loading" || availability === "ready" ? "error" : availability;
    return (
      <div className={`files-availability files-availability-${kind}`} role="status">
        <div className="files-availability-mark" aria-hidden="true">{kind === "offline" ? "×" : "!"}</div>
        <div>
          <h3>{availabilityTitle(kind)}</h3>
          <p>{availability === "error" ? workspaceErrorMessage(snapshot.error) : availabilityDetail(kind)}</p>
          <button type="button" className="files-button files-button-primary" onClick={() => void loadDirectory(currentPath, true)} disabled={operationBusy || networkOffline}>
            {networkOffline ? "Reconnect to retry" : "Retry"}
          </button>
        </div>
      </div>
    );
  };

  const renderPreview = () => {
    if (!selectedPath || !selectedEntry) {
      return <div className="files-preview-placeholder"><span className="files-placeholder-icon" aria-hidden="true">▧</span><h3>Select a file</h3><p>Choose a text file to preview and edit it in the live Pod workspace.</p></div>;
    }
    // ``idle`` carries no path at all, so it must be tested before the path
    // comparison: otherwise the union is not narrowed and ``preview.path`` is read
    // off a variant that does not declare it.
    if (preview.kind === "idle" || preview.kind === "loading" || preview.path !== selectedPath) {
      return <div className="files-preview-message" role="status" aria-live="polite"><span className="files-spinner" aria-hidden="true" />Loading preview…</div>;
    }
    if (preview.kind === "error") {
      const kind = classifyWorkspaceError(preview.error);
      return (
        <div className="files-preview-message files-preview-error" role="alert">
          <h3>{kind === "unsupported" ? "Preview unavailable" : "Could not load preview"}</h3>
          <p>{workspaceErrorMessage(preview.error)}</p>
          <div className="files-preview-actions">
            <button type="button" className="files-button" onClick={() => setPreviewNonce((value) => value + 1)} disabled={operationBusy}>Retry preview</button>
            <button type="button" className="files-button" onClick={() => void handleDownload(selectedEntry)} disabled={operationBusy || unavailable || networkOffline}>Download file</button>
          </div>
        </div>
      );
    }
    // ``idle`` can survive the two checks above when nothing has been requested yet
    // (or after a reset). Only ``ready`` carries a preview, so the editor branch must
    // say so explicitly: reading ``preview.preview`` off the union left ``idle`` in
    // play and the access was unchecked.
    if (preview.kind !== "ready") {
      return <div className="files-preview-message" role="status" aria-live="polite"><span className="files-spinner" aria-hidden="true" />Loading preview…</div>;
    }
    return (
      <div className="files-editor-wrap">
        <textarea
          className="files-editor"
          value={editorValue}
          onChange={(event) => { setEditorValue(event.target.value); setDirty(true); }}
          aria-label={`Editing ${preview.preview.name}`}
          spellCheck={false}
          readOnly={preview.preview.truncated || unavailable || networkOffline}
        />
        {preview.preview.truncated && <p className="files-editor-note">This preview is truncated at the server limit. Download the complete file before editing it.</p>}
        <div className="files-preview-actions">
          <button type="button" className="files-button" onClick={() => void handleDownload(selectedEntry)} disabled={operationBusy || unavailable || networkOffline}>Download</button>
          <button type="button" className="files-button files-button-primary" onClick={handleSave} disabled={operationBusy || !dirty || preview.preview.truncated || unavailable || networkOffline}>
            {operationBusy ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>
    );
  };

  return (
    <section className={`files-workspace ${className}`} aria-labelledby="files-workspace-title" aria-busy={snapshot.status === "loading" || operationBusy}>
      <header className="files-header">
        <div>
          <p className="files-eyebrow">Project workspace</p>
          <div className="files-title-line">
            <h1 id="files-workspace-title">{title}</h1>
            <span className={`files-status files-status-${availability}`} role="status">{availabilityLabel(availability)}</span>
          </div>
          <p className="files-subtitle">Live files from the project Pod</p>
        </div>
        <div className="files-header-actions">
          <button type="button" className="files-button" onClick={() => void loadDirectory(currentPath, true)} disabled={operationBusy || networkOffline}>
            {snapshot.status === "loading" ? "Refreshing…" : "Refresh"}
          </button>
          <button type="button" className="files-button" onClick={openCreateFile} disabled={writeDisabled}>New file</button>
          <button type="button" className="files-button" onClick={openCreateFolder} disabled={writeDisabled} title="Create a folder" aria-label="Create a folder">New folder</button>
          <button type="button" className="files-button files-button-primary" onClick={() => uploadInputRef.current?.click()} disabled={writeDisabled}>Upload</button>
          <input ref={uploadInputRef} className="files-visually-hidden" type="file" multiple onChange={handleUploadInput} aria-label="Upload files" />
        </div>
      </header>

      {networkOffline && snapshot.status === "ready" && <div className="files-offline-banner" role="status">You are offline. Cached directory contents are shown; write actions are paused until the connection returns.</div>}
      {notice && <div className={`files-notice files-notice-${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>{notice.message}</div>}

      <form className="files-pathbar" onSubmit={handlePathSubmit}>
        <label htmlFor="files-path-input">Path</label>
        <button type="button" className="files-path-home" onClick={() => navigateTo("")} aria-label="Go to workspace root">Workspace</button>
        <span className="files-path-separator" aria-hidden="true">/</span>
        <input id="files-path-input" value={pathInput} onChange={(event) => setPathInput(event.target.value)} placeholder="folder/subfolder" autoComplete="off" />
        <button type="submit" className="files-button" disabled={operationBusy}>Go</button>
      </form>

      <div className="files-layout">
        <aside className="files-sidebar" aria-label="Workspace folders">
          <div className="files-panel-heading"><h2>Folders</h2><span>{projectId}</span></div>
          <div className="files-tree" role="tree" aria-label="Project folders">
            <TreeNode path="" label="Workspace" depth={0} directories={directories} expanded={expanded} currentPath={currentPath} onToggle={handleToggleDirectory} onNavigate={navigateTo} />
          </div>
          <p className="files-sidebar-note">Only the live project Pod is used for these files.</p>
        </aside>

        <section className="files-list-panel" aria-labelledby="files-list-title">
          <div className="files-panel-heading">
            <div><h2 id="files-list-title">{currentPath || "Workspace"}</h2><span>{snapshot.status === "loading" ? "Loading…" : `${entries.length} item${entries.length === 1 ? "" : "s"}`}</span></div>
            {currentPath && <button type="button" className="files-button files-button-quiet" onClick={() => navigateTo(parentWorkspacePath(currentPath))}>Up</button>}
          </div>
          {unavailable ? renderAvailability() : (snapshot.status === "idle" || snapshot.status === "loading") && entries.length === 0 ? (
            <div className="files-list-state" role="status"><span className="files-spinner" aria-hidden="true" />Loading workspace…</div>
          ) : snapshot.status === "ready" && entries.length === 0 ? (
            <div className="files-list-state files-list-empty"><span className="files-placeholder-icon" aria-hidden="true">□</span><h3>This folder is empty</h3><p>Create a file or upload one to get started.</p></div>
          ) : (
            <div className="files-entry-list" role="list" aria-label={`Contents of ${currentPath || "workspace"}`}>
              {entries.map((entry) => (
                <div className={`files-entry-row${selectedPath === entry.path ? " is-selected" : ""}`} role="listitem" key={entry.path}>
                  <button type="button" className="files-entry-main" onClick={() => handleEntryOpen(entry)} aria-current={selectedPath === entry.path ? "true" : undefined}>
                    <span className={`files-entry-icon files-entry-icon-${entry.kind}`} aria-hidden="true">{entry.kind === "dir" ? "▰" : "▤"}</span>
                    <span className="files-entry-name"><strong>{entry.name}</strong><small>{entry.kind === "dir" ? "Folder" : `${sizeLabel(entry.size)} · ${modifiedLabel(entry.modified_at)}`}</small></span>
                  </button>
                  <div className="files-entry-actions">
                    {entry.kind === "file" && <button type="button" className="files-action-button" onClick={() => void handleDownload(entry)} disabled={operationBusy || unavailable || networkOffline}>Download</button>}
                    <button type="button" className="files-action-button" onClick={() => setDialog({ kind: "destination", operation: "move", entry, destination: entry.path })} disabled={writeDisabled}>Move</button>
                    <button type="button" className="files-action-button" onClick={() => setDialog({ kind: "destination", operation: "copy", entry, destination: entry.path })} disabled={writeDisabled}>Copy</button>
                    <button type="button" className="files-action-button files-action-danger" onClick={() => handleDelete(entry)} disabled={writeDisabled}>Delete</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="files-preview-panel" aria-labelledby="files-preview-title">
          <div className="files-panel-heading">
            <div><h2 id="files-preview-title">Preview</h2><span>{selectedEntry?.path ?? "No file selected"}</span></div>
            {selectedEntry && <span className="files-preview-kind">{selectedEntry.kind === "file" ? "Text or binary" : "Folder"}</span>}
          </div>
          {renderPreview()}
        </section>
      </div>

      {dialog?.kind === "confirm" && (
        <ModalShell title={dialog.title} onClose={() => !operationBusy && setDialog(undefined)}>
          <div className="files-modal-body"><p>{dialog.message}</p></div>
          <div className="files-modal-actions"><button type="button" className="files-button" onClick={() => setDialog(undefined)} disabled={operationBusy}>Cancel</button><button type="button" className={`files-button ${dialog.destructive ? "files-button-danger" : "files-button-primary"}`} onClick={() => { const action = dialog.onConfirm; setDialog(undefined); void action(); }} disabled={operationBusy}>{dialog.confirmLabel}</button></div>
        </ModalShell>
      )}

      {dialog?.kind === "create-file" && (
        <ModalShell title="Create file" onClose={() => setDialog(undefined)}>
          <form className="files-modal-form" onSubmit={handleCreateFile}>
            <label htmlFor="files-create-file-name">File name<input id="files-create-file-name" autoFocus value={dialog.name} onChange={(event) => setDialog({ ...dialog, name: event.target.value, error: undefined })} placeholder="notes.txt" /></label>
            <label htmlFor="files-create-file-content">Initial content<textarea id="files-create-file-content" value={dialog.content} onChange={(event) => setDialog({ ...dialog, content: event.target.value })} placeholder="Optional" /></label>
            {dialog.error && <p className="files-form-error" role="alert">{dialog.error}</p>}
            <div className="files-modal-actions"><button type="button" className="files-button" onClick={() => setDialog(undefined)}>Cancel</button><button type="submit" className="files-button files-button-primary">Create file</button></div>
          </form>
        </ModalShell>
      )}

      {dialog?.kind === "create-folder" && (
        <ModalShell title="Create folder" onClose={() => setDialog(undefined)}>
          <form className="files-modal-form" onSubmit={handleCreateFolder}>
            <label htmlFor="files-create-folder-name">Folder name<input id="files-create-folder-name" autoFocus value={dialog.name} onChange={(event) => setDialog({ ...dialog, name: event.target.value, error: undefined })} placeholder="docs" /></label>
            {dialog.error && <p className="files-form-error" role="alert">{dialog.error}</p>}
            <div className="files-modal-actions"><button type="button" className="files-button" onClick={() => setDialog(undefined)}>Cancel</button><button type="submit" className="files-button files-button-primary">Create folder</button></div>
          </form>
        </ModalShell>
      )}

      {dialog?.kind === "destination" && (
        <ModalShell title={`${dialog.operation === "move" ? "Move" : "Copy"} item`} onClose={() => setDialog(undefined)}>
          <form className="files-modal-form" onSubmit={handleDestinationSubmit}>
            <p className="files-dialog-path">Source: <code>{dialog.entry.path}</code></p>
            <label htmlFor="files-destination-path">Destination path<input id="files-destination-path" autoFocus value={dialog.destination} onChange={(event) => setDialog({ ...dialog, destination: event.target.value, error: undefined })} /></label>
            {dialog.error && <p className="files-form-error" role="alert">{dialog.error}</p>}
            <div className="files-modal-actions"><button type="button" className="files-button" onClick={() => setDialog(undefined)}>Cancel</button><button type="submit" className="files-button files-button-primary">Continue</button></div>
          </form>
        </ModalShell>
      )}

    </section>
  );
}
