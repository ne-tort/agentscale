import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useAuth } from "../../app/context";
import type { ModuleManifest, ModuleRecord, ModuleRuntimeScope, ModuleRuntimeService, ModuleSlotRegistry, ModuleViewDefinition } from "./contracts";
import { labelText } from "./manifest";
import { createDefaultModuleRuntimeService } from "./service";
import { ModuleRuntime } from "./ModuleRuntime";
import "./modules.css";

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

function SectionTitle({ title, detail, action }: { title: string; detail?: string; action?: ReactNode }) {
  return <header className="module-runtime-section-heading"><div><p className="module-runtime-eyebrow">Project modules</p><h2>{title}</h2>{detail && <p>{detail}</p>}</div>{action}</header>;
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
        else errorMap[items[index].id] = result.reason instanceof Error ? result.reason.message : "Module manifest is invalid or inaccessible.";
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
      if (invalidCount) setError(`${invalidCount} module(s) were not loaded. Open a module to see its repair hint.`);
      setLoading(false);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) { setError(cause instanceof Error ? cause.message : "Project modules could not be loaded."); setLoading(false); }
    });
    return () => controller.abort();
  }, [epoch, initialModuleId, projectId, service, sessionId]);

  const active = modules.find((item) => item.id === moduleId);
  const manifest = manifests[moduleId];
  const scope = scopes[moduleId];
  const initialRuntime = useMemo(() => manifest && scope ? { scope, manifest } : undefined, [manifest, scope]);
  const destinations = useMemo(() => navViews(modules, manifests, scopes), [manifests, modules, scopes]);
  if (loading) return <main className="module-runtime"><div className="module-runtime-shell"><div className="module-runtime-loading" role="status">Загружаем модули проекта и проверяем их manifests…</div></div></main>;
  return <main className="module-runtime"><div className="module-runtime-shell">
    <header className="module-runtime-header"><div><p className="module-runtime-eyebrow">Project / {projectId}</p><h1>Модули проекта</h1><p className="module-runtime-muted">Локальные и глобальные привязки; данные и действия проверяются backend.</p></div><div className="module-runtime-header-actions">{onBack && <button type="button" className="module-runtime-button" onClick={onBack}>Назад</button>}{onOpenProject && <button type="button" className="module-runtime-button" onClick={() => onOpenProject(projectId)}>Проект</button>}</div></header>
    {error && <div className="module-runtime-error" role="alert">{error}<button type="button" className="module-runtime-button module-runtime-button--small" disabled={loading} onClick={() => setEpoch((value) => value + 1)}>Повторить загрузку</button></div>}
    {modules.length === 0 && !error && <div className="module-runtime-empty"><strong>Нет привязанных модулей</strong><span>Подключите модуль к кабинету и проекту.</span></div>}
    {modules.length > 0 && <>
      <nav className="module-runtime-project-nav" aria-label="Project modules">{modules.map((module) => <button type="button" key={module.id} aria-current={module.id === moduleId ? "page" : undefined} onClick={() => { setModuleId(module.id); setViewSlug(manifests[module.id]?.tabs.find((tab) => tab.enabled !== false && tab.nav?.contour !== "admin" && tab.nav?.contour !== "company")?.view_slug ?? ""); }}><span>{module.name}</span><small>{module.bind_kind}{module.child_may_edit ? " · edit" : " · read-only"}</small></button>)}</nav>
      {moduleErrors[moduleId] && <div className="module-runtime-invalid" role="alert"><strong>{active?.name ?? moduleId}</strong><p>{moduleErrors[moduleId]}</p><small>Проверьте runtime binding и исправьте manifest: поддерживаются только allowlisted declarative view/widget/action types.</small><button type="button" className="module-runtime-button module-runtime-button--small" disabled={loading} onClick={() => setEpoch((value) => value + 1)}>Повторить загрузку</button></div>}
      {manifest && scope && active && <>
        <nav className="module-runtime-nav" aria-label="Module views">{destinations.filter((item) => item.module.id === moduleId).map(({ tab, view }) => <button key={tab.id} type="button" aria-current={view.slug === viewSlug ? "page" : undefined} onClick={() => setViewSlug(view.slug)}><span>{labelText(tab.title) || view.slug}</span><small>{tab.nav?.placement === "data" ? "Данные" : "Управление"}</small></button>)}{manifest.views.filter((view) => view.enabled !== false && !manifest.tabs.some((tab) => tab.view_slug === view.slug)).map((view) => <button key={view.slug} type="button" aria-current={view.slug === viewSlug ? "page" : undefined} onClick={() => setViewSlug(view.slug)}><span>{labelText(view.label) || view.slug}</span><small>{view.kind}</small></button>)}</nav>
        <ModuleRuntime key={`${projectId}:${moduleId}:${sessionId ?? ""}`} projectId={projectId} moduleId={moduleId} sessionId={sessionId} service={service} initialRuntime={initialRuntime} initialViewSlug={viewSlug || undefined} showNavigation={false} onBack={onBack} onOpenProject={onOpenProject} slots={slots}/>
      </>}
      {!active && moduleId && <div className="module-runtime-invalid" role="alert"><strong>Модуль не входит в список привязанных к проекту</strong><p>{moduleId}</p><small>Откройте проект, в котором сервер подтвердил привязку модуля.</small></div>}
    </>}
  </div></main>;
}
