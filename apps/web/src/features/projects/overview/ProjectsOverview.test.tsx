// @vitest-environment jsdom
import { act } from "react";
import type { ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectsOverview } from "./ProjectsOverview";
import type { ProjectOverviewService } from "./service";
import type { ProjectSummary } from "../../../api/types";

const project: ProjectSummary = {
  id: "p-1", cabinet_id: "cab-1", name: "Market study", status: "draft", selected: false, new_chat_enabled: false,
};

function makeService(overrides: Partial<ProjectOverviewService> = {}): ProjectOverviewService {
  return {
    listCabinets: vi.fn(async () => [{ id: "cab-1", name: "Operations" }]),
    listProjects: vi.fn(async () => []),
    getProject: vi.fn(async () => project),
    getSelection: vi.fn(async () => undefined),
    createProject: vi.fn(async () => project),
    selectProject: vi.fn(async (_cabinetId: string, projectId: string | null) => projectId ?? undefined),
    lifecycle: vi.fn(async () => ({ ...project, status: "active" as const })),
    ...overrides,
  };
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;

async function mount(element: ReactNode): Promise<void> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root?.render(element); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

async function click(selector: string): Promise<void> {
  await act(async () => {
    const target = document.querySelector<HTMLElement>(selector);
    if (!target) throw new Error(`Missing element: ${selector}`);
    target.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function QueryEcho() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}{location.search}</output>;
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  window.localStorage.clear();
});

const service: ProjectOverviewService = {
  listCabinets: async () => [],
  listProjects: async () => [],
  getProject: async () => { throw new Error("Not used by this render test"); },
  getSelection: async () => undefined,
  createProject: async () => { throw new Error("Not used by this render test"); },
  selectProject: async () => { throw new Error("Not used by this render test"); },
  lifecycle: async () => { throw new Error("Not used by this render test"); },
};

describe("ProjectsOverview acceptance surface", () => {
  it("starts with an explicit loading state and requires a real service contract", () => {
    const markup = renderToStaticMarkup(<MemoryRouter><ProjectsOverview service={service} employeeId="employee-1" /></MemoryRouter>);
    expect(markup).toContain("Проекты");
    expect(markup).toContain("Загрузка кабинетов");
    expect(markup).not.toContain("Open placeholder project");
    expect(markup).not.toContain("disabled title=");
  });

  it("handles no cabinets as a stable empty state without project requests", async () => {
    const api = makeService({ listCabinets: vi.fn(async () => []) });
    await mount(<MemoryRouter><ProjectsOverview service={api} employeeId="employee-1" /></MemoryRouter>);
    expect(document.body.textContent).toContain("Нет доступных кабинетов");
    expect(api.listProjects).not.toHaveBeenCalled();
  });

  it("loads an empty cabinet and keeps the create workflow available", async () => {
    const api = makeService();
    await mount(<MemoryRouter><ProjectsOverview service={api} employeeId="employee-1" /></MemoryRouter>);
    expect(document.body.textContent).toContain("В кабинете пока нет проектов");
    expect(document.querySelector("button")?.textContent).not.toBe("Нет доступных кабинетов");
    expect(document.body.textContent).toContain("Создать первый проект");
  });

  it("retries cabinet loading after an error and removes stale project rows", async () => {
    const listCabinets = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce([{ id: "cab-1", name: "Operations" }]);
    const api = makeService({ listCabinets, listProjects: vi.fn(async () => [project]) });
    await mount(<MemoryRouter><ProjectsOverview service={api} employeeId="employee-1" /></MemoryRouter>);
    expect(document.body.textContent).toContain("offline");
    await click(".projects-feedback--error button");
    expect(document.body.textContent).toContain("Market study");
    expect(listCabinets).toHaveBeenCalledTimes(2);
  });

  it("retries a failed project list without retaining stale rows", async () => {
    const listProjects = vi.fn().mockRejectedValueOnce(new Error("list unavailable")).mockResolvedValueOnce([project]);
    const api = makeService({ listProjects });
    await mount(<MemoryRouter><ProjectsOverview service={api} employeeId="employee-1" /></MemoryRouter>);
    expect(document.body.textContent).toContain("list unavailable");
    expect(document.body.textContent).not.toContain("Market study");
    await click(".projects-feedback--error button");
    expect(document.body.textContent).toContain("Market study");
    expect(listProjects).toHaveBeenCalledTimes(2);
  });

  it("creates only after list and GET readback and keeps the project visible", async () => {
    const calls: string[] = [];
    const api = makeService({
      listProjects: vi.fn(async () => { calls.push("list"); return [project]; }),
      createProject: vi.fn(async () => { calls.push("create"); return project; }),
      getProject: vi.fn(async () => { calls.push("get"); return project; }),
      getSelection: vi.fn(async () => { calls.push("selection"); return "p-1"; }),
      selectProject: vi.fn(async (_cabinetId: string, projectId: string | null) => { calls.push("select"); return projectId ?? undefined; }),
    });
    await mount(<MemoryRouter><ProjectsOverview service={api} employeeId="employee-1" /></MemoryRouter>);
    calls.length = 0;
    await click(".projects-primary");
    const input = document.querySelector<HTMLInputElement>(".projects-create-form input");
    if (!input) throw new Error("Missing project name input");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "Market study");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await click(".projects-create-form button[type='submit']");
    expect(api.createProject).toHaveBeenCalledWith("cab-1", "Market study");
    expect(api.selectProject).toHaveBeenCalledWith("cab-1", "p-1");
    expect(api.getProject).toHaveBeenCalledWith("p-1");
    expect(calls).toEqual(["create", "list", "get", "select", "list", "selection", "get"]);
    expect(document.body.textContent).toContain("создан и выбран");
    expect(document.querySelector(".projects-name")?.textContent).toBe("Market study");
  });

  it("does not claim create success when the project readback mismatches", async () => {
    const api = makeService({
      listProjects: vi.fn(async () => [{ ...project, name: "Other name" }]),
      getProject: vi.fn(async () => ({ ...project, name: "Other name" })),
      getSelection: vi.fn(async () => "p-1"),
    });
    await mount(<MemoryRouter><ProjectsOverview service={api} employeeId="employee-1" /></MemoryRouter>);
    await click(".projects-primary");
    const input = document.querySelector<HTMLInputElement>(".projects-create-form input");
    if (!input) throw new Error("Missing project name input");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "Market study");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await click(".projects-create-form button[type='submit']");
    expect(document.body.textContent).toContain("Ответ создания не совпадает");
    expect(document.body.textContent).not.toContain("создан и выбран");
  });

  it("does not claim create success when the create route returns a different name", async () => {
    const api = makeService({ createProject: vi.fn(async () => ({ ...project, name: "Different name" })) });
    await mount(<MemoryRouter><ProjectsOverview service={api} employeeId="employee-1" /></MemoryRouter>);
    await click(".projects-primary");
    const input = document.querySelector<HTMLInputElement>(".projects-create-form input");
    if (!input) throw new Error("Missing project name input");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "Market study");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await click(".projects-create-form button[type='submit']");
    expect(document.body.textContent).toContain("Ответ создания не совпадает");
    expect(document.body.textContent).not.toContain("создан и выбран");
  });

  it("does not confirm a lifecycle action when the route DTO and project/list readback disagree", async () => {
    const running = { ...project, status: "active" as const, observed_state: "running" };
    const api = makeService({
      listProjects: vi.fn(async () => [running]),
      lifecycle: vi.fn(async () => ({ ...running, status: "paused" as const })),
      getProject: vi.fn(async () => ({ ...running, status: "paused" as const })),
    });
    await mount(<MemoryRouter><ProjectsOverview service={api} employeeId="employee-1" /></MemoryRouter>);
    await click(".projects-action");
    expect(document.body.textContent).toContain("списка и проекта не подтверждает одинаковый статус");
    expect(document.body.textContent).not.toContain("подтверждено backend");
  });

  it("keeps a cabinet deep link across reload and links each project to workspace, chat, and files", async () => {
    const api = makeService();
    await mount(<MemoryRouter initialEntries={["/projects?cabinet=cab-1"]}>
      <Routes><Route path="/projects" element={<><ProjectsOverview service={api} employeeId="employee-1" /><QueryEcho /></>} /></Routes>
    </MemoryRouter>);
    expect(document.querySelector("[data-testid='location']")?.textContent).toBe("/projects?cabinet=cab-1");
    expect(api.listProjects).toHaveBeenCalledWith("cab-1", expect.any(AbortSignal));
    const rowService = makeService({ listProjects: vi.fn(async () => [project]) });
    if (root) await act(async () => root?.unmount());
    root = undefined;
    host?.remove();
    host = undefined;
    await mount(<MemoryRouter><ProjectsOverview service={rowService} employeeId="employee-1" /></MemoryRouter>);
    expect(document.querySelector("a[href='/projects/p-1/runtime']")).toBeTruthy();
    expect(document.querySelector("a[href='/projects/p-1/chat']")).toBeTruthy();
    expect(document.querySelector("a[href='/projects/p-1/files']")).toBeTruthy();
  });

  it("persists a cabinet switch in the URL for the next overview mount", async () => {
    const api = makeService({
      listCabinets: vi.fn(async () => [{ id: "cab-1", name: "Operations" }, { id: "cab-2", name: "Research" }]),
      listProjects: vi.fn(async () => []),
    });
    await mount(<MemoryRouter initialEntries={["/projects?cabinet=cab-1"]}>
      <Routes><Route path="/projects" element={<><ProjectsOverview service={api} employeeId="employee-1" /><QueryEcho /></>} /></Routes>
    </MemoryRouter>);
    const picker = document.querySelector<HTMLSelectElement>(".projects-cabinet-picker select");
    if (!picker) throw new Error("Missing cabinet picker");
    await act(async () => {
      picker.value = "cab-2";
      picker.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(document.querySelector("[data-testid='location']")?.textContent).toBe("/projects?cabinet=cab-2");
    expect(vi.mocked(api.listProjects).mock.calls.some(([cabinetId]) => cabinetId === "cab-2")).toBe(true);
  });

  it("ignores a delayed project list from the previous cabinet after a switch", async () => {
    let resolveCabinetOne!: (projects: ProjectSummary[]) => void;
    const cabinetOneProject = { ...project, name: "Old cabinet project" };
    const cabinetTwoProject: ProjectSummary = { ...project, id: "p-2", cabinet_id: "cab-2", name: "Current cabinet project" };
    const api = makeService({
      listCabinets: vi.fn(async () => [{ id: "cab-1", name: "Operations" }, { id: "cab-2", name: "Research" }]),
      listProjects: vi.fn((cabinetId: string) => cabinetId === "cab-1"
        ? new Promise<ProjectSummary[]>((resolve) => { resolveCabinetOne = resolve; })
        : Promise.resolve([cabinetTwoProject])),
    });
    await mount(<MemoryRouter initialEntries={["/projects?cabinet=cab-1"]}>
      <Routes><Route path="/projects" element={<ProjectsOverview service={api} employeeId="employee-1" />} /></Routes>
    </MemoryRouter>);
    const picker = document.querySelector<HTMLSelectElement>(".projects-cabinet-picker select");
    if (!picker) throw new Error("Missing cabinet picker");
    await act(async () => {
      picker.value = "cab-2";
      picker.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(document.body.textContent).toContain("Current cabinet project");
    await act(async () => {
      resolveCabinetOne([cabinetOneProject]);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(document.body.textContent).toContain("Current cabinet project");
    expect(document.body.textContent).not.toContain("Old cabinet project");
  });

  it("restores the employee cabinet preference on a later mount without a query parameter", async () => {
    window.localStorage.setItem("agentscale.projects.cabinet:employee-1", "cab-2");
    const api = makeService({
      listCabinets: vi.fn(async () => [{ id: "cab-1", name: "Operations" }, { id: "cab-2", name: "Research" }]),
    });
    await mount(<MemoryRouter initialEntries={["/projects"]}>
      <Routes><Route path="/projects" element={<><ProjectsOverview service={api} employeeId="employee-1" /><QueryEcho /></>} /></Routes>
    </MemoryRouter>);
    expect(vi.mocked(api.listProjects).mock.calls.some(([cabinetId]) => cabinetId === "cab-2")).toBe(true);
    expect(document.querySelector<HTMLSelectElement>(".projects-cabinet-picker select")?.value).toBe("cab-2");
  });

  it("renders lifecycle controls appropriate to draft, active, paused, completed, and error records", async () => {
    for (const [status, expected, absent] of [
      ["draft", "Запустить", "Приостановить"],
      ["active", "Перезапустить", "Возобновить"],
      ["paused", "Приостановлен", "Возобновить"],
      ["completed", "Завершён", "Возобновить"],
      ["error", "Перезапустить", "Возобновить"],
    ] as const) {
      const api = makeService({ listProjects: vi.fn(async () => [{ ...project, status,
        ...(status === "active" ? { observed_state: "running" } : {}),
      }]) });
      await mount(<MemoryRouter><ProjectsOverview service={api} employeeId="employee-1" /></MemoryRouter>);
      expect(document.body.textContent).toContain(expected);
      expect(document.body.textContent).not.toContain(absent);
      if (root) await act(async () => root?.unmount());
      root = undefined;
      host?.remove();
      host = undefined;
    }
  });
});
