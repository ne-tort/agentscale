import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CabinetOverview } from "./CabinetOverview";
import type { CabinetOverviewService, CabinetOverviewSnapshot } from "./contracts";

const snapshot: CabinetOverviewSnapshot = {
  cabinet: { id: "cabinet-1", name: "Operations", company_id: "company-1", company_name: "Northwind" },
  projects: [{ id: "project-1", cabinet_id: "cabinet-1", name: "Signal Lab", status: "active", observed_state: "running" }],
  metrics: { employees_total: 2, employees_online: 1, agent_tokens_used: 42, agent_requests: 3, last_activity_at: "2026-11-01T10:00:00Z" },
  selection: { project_id: "project-1", chat_session_id: "session-1" },
  sidebar: { projects: [{ project_id: "project-1", chats: [{ project_id: "project-1", session_id: "session-1" }] }], pinned: [] },
  modules: [{ id: "module-1", name: "Equipment" }],
  moduleTabs: { "module-1": [{ title: "Equipment", view_slug: "equipment_hub", nav: { contour: "employee", placement: "data" } }] },
  errors: {},
};

const service: CabinetOverviewService = { load: vi.fn(async () => snapshot) };

describe("CabinetOverview", () => {
  it("starts with an explicit loading state and does not render fabricated metrics during SSR", () => {
    const markup = renderToStaticMarkup(<CabinetOverview cabinetId="cabinet-1" cabinetName="Operations" service={service} onNavigate={() => undefined} />);
    expect(markup).toContain("Загрузка обзора кабинета");
    expect(markup).not.toContain("Northwind");
    expect(markup).not.toContain("42");
  });

  it("declares a scoped surface for confirmed context, projects, activity, and module navigation", () => {
    const immediateService: CabinetOverviewService = { load: () => Promise.resolve(snapshot) };
    const markup = renderToStaticMarkup(<CabinetOverview cabinetId="cabinet-1" cabinetName="Operations" initialSnapshot={snapshot} service={immediateService} onNavigate={() => undefined} />);
    expect(markup).toContain("Operations");
    expect(markup).toContain("Проекты");
    expect(markup).toContain("Активность и присутствие");
    expect(markup).toContain("Управление");
    expect(markup).toContain("Данные");
    expect(markup).toContain("Только подтверждённые API-данные");
  });

  it("renders explicit empty states when the API confirms no projects and no modules", () => {
    const empty: CabinetOverviewSnapshot = { ...snapshot, projects: [], modules: [], moduleTabs: {}, errors: {} };
    const immediateService: CabinetOverviewService = { load: () => Promise.resolve(empty) };
    const markup = renderToStaticMarkup(<CabinetOverview cabinetId="cabinet-1" cabinetName="Operations" initialSnapshot={empty} service={immediateService} onNavigate={() => undefined} />);
    expect(markup).toContain("В этом кабинете пока нет проектов");
    expect(markup).toContain("Нет подключённых разделов");
  });
});
