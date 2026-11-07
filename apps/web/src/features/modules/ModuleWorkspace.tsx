import { useEffect, useMemo, useState } from "react";
import type { Translate } from "../../i18n/catalogue";
import { useI18n } from "../../i18n/useT";
import type { ModuleManifest, ModuleRecord, ModuleRuntimeScope, ModuleRuntimeService, ModuleSlotRegistry, ModuleViewDefinition } from "./contracts";
import { labelText } from "./manifest";
import { bindingLabel } from "./binding";
import { createDefaultModuleRuntimeService } from "./service";
import { ModuleRuntime } from "./ModuleRuntime";
import "./modules.css";

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** `local`/`global` is the data model's word, not the reader's. */
function bindLabel(t: Translate, kind: string): string {
  return bindingLabel(t, kind);
}

function navViews(modules: readonly ModuleRecord[], manifests: Readonly<Record<string, ModuleManifest>>, scopes: Readonly<Record<string, ModuleRuntimeScope>>) {
  return modules.flatMap((module) => {
    const manifest = manifests[module.id];
    const scope = scopes[module.id];
    if (!manifest || !scope) return [];
    return manifest.tabs.flatMap((tab) => {
      if (tab.enabled === false || tab.nav?.contour === "admin" || tab.nav?.contour === "company" || (tab.nav?.placement !== "management" && tab.nav?.placement !== "data")) return [];
      const view = manifest.views.find((item) => item.slug === tab.view_slug);
      if (!view || view.enabled === false) return [];
      if (view.kind === "hub" && Array.isArray(view.ui_json.items) && view.ui_json.items.every((item) => record(item) && record(item.scope) && item.scope.active_chat === "required") && !scope.sessionId) return [];
      return [{ module, manifest, view, tab }];
    });
  });
}

const defaultWorkspaceService = createDefaultModuleRuntimeService();

