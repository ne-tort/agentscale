import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { Link, matchPath, useLocation } from "react-router-dom";
import "./workspace-shell.css";

export const WORKSPACE_ROUTE_KEYS = [
  "overview",
  "runtime",
  "chat",
  "files",
  "runs",
  "approvals",
  "resources",
  "content",
  "equipment",
  "automations",
  "models",
  "settings",
  "module",
] as const;

export type WorkspaceRouteKey = typeof WORKSPACE_ROUTE_KEYS[number];
export type WorkspaceNavRouteKey = WorkspaceRouteKey;
type WorkspaceBaseRouteKey = Exclude<WorkspaceRouteKey, "module">;
type WorkspaceChildRouteKey = Exclude<WorkspaceBaseRouteKey, "overview">;
export type WorkspaceNavSection = "workspace" | "operations" | "manage";
export type WorkspaceIconName =
  | "grid"
  | "message"
  | "file"
  | "pulse"
  | "layers"
  | "server"
  | "clock"
  | "settings"
  | "check";

export interface WorkspaceRouteDefinition {
  /** Concrete URL used by the shell's links. */
  readonly href: string;
  /** React Router path pattern used to calculate the active route. */
  readonly pattern: string;
  /** Optional label for a contextual destination such as a module. */
  readonly label?: string;
  /** Optional confirmed resource identifier for visible scope. */
  readonly scopeId?: string;
  /** Bound the number of path segments accepted after a wildcard route base. */
  readonly maxTailSegments?: number;
}

/**
 * Every route rendered by the shell must be declared here. Keeping the
 * concrete href and matching pattern together prevents a link from drifting
 * away from the route that marks it active.
 */
export interface WorkspaceRouteContract extends Readonly<Record<WorkspaceBaseRouteKey, WorkspaceRouteDefinition>> {
  /** Present only when a confirmed module ID is in the current route context. */
  readonly module?: WorkspaceRouteDefinition;
}

export interface WorkspaceCabinetContext {
  readonly id: string;
  readonly name: string;
  readonly description?: string;
}

export interface WorkspaceProjectContext {
  readonly id: string;
  readonly name: string;
  readonly status?: string;
  readonly statusLabel?: string;
}

export interface WorkspaceAccountContext {
  readonly id?: string;
  readonly displayName: string;
  readonly email?: string;
  readonly role?: string;
  readonly avatarUrl?: string;
}

export interface WorkspaceShellProps {
  readonly children: ReactNode;
  readonly cabinet: WorkspaceCabinetContext;
  readonly project: WorkspaceProjectContext;
  readonly account: WorkspaceAccountContext;
  readonly routeContract: WorkspaceRouteContract;
  readonly onNavigate?: (route: WorkspaceRouteKey) => void;
  readonly onSignOut: () => void | Promise<void>;
  readonly initialSidebarCollapsed?: boolean;
  readonly className?: string;
}

export interface WorkspaceRouteMeta {
  readonly label: string;
  readonly section: WorkspaceNavSection;
  readonly icon: WorkspaceIconName;
}

export const WORKSPACE_ROUTE_META: Readonly<Record<WorkspaceRouteKey, WorkspaceRouteMeta>> = {
  overview: { label: "Overview", section: "workspace", icon: "grid" },
  runtime: { label: "Runtime", section: "workspace", icon: "pulse" },
  chat: { label: "Chat", section: "workspace", icon: "message" },
  files: { label: "Files", section: "workspace", icon: "file" },
  runs: { label: "Runs", section: "operations", icon: "clock" },
  approvals: { label: "Approvals", section: "operations", icon: "check" },
  resources: { label: "Resources", section: "operations", icon: "layers" },
  content: { label: "Content", section: "operations", icon: "file" },
  equipment: { label: "Equipment", section: "operations", icon: "server" },
  automations: { label: "Automations", section: "operations", icon: "clock" },
  models: { label: "Models", section: "operations", icon: "grid" },
  settings: { label: "Settings", section: "manage", icon: "settings" },
  module: { label: "Module", section: "workspace", icon: "layers" },
};

export interface WorkspaceNavItem {
  readonly key: WorkspaceNavRouteKey;
  readonly label: string;
  readonly section: WorkspaceNavSection;
  readonly icon: WorkspaceIconName;
}

const WORKSPACE_NAV_ROUTE_KEYS: readonly WorkspaceNavRouteKey[] = [
  "overview",
  "runtime",
  "chat",
  "files",
  "runs",
  "approvals",
  "resources",
  "content",
  "equipment",
  "automations",
  "models",
  "settings",
  "module",
];

