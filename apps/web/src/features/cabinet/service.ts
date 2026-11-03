import type { Cabinet, ProjectSummary } from "../../api/types";
import type {
  CabinetChatSidebar,
  CabinetModule,
  CabinetOverviewSection,
  CabinetOverviewService,
  CabinetOverviewServiceOptions,
  CabinetOverviewSnapshot,
  CabinetPresenceHeartbeat,
  CabinetProjectSelection,
} from "./contracts";

const projectStatuses = new Set(["draft", "active", "paused", "error", "completed"]);
const navigationPlacements = new Set(["management", "data"]);

export class CabinetOverviewRequestError extends Error {
  readonly status?: number;
  readonly code?: string;

  constructor(message: string, details: { status?: number; code?: string } = {}) {
    super(message);
    this.name = "CabinetOverviewRequestError";
    this.status = details.status;
    this.code = details.code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function mapCabinet(raw: unknown): Cabinet {
  if (!isRecord(raw) || typeof raw.id !== "string" || typeof raw.name !== "string") {
    throw new CabinetOverviewRequestError("Сервер вернул неполные данные кабинета.");
  }
  return raw as unknown as Cabinet;
}

function mapProject(raw: unknown): ProjectSummary {
  if (!isRecord(raw) || typeof raw.id !== "string" || typeof raw.cabinet_id !== "string" || typeof raw.name !== "string") {
    throw new CabinetOverviewRequestError("Сервер вернул неполные данные проекта кабинета.");
  }
  if (typeof raw.status !== "string" || !projectStatuses.has(raw.status)) {
    throw new CabinetOverviewRequestError("Сервер вернул неизвестный статус проекта.");
  }
  return raw as unknown as ProjectSummary;
}

function mapModules(raw: unknown): CabinetModule[] {
  if (!isRecord(raw) || !Array.isArray(raw.items)) {
    throw new CabinetOverviewRequestError("Сервер вернул некорректный список модулей кабинета.");
  }
  return raw.items.flatMap((item) => {
    if (!isRecord(item) || typeof item.id !== "string" || typeof item.name !== "string") return [];
    return [{ id: item.id, name: item.name, ...(typeof item.status === "string" ? { status: item.status } : {}), ...(typeof item.bind_kind === "string" ? { bind_kind: item.bind_kind } : {}) }];
  });
}

function mapSelection(raw: unknown): CabinetProjectSelection {
  if (!isRecord(raw)) throw new CabinetOverviewRequestError("Сервер вернул некорректный выбор проекта.");
  if (raw.project_id != null && typeof raw.project_id !== "string") throw new CabinetOverviewRequestError("Сервер вернул некорректный идентификатор проекта.");
  if (raw.chat_session_id != null && typeof raw.chat_session_id !== "string") throw new CabinetOverviewRequestError("Сервер вернул некорректный идентификатор чата.");
  return {
    ...(typeof raw.project_id === "string" ? { project_id: raw.project_id } : {}),
    ...(typeof raw.chat_session_id === "string" ? { chat_session_id: raw.chat_session_id } : {}),
  };
}

function mapSidebar(raw: unknown): CabinetChatSidebar {
  if (!isRecord(raw)) throw new CabinetOverviewRequestError("Сервер вернул некорректную боковую панель чатов.");
  const readChat = (value: unknown) => {
    if (!isRecord(value) || typeof value.session_id !== "string" || typeof value.project_id !== "string") return undefined;
    return { session_id: value.session_id, project_id: value.project_id };
  };
  const projects = Array.isArray(raw.projects) ? raw.projects.flatMap((value) => {
    if (!isRecord(value) || typeof value.project_id !== "string") return [];
    const chats = Array.isArray(value.chats) ? value.chats.map(readChat).filter((chat): chat is NonNullable<typeof chat> => chat !== undefined) : [];
    return [{ project_id: value.project_id, chats }];
  }) : [];
  const pinned = Array.isArray(raw.pinned) ? raw.pinned.map(readChat).filter((chat): chat is NonNullable<typeof chat> => chat !== undefined) : [];
  const legacyChats = Array.isArray(raw.project_chats) ? raw.project_chats.map(readChat).filter((chat): chat is NonNullable<typeof chat> => chat !== undefined) : [];
  return { projects, pinned: [...pinned, ...legacyChats] };
}

function mapMetrics(raw: unknown): Record<string, unknown> {
  if (!isRecord(raw)) throw new CabinetOverviewRequestError("Сервер вернул некорректные метрики кабинета.");
  return raw;
}

function isAbort(error: unknown, signal?: AbortSignal): boolean {
  return signal?.aborted === true || (error instanceof Error && error.name === "AbortError");
}

export function createCabinetOverviewService(options: CabinetOverviewServiceOptions = {}): CabinetOverviewService {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
  const fetcher = options.fetcher ?? fetch;

  async function request<T>(path: string, signal?: AbortSignal, init: RequestInit = {}): Promise<T> {
    let response: Response;
    try {
      const token = options.getAccessToken?.();
      response = await fetcher(`${baseUrl}${path}`, {
        ...init,
        signal,
        headers: { Accept: "application/json", ...(token ? { Authorization: "Bearer " + token } : {}), ...init.headers },
      });
    } catch (error) {
      if (isAbort(error, signal)) throw error;
      throw new CabinetOverviewRequestError(error instanceof Error ? `Не удалось связаться с API: ${error.message}` : "Не удалось связаться с API.");
    }
    if (!response.ok) {
      let message = response.statusText || `Запрос завершился с кодом ${response.status}.`;
      let code: string | undefined;
      try {
        const body: unknown = await response.json();
        if (isRecord(body)) {
          message = typeof body.detail === "string" ? body.detail : typeof body.title === "string" ? body.title : message;
          code = typeof body.code === "string" ? body.code : undefined;
        }
      } catch { /* Keep the HTTP fallback. */ }
      throw new CabinetOverviewRequestError(message, { status: response.status, code });
    }
    if (response.status === 204) return undefined as T;
    try {
      return await response.json() as T;
    } catch {
      throw new CabinetOverviewRequestError("Сервер вернул некорректный JSON.", { status: response.status });
    }
  }

  async function section<T>(
    errors: CabinetOverviewSnapshot["errors"],
    name: CabinetOverviewSection,
    load: () => Promise<T>,
  ): Promise<T | undefined> {
    try {
      return await load();
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") throw error;
      errors[name] = error instanceof Error ? error : new Error("Не удалось загрузить раздел кабинета.");
      return undefined;
    }
  }

  return {
    async load(cabinetId, requestOptions = {}) {
      const signal = requestOptions.signal;
      const id = encodeURIComponent(cabinetId);
      const errors: CabinetOverviewSnapshot["errors"] = {};
      const snapshot: CabinetOverviewSnapshot = { errors };
      const presence = await section(errors, "presence", () => request<CabinetPresenceHeartbeat>(`/cabinets/${id}/presence/heartbeat`, signal, { method: "POST" }));
      if (presence !== undefined) snapshot.presence = presence;
      const [cabinets, cabinet, projects, metrics, selection, sidebar, modules] = await Promise.all([
        section(errors, "cabinets", async () => {
          const result = await request<{ items?: unknown[] }>("/cabinets", signal);
          if (!Array.isArray(result.items)) throw new CabinetOverviewRequestError("Сервер вернул некорректный список кабинетов.");
          return result.items.map(mapCabinet);
        }),
        section(errors, "cabinet", async () => mapCabinet(await request(`/cabinets/${id}`, signal))),
        section(errors, "projects", async () => {
          const result = await request<{ items?: unknown[] }>(`/cabinets/${id}/projects`, signal);
          if (!Array.isArray(result.items)) throw new CabinetOverviewRequestError("Сервер вернул некорректный список проектов.");
          return result.items.map(mapProject);
        }),
        section(errors, "metrics", async () => mapMetrics(await request(`/cabinets/${id}/metrics`, signal))),
        section(errors, "selection", async () => mapSelection(await request(`/cabinets/${id}/me/selection`, signal))),
        section(errors, "sidebar", async () => mapSidebar(await request(`/cabinets/${id}/chats/sidebar`, signal))),
        section(errors, "modules", async () => mapModules(await request(`/cabinets/${id}/modules`, signal))),
      ]);
      if (signal?.aborted) {
        const abortError = new Error("The operation was aborted.");
        abortError.name = "AbortError";
        throw abortError;
      }
      if (cabinets !== undefined) snapshot.cabinets = cabinets;
      if (cabinet !== undefined) snapshot.cabinet = cabinet;
      if (projects !== undefined) snapshot.projects = projects;
      if (metrics !== undefined) snapshot.metrics = metrics;
      if (selection !== undefined) snapshot.selection = selection;
      if (sidebar !== undefined) snapshot.sidebar = sidebar;
      if (modules !== undefined) {
        snapshot.cabinetModules = modules;
        const selectedProjectId = snapshot.selection?.project_id;
        const selectedProject = snapshot.projects?.find((project) => project.id === selectedProjectId);
        if (!selectedProject) {
          snapshot.modules = [];
          snapshot.moduleTabs = {};
          return snapshot;
        }
        let projectModules: CabinetModule[];
        try {
          const result = await request<{ items?: unknown[] }>(`/projects/${encodeURIComponent(selectedProject.id)}/runtime-modules`, signal);
          if (!Array.isArray(result.items)) throw new CabinetOverviewRequestError("Сервер вернул некорректные модули проекта.");
          projectModules = result.items.flatMap((item) => {
            if (!isRecord(item)) return [];
            const id = typeof item.id === "string" ? item.id : typeof item.module_id === "string" ? item.module_id : undefined;
            const name = typeof item.name === "string" ? item.name : id;
            if (!id || !name) return [];
            return [{ id, name, ...(typeof item.status === "string" ? { status: item.status } : {}), ...(typeof item.bind_kind === "string" ? { bind_kind: item.bind_kind } : {}) }];
          });
        } catch (error) {
          if (isAbort(error, signal)) throw error;
          errors.modules = error instanceof Error ? error : new Error("Не удалось загрузить модули проекта.");
          snapshot.modules = [];
          snapshot.moduleTabs = {};
          return snapshot;
        }
        snapshot.modules = projectModules;
        const results = await Promise.all(projectModules.map(async (module) => {
          try {
            const document = await request<unknown>(`/projects/${encodeURIComponent(selectedProject.id)}/runtime-modules/${encodeURIComponent(module.id)}/meta/documents/tabs`, signal);
            if (!isRecord(document)) return [module.id, []] as const;
            const body = document.body;
            const items = isRecord(body) && Array.isArray(body.items) ? body.items : Array.isArray(body) ? body : [];
            const tabs = items.filter((tab): tab is Record<string, unknown> => {
              if (!isRecord(tab) || tab.enabled === false) return false;
              const nav = tab.nav;
              const contour = isRecord(nav) && typeof nav.contour === "string" ? nav.contour : "employee";
              const placement = isRecord(nav) && typeof nav.placement === "string" ? nav.placement : "management";
              return contour === "employee" && navigationPlacements.has(placement) && typeof tab.view_slug === "string";
            });
            return [module.id, tabs] as const;
          } catch (error) {
            if (isAbort(error, signal)) throw error;
            errors.modules = error instanceof Error ? error : new Error("Не удалось загрузить навигацию модулей.");
            return [module.id, []] as const;
          }
        }));
        snapshot.moduleTabs = Object.fromEntries(results);
      }
      return snapshot;
    },
  };
}