export function ModuleWorkspace({
  projectId,
  sessionId,
  service = defaultWorkspaceService,
  initialModuleId,
  initialViewSlug,
  onBack,
  onOpenProject,
  slots,
}: {
  readonly projectId: string;
  readonly sessionId?: string;
  readonly service?: ModuleRuntimeService;
  readonly initialModuleId?: string;
  readonly initialViewSlug?: string;
  readonly onBack?: () => void;
  readonly onOpenProject?: (projectId: string) => void;
  readonly slots?: ModuleSlotRegistry;
}) {
  const { t } = useI18n();
  const [modules, setModules] = useState<readonly ModuleRecord[]>([]);
  const [manifests, setManifests] = useState<Record<string, ModuleManifest>>({});
  const [scopes, setScopes] = useState<Record<string, ModuleRuntimeScope>>({});
  const [moduleErrors, setModuleErrors] = useState<Record<string, string>>({});
  const [moduleId, setModuleId] = useState(initialModuleId ?? "");
  const [viewSlug, setViewSlug] = useState(initialViewSlug ?? "");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [epoch, setEpoch] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(undefined);
    void service.listProjectModules(projectId, { signal: controller.signal }).then(async (items) => {
      const loaded = await Promise.allSettled(items.map((module) => service.loadModule(projectId, module.id, sessionId, { signal: controller.signal })));
      if (controller.signal.aborted) return;
      const manifestMap: Record<string, ModuleManifest> = {};
      const scopeMap: Record<string, ModuleRuntimeScope> = {};
      const errorMap: Record<string, string> = {};
      loaded.forEach((result, index) => {
        if (result.status === "fulfilled") { manifestMap[items[index].id] = result.value.manifest; scopeMap[items[index].id] = result.value.scope; }
        else errorMap[items[index].id] = result.reason instanceof Error ? result.reason.message : t("modules.invalidTitle");
      });
      const validModules = items.filter((item) => manifestMap[item.id] !== undefined);
      const chosen = validModules.find((item) => item.id === (initialModuleId ?? moduleId)) ?? validModules[0];
      setModules(items); setManifests(manifestMap); setScopes(scopeMap); setModuleErrors(errorMap);
      if (chosen) {
        setModuleId(chosen.id);
        const selectedView = initialViewSlug && manifestMap[chosen.id].views.some((view) => view.slug === initialViewSlug) ? initialViewSlug : undefined;
        setViewSlug(selectedView ?? manifestMap[chosen.id].tabs.find((tab) => tab.enabled !== false && tab.nav?.contour !== "admin" && tab.nav?.contour !== "company")?.view_slug ?? manifestMap[chosen.id].views.find((view) => view.enabled !== false)?.slug ?? "");
      }
      const invalidCount = loaded.filter((result) => result.status === "rejected").length;
      if (invalidCount) setError(t("modules.modulesFailed"));
      setLoading(false);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) { setError(cause instanceof Error ? cause.message : t("modules.backendNoData")); setLoading(false); }
    });
    return () => controller.abort();
  }, [epoch, initialModuleId, projectId, service, sessionId, t]);

  const active = modules.find((item) => item.id === moduleId);
  const manifest = manifests[moduleId];
  const scope = scopes[moduleId];
  const initialRuntime = useMemo(() => manifest && scope ? { scope, manifest } : undefined, [manifest, scope]);
  const destinations = useMemo(() => navViews(modules, manifests, scopes), [manifests, modules, scopes]);
  if (loading) return <main className="module-runtime"><div className="module-runtime-shell"><div className="module-runtime-loading" role="status">{t("modules.loadingWorkspace")}</div></div></main>;
  return <main className="module-runtime"><div className="module-runtime-shell">
    <header className="module-runtime-header">
      <div>
        <p className="module-runtime-eyebrow">{t("modules.eyebrow")}</p>
        <h1>{t("modules.title")}</h1>
        <p className="module-runtime-muted">{t("modules.subtitle")}</p>
      </div>
      <div className="module-runtime-header-actions">
        {onBack && <button type="button" className="module-runtime-button" onClick={onBack}>{t("modules.back")}</button>}
        {onOpenProject && <button type="button" className="module-runtime-button" onClick={() => onOpenProject(projectId)}>{t("modules.openProject")}</button>}
      </div>
    </header>
    {error && <div className="module-runtime-error" role="alert">{error}<button type="button" className="module-runtime-button module-runtime-button--small" disabled={loading} onClick={() => setEpoch((value) => value + 1)}>{t("modules.retryLoad")}</button></div>}
    {modules.length === 0 && !error && <div className="module-runtime-empty"><strong>{t("modules.emptyTitle")}</strong><span>{t("modules.emptyHint")}</span></div>}
    {modules.length > 0 && <>
      <nav className="module-runtime-project-nav" aria-label={t("modules.navAria")}>{modules.map((module) => <button type="button" key={module.id} aria-current={module.id === moduleId ? "page" : undefined} onClick={() => { setModuleId(module.id); setViewSlug(manifests[module.id]?.tabs.find((tab) => tab.enabled !== false && tab.nav?.contour !== "admin" && tab.nav?.contour !== "company")?.view_slug ?? ""); }}><span>{module.name}</span><small>{bindLabel(t, module.bind_kind)} · {module.child_may_edit ? t("modules.canEdit") : t("modules.readOnly")}</small></button>)}</nav>
      {moduleErrors[moduleId] && <div className="module-runtime-invalid" role="alert">
        <strong>{active?.name ?? t("modules.moduleUnknown")}</strong>
        <p>{moduleErrors[moduleId]}</p>
        <small>{t("modules.invalidRepair")}</small>
        <button type="button" className="module-runtime-button module-runtime-button--small" disabled={loading} onClick={() => setEpoch((value) => value + 1)}>{t("modules.retryLoad")}</button>
      </div>}
      {manifest && scope && active && <>
        <nav className="module-runtime-nav" aria-label={t("modules.viewsAria")}>{destinations.filter((item) => item.module.id === moduleId).map(({ tab, view }) => <button key={tab.id} type="button" aria-current={view.slug === viewSlug ? "page" : undefined} onClick={() => setViewSlug(view.slug)}><span>{labelText(tab.title) || t("modules.untitled")}</span><small>{tab.nav?.placement === "data" ? t("modules.placementData") : t("modules.placementManage")}</small></button>)}{manifest.views.filter((view) => view.enabled !== false && !manifest.tabs.some((tab) => tab.view_slug === view.slug)).map((view) => <button key={view.slug} type="button" aria-current={view.slug === viewSlug ? "page" : undefined} onClick={() => setViewSlug(view.slug)}><span>{labelText(view.label) || t("modules.untitled")}</span><small>{view.kind === "collection" ? t("modules.kindCollection") : view.kind === "form" ? t("modules.kindForm") : view.kind === "detail" ? t("modules.kindDetail") : view.kind === "hub" ? t("modules.kindHub") : view.kind === "profile_hub" ? t("modules.kindProfileHub") : t("modules.kindDefault")}</small></button>)}</nav>
        <ModuleRuntime key={`${projectId}:${moduleId}:${sessionId ?? ""}`} projectId={projectId} moduleId={moduleId} sessionId={sessionId} service={service} initialRuntime={initialRuntime} initialViewSlug={viewSlug || undefined} showNavigation={false} onBack={onBack} onOpenProject={onOpenProject} slots={slots}/>
      </>}
      {!active && moduleId && <div className="module-runtime-invalid" role="alert">
        <strong>{t("modules.moduleNotBoundTitle")}</strong>
        <p>{t("modules.moduleNotBoundName", { name: moduleId })}</p>
        <small>{t("modules.moduleNotBoundHint")}</small>
      </div>}
    </>}
  </div></main>;
}