export const WORKSPACE_NAV_ITEMS: readonly WorkspaceNavItem[] = WORKSPACE_NAV_ROUTE_KEYS.map((key) => ({
  key,
  label: WORKSPACE_ROUTE_META[key].label,
  section: WORKSPACE_ROUTE_META[key].section,
  icon: WORKSPACE_ROUTE_META[key].icon,
}));

export interface WorkspaceNavGroup {
  readonly label: string;
  readonly section: WorkspaceNavSection;
  readonly items: readonly WorkspaceNavItem[];
}

export const WORKSPACE_NAV_GROUPS: readonly WorkspaceNavGroup[] = [
  {
    label: "Workspace",
    section: "workspace",
    items: WORKSPACE_NAV_ITEMS.filter((item) => item.section === "workspace"),
  },
  {
    label: "Operations",
    section: "operations",
    items: WORKSPACE_NAV_ITEMS.filter((item) => item.section === "operations"),
  },
  {
    label: "Manage",
    section: "manage",
    items: WORKSPACE_NAV_ITEMS.filter((item) => item.section === "manage"),
  },
];

export interface WorkspaceRouteContractOptions {
  readonly projectId: string;
  /** Concrete project URL, useful when the host router uses a custom prefix. */
  readonly projectPath?: string;
  /** Matching project pattern used by the host router. */
  readonly projectPattern?: string;
  /** A module is linked only when its ID is supplied by the confirmed route. */
  readonly moduleId?: string;
  readonly moduleLabel?: string;
}

function trimTrailingSlashes(value: string): string {
  return value.replace(/\/+$/, "");
}

function trimRouteWildcard(value: string): string {
  return value.replace(/\/\*$/, "");
}

function encodePathSegment(value: string): string {
  return encodeURIComponent(value);
}

/** Build the canonical project-scoped route contract used by the shell. */
export function createWorkspaceRouteContract({
  projectId,
  projectPath,
  projectPattern = "/projects/:projectId",
  moduleId,
  moduleLabel,
}: WorkspaceRouteContractOptions): WorkspaceRouteContract {
  const concreteProjectPath = trimRouteWildcard(trimTrailingSlashes(projectPath ?? `/projects/${encodePathSegment(projectId)}`));
  const matchingProjectPattern = trimRouteWildcard(trimTrailingSlashes(projectPattern));
  const child = (segment: WorkspaceChildRouteKey, nested = true): WorkspaceRouteDefinition => ({
    href: `${concreteProjectPath}/${segment}`,
    pattern: `${matchingProjectPattern}/${segment}${nested ? "/*" : ""}`,
    ...(segment === "chat" ? { maxTailSegments: 1 } : {}),
  });

  return {
    overview: { href: concreteProjectPath, pattern: matchingProjectPattern },
    runtime: child("runtime"),
    chat: child("chat"),
    files: child("files"),
    runs: child("runs", false),
    approvals: child("approvals", false),
    resources: child("resources"),
    content: child("content", false),
    equipment: child("equipment", false),
    automations: child("automations", false),
    models: child("models", false),
    settings: child("settings", false),
    ...(moduleId ? {
      module: {
        href: `${concreteProjectPath}/modules/${encodePathSegment(moduleId)}`,
        pattern: `${matchingProjectPattern}/modules/:moduleId/*`,
        label: moduleLabel ?? moduleId,
        scopeId: moduleId,
      },
    } : {}),
  };
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function getRouteDefinition(
  contract: WorkspaceRouteContract,
  key: WorkspaceRouteKey,
): WorkspaceRouteDefinition | undefined {
  return key === "module" ? contract.module : contract[key];
}

/** Fail early when a host router passes an incomplete or unsafe route contract. */
export function assertWorkspaceRouteContract(contract: WorkspaceRouteContract): WorkspaceRouteContract {
  const routeKeys = contract.module ? WORKSPACE_ROUTE_KEYS : WORKSPACE_ROUTE_KEYS.filter((key) => key !== "module");
  const invalidKeys = routeKeys.filter((key) => {
    const route = getRouteDefinition(contract, key);
    return !route
      || !isNonEmptyString(route.href)
      || !route.href.startsWith("/")
      || route.href.startsWith("//")
      || !isNonEmptyString(route.pattern)
      || !route.pattern.startsWith("/")
      || route.pattern.startsWith("//")
      || (key === "module" && (!isNonEmptyString(route.scopeId)
        || matchPath({ path: route.pattern, end: false }, normalizePathname(route.href))?.params.moduleId !== route.scopeId))
      || !workspaceRouteMatches(route, route.href);
  });

  if (invalidKeys.length > 0) {
    throw new Error(`Workspace route contract is incomplete: ${invalidKeys.join(", ")}`);
  }

  const ambiguousRoutes = routeKeys.filter((key) => {
    const route = getRouteDefinition(contract, key);
    return Boolean(route && routeKeys.some((candidate) => {
      const candidateRoute = getRouteDefinition(contract, candidate);
      return candidate !== key
        && candidateRoute !== undefined
        && !(candidate === "overview" && candidateRoute.pattern.endsWith("/*"))
        && workspaceRouteMatches(candidateRoute, route.href);
    }));
  });
  if (ambiguousRoutes.length > 0) {
    throw new Error(`Workspace route contract has overlapping active routes: ${ambiguousRoutes.join(", ")}`);
  }

  return contract;
}

export function getWorkspaceRoutePath(
  routeContract: WorkspaceRouteContract,
  route: WorkspaceRouteKey,
): string {
  const contract = assertWorkspaceRouteContract(routeContract);
  const destination = getRouteDefinition(contract, route);
  if (!destination) throw new Error(`Workspace route is unavailable: ${route}`);
  return destination.href;
}

function normalizePathname(pathname: string): string {
  const pathOnly = pathname.split(/[?#]/, 1)[0] || "/";
  if (pathOnly === "/") return pathOnly;
  return pathOnly.replace(/\/+$/, "");
}

/** Match exact routes and child routes represented with a trailing `/*`. */
export function workspaceRouteMatches(
  route: WorkspaceRouteDefinition,
  pathname: string,
): boolean {
  const normalizedPathname = normalizePathname(pathname);
  const pattern = trimTrailingSlashes(route.pattern);
  const matches = pattern.endsWith("/*")
    ? Boolean(matchPathSafely(pattern, normalizedPathname) || matchPathSafely(pattern.slice(0, -2) || "/", normalizedPathname, false))
    : matchPathSafely(pattern, normalizedPathname);
  if (!matches) return false;
  if (route.maxTailSegments !== undefined) {
    const tail = matchPath({ path: pattern, end: false }, normalizedPathname)?.params["*"] ?? "";
    if (tail.split("/").filter(Boolean).length > route.maxTailSegments) return false;
  }
  return true;
}

function matchPathSafely(pattern: string, pathname: string, end = true): boolean {
  // Importing matchPath lazily is unnecessary in the browser, but keeping the
  // call behind this helper makes the route contract functions easy to test.
  return Boolean(requireMatchPath({ path: pattern, end }, pathname));
}

function requireMatchPath(
  pattern: { path: string; end: boolean },
  pathname: string,
): { pathname: string } | null {
  // This indirection is typed against react-router-dom below and keeps all
  // active matching in one place.
  return matchPath(pattern, pathname);
}

export function getActiveWorkspaceRoute(
  pathname: string,
  routeContract: WorkspaceRouteContract,
): WorkspaceRouteKey | undefined {
  const contract = assertWorkspaceRouteContract(routeContract);
  return getAvailableRouteKeys(contract)
    .sort((left, right) => (getRouteDefinition(contract, right)?.pattern.length ?? 0) - (getRouteDefinition(contract, left)?.pattern.length ?? 0))
    .find((key) => {
      const route = getRouteDefinition(contract, key);
      return route ? routeMatchesCurrentScope(route, pathname) : false;
    });
}

export interface WorkspaceRouteResolution {
  readonly activeRoute?: WorkspaceRouteKey;
  readonly scope: "current-project" | "project-context-mismatch" | "other-project" | "other-module" | "outside-project";
  readonly requestedProjectId?: string;
  readonly requestedModuleId?: string;
  readonly contextProjectId?: string;
}

export function resolveWorkspaceRoute(
  pathname: string,
  routeContract: WorkspaceRouteContract,
  selectedProjectId?: string,
): WorkspaceRouteResolution {
  const contract = assertWorkspaceRouteContract(routeContract);
  const projectPattern = trimRouteWildcard(trimTrailingSlashes(contract.overview.pattern));
  const currentScope = matchPath({ path: projectPattern, end: true }, contract.overview.href)?.params;
  const requestedScope = matchPath({ path: projectPattern, end: false }, normalizePathname(pathname))?.params;
  if (!currentScope || !requestedScope) return { scope: "outside-project" };
  const scopeKeys = Object.keys(currentScope);
  const projectKey = scopeKeys.includes("projectId") ? "projectId" : scopeKeys.at(-1);
  const currentProjectId = projectKey ? currentScope[projectKey] : undefined;
  const requestedProjectId = projectKey ? requestedScope[projectKey] : undefined;
  if (!currentProjectId || !requestedProjectId) return { scope: "outside-project" };
  if (selectedProjectId && currentProjectId !== selectedProjectId) {
    return { scope: "project-context-mismatch", requestedProjectId: currentProjectId, contextProjectId: selectedProjectId };
  }
  if (scopeKeys.some((key) => requestedScope[key] !== currentScope[key])) return { scope: "other-project", requestedProjectId };
  if (contract.module) {
    const moduleScope = `${projectPattern}/modules/:moduleId/*`;
    const requestedModuleId = matchPath({ path: moduleScope, end: false }, normalizePathname(pathname))?.params.moduleId;
    if (requestedModuleId && requestedModuleId !== contract.module.scopeId) {
      return { scope: "other-module", requestedProjectId, requestedModuleId };
    }
  }
  const activeRoute = getActiveWorkspaceRoute(pathname, contract);
  return { activeRoute, scope: "current-project", requestedProjectId };
}

function getAvailableRouteKeys(contract: WorkspaceRouteContract): WorkspaceRouteKey[] {
  return WORKSPACE_ROUTE_KEYS.filter((key) => key !== "module" || Boolean(contract.module)) as WorkspaceRouteKey[];
}

function routeMatchesCurrentScope(route: WorkspaceRouteDefinition, pathname: string): boolean {
  if (!workspaceRouteMatches(route, pathname)) return false;
  const expected = matchPath({ path: route.pattern, end: false }, normalizePathname(route.href))?.params;
  const requested = matchPath({ path: route.pattern, end: false }, normalizePathname(pathname))?.params;
  if (!expected || !requested) return false;
  return Object.entries(expected).every(([key, value]) => key === "*" || requested[key] === value);
}

export interface WorkspaceNavigationLink extends WorkspaceNavItem {
  readonly href: string;
  readonly active: boolean;
}

export function getWorkspaceNavigation(
  routeContract: WorkspaceRouteContract,
  activeRoute?: WorkspaceRouteKey,
): readonly WorkspaceNavigationLink[] {
  const contract = assertWorkspaceRouteContract(routeContract);
  return WORKSPACE_NAV_ITEMS.flatMap((item) => {
    const route = getRouteDefinition(contract, item.key);
    return route ? [{
      ...item,
      label: route.label ?? item.label,
      href: route.href,
      active: activeRoute === item.key,
    }] : [];
  });
}

export interface WorkspaceBreadcrumb {
  readonly label: string;
  readonly href?: string;
  readonly current: boolean;
}

export function getWorkspaceBreadcrumbs({
  cabinetName,
  projectName,
  projectId,
  activeRoute,
  routeContract,
}: {
  readonly cabinetName: string;
  readonly projectName: string;
  readonly projectId: string;
  readonly activeRoute?: WorkspaceRouteKey;
  readonly routeContract: WorkspaceRouteContract;
}): readonly WorkspaceBreadcrumb[] {
  const contract = assertWorkspaceRouteContract(routeContract);
  const breadcrumbs: WorkspaceBreadcrumb[] = [
    { label: cabinetName, current: false },
    {
      label: `${projectName} · ${projectId}`,
      href: activeRoute === "overview" || !activeRoute ? undefined : contract.overview.href,
      current: activeRoute === "overview" || !activeRoute,
    },
  ];

  if (activeRoute && activeRoute !== "overview") {
    const definition = getRouteDefinition(contract, activeRoute);
    breadcrumbs.push({
      label: definition?.scopeId
        ? `${definition.label ?? WORKSPACE_ROUTE_META[activeRoute].label} · ${definition.scopeId}`
        : definition?.label ?? WORKSPACE_ROUTE_META[activeRoute].label,
      current: true,
    });
  }

  return breadcrumbs;
}

const ICON_PATHS: Readonly<Record<WorkspaceIconName, ReactNode>> = {
  grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  message: <><path d="M20 11.5a7.5 7.5 0 0 1-8 7.45 8.9 8.9 0 0 1-3.2-.58L4 20l1.63-3.7A7.16 7.16 0 0 1 4.5 11.5 7.5 7.5 0 0 1 12 4a7.5 7.5 0 0 1 8 7.5Z" /><path d="M8.5 11.5h.01M12 11.5h.01M15.5 11.5h.01" strokeWidth="2.4" strokeLinecap="round" /></>,
  file: <><path d="M6 3.5h8l4 4V20.5H6z" /><path d="M14 3.5v4h4M9 12h6M9 16h4" /></>,
  pulse: <><path d="M3 12h4l2.1-5.5 4 11L15.2 12H21" /><path d="M12 3.5a8.5 8.5 0 1 0 8.5 8.5" /></>,
  layers: <><path d="m12 3.5 8.5 4.4L12 12.3 3.5 7.9 12 3.5Z" /><path d="m4.6 12 7.4 3.8 7.4-3.8M4.6 16.1l7.4 3.8 7.4-3.8" /></>,
  server: <><rect x="4" y="4" width="16" height="6" rx="1.5" /><rect x="4" y="14" width="16" height="6" rx="1.5" /><path d="M8 7h.01M8 17h.01M11 7h5M11 17h5" strokeLinecap="round" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3.5 2" strokeLinecap="round" /></>,
  settings: <><path d="M12 8.3a3.7 3.7 0 1 0 0 7.4 3.7 3.7 0 0 0 0-7.4Z" /><path d="m19.4 13.4 1.1.8-1.8 3.1-1.3-.5a7.8 7.8 0 0 1-1.8 1.1l-.2 1.4h-3.6l-.2-1.4a7.8 7.8 0 0 1-1.8-1.1l-1.3.5-1.8-3.1 1.1-.8a7.3 7.3 0 0 1 0-2.8l-1.1-.8 1.8-3.1 1.3.5a7.8 7.8 0 0 1 1.8-1.1l.2-1.4h3.6l.2 1.4a7.8 7.8 0 0 1 1.8 1.1l1.3-.5 1.8 3.1-1.1.8a7.3 7.3 0 0 1 0 2.8Z" /></>,
  check: <><path d="m5 12.5 4.2 4.2L19 6.8" /><path d="M21 12a9 9 0 1 1-4.2-7.6" /></>,
};

function WorkspaceIcon({ name }: { readonly name: WorkspaceIconName }) {
  return (
    <svg
      aria-hidden="true"
      className="workspace-shell__icon"
      fill="none"
      focusable="false"
      viewBox="0 0 24 24"
    >
      {ICON_PATHS[name]}
    </svg>
  );
}

function getInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return (words.length > 1 ? `${words[0][0]}${words[words.length - 1][0]}` : words[0]?.slice(0, 2) || "A").toUpperCase();
}

function joinClassNames(...classNames: (string | undefined | false)[]): string {
  return classNames.filter(Boolean).join(" ");
}

function focusOnNextFrame(target: () => HTMLElement | null): void {
  const focus = () => target()?.focus();
  if (typeof window.requestAnimationFrame === "function") window.requestAnimationFrame(focus);
  else window.setTimeout(focus, 0);
}

export function WorkspaceShell({
  children,
  cabinet,
  project,
  account,
  routeContract,
  onNavigate,
  onSignOut,
  initialSidebarCollapsed = false,
  className,
}: WorkspaceShellProps) {
  const contract = assertWorkspaceRouteContract(routeContract);
  const location = useLocation();
  const routeResolution = resolveWorkspaceRoute(location.pathname, contract, project.id);
  const activeRoute = routeResolution.activeRoute;
  const routeIsSupported = routeResolution.scope === "current-project" && activeRoute !== undefined;
  const navigation = useMemo(
    () => getWorkspaceNavigation(contract, activeRoute),
    [activeRoute, contract],
  );
  const breadcrumbs = useMemo(
    () => getWorkspaceBreadcrumbs({
      cabinetName: cabinet.name,
      projectName: project.name,
      projectId: project.id,
      activeRoute,
      routeContract: contract,
    }),
    [activeRoute, cabinet.name, contract, project.id, project.name],
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(initialSidebarCollapsed);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const mobileTriggerRef = useRef<HTMLButtonElement>(null);
  const firstNavLinkRef = useRef<HTMLAnchorElement>(null);
  const accountContainerRef = useRef<HTMLDivElement>(null);
  const accountTriggerRef = useRef<HTMLButtonElement>(null);
  const accountItemRefs = useRef<Array<HTMLAnchorElement | HTMLButtonElement | null>>([]);

  const closeMobileNavigation = useCallback(() => {
    setMobileNavOpen(false);
    focusOnNextFrame(() => mobileTriggerRef.current);
  }, []);

  const openMobileNavigation = useCallback(() => {
    setSidebarCollapsed(false);
    setMobileNavOpen(true);
    focusOnNextFrame(() => firstNavLinkRef.current);
  }, []);

  const openAccountMenu = useCallback((focusFirstItem = false) => {
    setAccountMenuOpen(true);
    if (focusFirstItem) {
      focusOnNextFrame(() => accountItemRefs.current[0] ?? null);
    }
  }, []);

  const closeAccountMenu = useCallback((focusTrigger = false) => {
    setAccountMenuOpen(false);
    if (focusTrigger) {
      focusOnNextFrame(() => accountTriggerRef.current);
    }
  }, []);

  useEffect(() => {
    if (!accountMenuOpen) return undefined;

    const handleOutsidePointer = (event: PointerEvent) => {
      if (!accountContainerRef.current?.contains(event.target as Node)) {
        closeAccountMenu();
      }
    };
    document.addEventListener("pointerdown", handleOutsidePointer);
    return () => document.removeEventListener("pointerdown", handleOutsidePointer);
  }, [accountMenuOpen, closeAccountMenu]);

  useEffect(() => {
    setMobileNavOpen(false);
    setAccountMenuOpen(false);
  }, [location.pathname, location.search, location.hash]);

  useEffect(() => {
    const handleGlobalKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      const isEditable = target instanceof HTMLElement
        && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

      if (event.key === "Escape") {
        if (accountMenuOpen) {
          event.preventDefault();
          closeAccountMenu(true);
          return;
        }
        if (mobileNavOpen) {
          event.preventDefault();
          closeMobileNavigation();
          return;
        }
      }

      if (mobileNavOpen && event.key === "Tab") {
        const sidebar = document.getElementById("workspace-shell-navigation");
        const focusable = sidebar?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
        );
        if (!focusable?.length) {
          event.preventDefault();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }

      if (!isEditable && !event.metaKey && !event.ctrlKey && event.key === "[") {
        event.preventDefault();
        setSidebarCollapsed((collapsed) => !collapsed);
      }
    };

    document.addEventListener("keydown", handleGlobalKeyDown);
    return () => document.removeEventListener("keydown", handleGlobalKeyDown);
  }, [accountMenuOpen, closeAccountMenu, closeMobileNavigation, mobileNavOpen]);

  const handleAccountTriggerKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowDown" || event.key === " ") {
      event.preventDefault();
      openAccountMenu(true);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      openAccountMenu(true);
    } else if (event.key === "Escape" && accountMenuOpen) {
      event.preventDefault();
      closeAccountMenu(true);
    }
  };

  const handleAccountMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const currentIndex = accountItemRefs.current.findIndex((item) => item === event.target);
    if (event.key === "Escape") {
      event.preventDefault();
      closeAccountMenu(true);
      return;
    }
    if (currentIndex < 0) return;

    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      const nextIndex = (currentIndex + direction + accountItemRefs.current.length) % accountItemRefs.current.length;
      accountItemRefs.current[nextIndex]?.focus();
    } else if (event.key === "Home") {
      event.preventDefault();
      accountItemRefs.current[0]?.focus();
    } else if (event.key === "End") {
      event.preventDefault();
      accountItemRefs.current[accountItemRefs.current.length - 1]?.focus();
    }
  };

  const handleNavigate = (route: WorkspaceRouteKey) => {
    onNavigate?.(route);
    setAccountMenuOpen(false);
    if (mobileNavOpen) closeMobileNavigation();
  };

  const accountInitials = getInitials(account.displayName);
  const shellClassName = joinClassNames("workspace-shell", className);

  return (
    <div
      className={shellClassName}
      data-mobile-open={mobileNavOpen ? "true" : "false"}
      data-project-id={project.id}
      data-route-state={!routeIsSupported
        ? routeResolution.scope === "other-project" ? "project-mismatch"
          : routeResolution.scope === "project-context-mismatch" ? "project-context-mismatch"
            : routeResolution.scope === "other-module" ? "module-mismatch" : "unsupported"
        : "ready"}
      data-sidebar-collapsed={sidebarCollapsed ? "true" : "false"}
    >
      <a className="workspace-shell__skip-link" href="#workspace-shell-main">
        Skip to workspace
      </a>

      <aside className="workspace-shell__sidebar" id="workspace-shell-navigation" aria-label="Workspace navigation">
        <div className="workspace-shell__sidebar-header">
          <Link
            aria-label={`${project.name} workspace overview`}
            className="workspace-shell__brand"
            onClick={() => handleNavigate("overview")}
            to={contract.overview.href}
          >
            <span aria-hidden="true" className="workspace-shell__brand-mark">A</span>
            <span className="workspace-shell__brand-copy">
              <strong>agentscale</strong>
              <span>workspace</span>
            </span>
          </Link>
          <button
            aria-controls="workspace-shell-navigation"
            aria-expanded={!sidebarCollapsed}
            aria-keyshortcuts="["
            aria-label={sidebarCollapsed ? "Expand navigation" : "Collapse navigation"}
            className="workspace-shell__sidebar-toggle"
            onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
            type="button"
          >
            <span aria-hidden="true">{sidebarCollapsed ? "›" : "‹"}</span>
          </button>
        </div>

        <div className="workspace-shell__context-card">
          <span className="workspace-shell__context-kicker">Cabinet</span>
          <strong className="workspace-shell__context-name">{cabinet.name}</strong>
          <span className="workspace-shell__context-project">
            <span aria-hidden="true" className="workspace-shell__project-dot" />
            <span>{project.name}</span>
          </span>
          <code className="workspace-shell__context-project-id">{project.id}</code>
          {cabinet.description && <span className="workspace-shell__context-description">{cabinet.description}</span>}
          {activeRoute === "module" && contract.module && <span className="workspace-shell__context-module"><span>Module</span><strong>{contract.module.label ?? "Module"}</strong><code>{contract.module.scopeId}</code></span>}
        </div>

        <nav className="workspace-shell__nav" aria-label="Workspace sections">
          {WORKSPACE_NAV_GROUPS.map((group) => (
            <div className="workspace-shell__nav-group" key={group.section}>
              <p className="workspace-shell__nav-heading">{group.label}</p>
              {group.items.map((item) => {
                const route = navigation.find((candidate) => candidate.key === item.key);
                if (!route) return null;
                const isActive = activeRoute === item.key;
                return (
                  <Link
                    aria-current={isActive ? "page" : undefined}
                    aria-label={route.label}
                    className={joinClassNames(
                      "workspace-shell__nav-link",
                      isActive && "workspace-shell__nav-link--active",
                    )}
                    data-active={isActive ? "true" : "false"}
                    key={item.key}
                    onClick={() => handleNavigate(item.key)}
                    ref={item.key === "overview" ? firstNavLinkRef : undefined}
                    title={route.label}
                    to={route.href}
                  >
                    <WorkspaceIcon name={item.icon} />
                    <span className="workspace-shell__nav-label">{route.label}</span>
                    {isActive && <span aria-hidden="true" className="workspace-shell__nav-indicator" />}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="workspace-shell__sidebar-footer">
          <span className="workspace-shell__status-dot" />
          <span className="workspace-shell__status-copy">
            <strong>Workspace online</strong>
            <span>All systems operational</span>
          </span>
        </div>
      </aside>

      {mobileNavOpen && (
        <button
          aria-label="Close navigation"
          className="workspace-shell__backdrop"
          onClick={closeMobileNavigation}
          type="button"
        />
      )}

      <div className="workspace-shell__main-column">
        <header className="workspace-shell__topbar">
          <div className="workspace-shell__topbar-leading">
            <button
              aria-controls="workspace-shell-navigation"
              aria-expanded={mobileNavOpen}
              aria-label={mobileNavOpen ? "Close navigation" : "Open navigation"}
              className="workspace-shell__mobile-trigger"
              onClick={mobileNavOpen ? closeMobileNavigation : openMobileNavigation}
              ref={mobileTriggerRef}
              type="button"
            >
              <span aria-hidden="true" className="workspace-shell__menu-icon"><span /><span /><span /></span>
            </button>
            <div className="workspace-shell__breadcrumb-wrap">
              <nav aria-label="Breadcrumb" className="workspace-shell__breadcrumbs">
                <ol>
                  {breadcrumbs.map((breadcrumb, index) => (
                    <li key={`${breadcrumb.label}-${index}`}>
                      {breadcrumb.href && !breadcrumb.current ? (
                        <Link onClick={() => handleNavigate("overview")} to={breadcrumb.href}>{breadcrumb.label}</Link>
                      ) : (
                        <span aria-current={breadcrumb.current ? "page" : undefined}>{breadcrumb.label}</span>
                      )}
                      {index < breadcrumbs.length - 1 && <span aria-hidden="true" className="workspace-shell__breadcrumb-separator">/</span>}
                    </li>
                  ))}
                </ol>
              </nav>
              <div className="workspace-shell__mobile-context">
                <span>{cabinet.name}</span>
                <span aria-hidden="true">/</span>
                <strong>{project.name}</strong>
                <code className="workspace-shell__mobile-project-id">{project.id}</code>
                {activeRoute === "module" && contract.module && <span className="workspace-shell__mobile-module">{contract.module.label ?? "Module"} · {contract.module.scopeId}</span>}
              </div>
            </div>
          </div>

          <div className="workspace-shell__topbar-actions">
            {project.status && (
              <span className="workspace-shell__project-status" data-status={project.status}>
                <span aria-hidden="true" className="workspace-shell__status-dot" />
                {project.statusLabel ?? project.status}
              </span>
            )}
            <div className="workspace-shell__account" ref={accountContainerRef}>
              <button
                aria-expanded={accountMenuOpen}
                aria-haspopup="menu"
                aria-label={`Open account menu for ${account.displayName}`}
                className="workspace-shell__account-trigger"
                onClick={() => (accountMenuOpen ? closeAccountMenu() : openAccountMenu())}
                onKeyDown={handleAccountTriggerKeyDown}
                ref={accountTriggerRef}
                type="button"
              >
                {account.avatarUrl ? (
                  <img alt="" className="workspace-shell__avatar" src={account.avatarUrl} />
                ) : (
                  <span aria-hidden="true" className="workspace-shell__avatar workspace-shell__avatar--initials">{accountInitials}</span>
                )}
                <span className="workspace-shell__account-copy">
                  <strong>{account.displayName}</strong>
                  <span>{account.role ?? account.email ?? "Account"}</span>
                </span>
                <span aria-hidden="true" className="workspace-shell__account-chevron">⌄</span>
              </button>
              {accountMenuOpen && (
                <div
                  aria-label="Account actions"
                  className="workspace-shell__account-menu"
                  onKeyDown={handleAccountMenuKeyDown}
                  role="menu"
                >
                  <div className="workspace-shell__account-menu-header">
                    <strong>{account.displayName}</strong>
                    {account.email && <span>{account.email}</span>}
                  </div>
                  <Link
                    className="workspace-shell__account-menu-item"
                    onClick={() => handleNavigate("settings")}
                    ref={(node) => { accountItemRefs.current[0] = node; }}
                    role="menuitem"
                    to={contract.settings.href}
                  >
                    <WorkspaceIcon name="settings" />
                    Project settings
                  </Link>
                  <button
                    className="workspace-shell__account-menu-item workspace-shell__account-menu-item--danger"
                    onClick={() => {
                      closeAccountMenu();
                      void onSignOut();
                    }}
                    ref={(node) => { accountItemRefs.current[1] = node; }}
                    role="menuitem"
                    type="button"
                  >
                    <span aria-hidden="true" className="workspace-shell__signout-icon">↪</span>
                    Sign out
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>

        <main className="workspace-shell__main" id="workspace-shell-main" tabIndex={-1}>
          {!routeIsSupported ? (
            <section className="workspace-shell__route-recovery" role="alert">
              <p className="workspace-shell__route-eyebrow">{routeResolution.scope === "other-project" ? "Project scope mismatch" : routeResolution.scope === "project-context-mismatch" ? "Project context mismatch" : routeResolution.scope === "other-module" ? "Module scope mismatch" : "Unsupported workspace route"}</p>
              <h1>{routeResolution.scope === "other-project" ? "This workspace belongs to a different project" : routeResolution.scope === "project-context-mismatch" ? "The selected project does not match this route" : routeResolution.scope === "other-module" ? "This URL requests a different module" : "This route is not available in this workspace"}</h1>
              <p>
                {routeResolution.scope === "other-project"
                  ? `This shell is scoped to ${project.name} (${project.id}); the URL requests project ${routeResolution.requestedProjectId ?? "unknown"}. Its content was not loaded.`
                  : routeResolution.scope === "project-context-mismatch"
                    ? `The route is scoped to project ${routeResolution.requestedProjectId ?? "unknown"}, but the selected project context is ${routeResolution.contextProjectId ?? "unknown"}. No project content was loaded.`
                    : routeResolution.scope === "other-module"
                    ? `This workspace is scoped to module ${contract.module?.scopeId ?? "unknown"}; the URL requests module ${routeResolution.requestedModuleId ?? "unknown"}. Its content was not loaded.`
                    : `No confirmed workspace destination matches ${location.pathname}. The URL was kept so you can recover without losing the deep link.`}
              </p>
              <div className="workspace-shell__route-actions">
                {routeResolution.scope === "project-context-mismatch" ? <Link to="/projects">Projects</Link> : (
                  <Link onClick={() => handleNavigate("overview")} to={contract.overview.href}>Open {project.name}</Link>
                )}
              </div>
            </section>
          ) : children}
        </main>
      </div>
    </div>
  );
}
