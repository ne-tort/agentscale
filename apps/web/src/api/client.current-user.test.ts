import { describe, expect, it, vi } from "vitest";
import { createApiClient } from "./client";
import type { CurrentUserWireResponse } from "./types";

function meResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function employeeWire(overrides: Partial<CurrentUserWireResponse> = {}): CurrentUserWireResponse {
  return {
    active: true,
    sub: "employee-subject",
    email: "ada@example.test",
    roles: ["employee"],
    contours: ["employee"],
    company: null,
    employee: {
      id: "employee-1",
      status: "active",
      email: "ada@example.test",
      contact_email: null,
      memberships: [{ company_id: "company-1", company_name: "Northwind", role: "member" }],
    },
    work_context: { cabinet_id: "cabinet-1", project_id: "project-1" },
    ...overrides,
  };
}

function companyWire(active: boolean): CurrentUserWireResponse {
  return {
    active,
    sub: "company-subject",
    email: "accounts@example.test",
    roles: ["company"],
    contours: ["company"],
    company: { id: "company-1", name: "Northwind", username: "northwind", contact_email: "accounts@example.test" },
    employee: null,
    work_context: { cabinet_id: null, project_id: null },
  };
}

describe("GET /api/v1/me current-user normalization", () => {
  it("maps the active employee wire response explicitly", async () => {
    const fetcher = vi.fn(async () => meResponse({ ...employeeWire(), ignored_server_field: "discard" }));
    const client = createApiClient({ fetcher });

    const user = await client.me();

    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/me");
    expect(user).toMatchObject({
      id: "employee-1",
      subject: "employee-subject",
      sub: "employee-subject",
      email: "ada@example.test",
      display_name: "ada@example.test",
      roles: ["employee"],
      active: true,
      employee_id: "employee-1",
      employee: { id: "employee-1", status: "active" },
      memberships: [{ company_id: "company-1", company_name: "Northwind", role: "member" }],
      work_context: { cabinet_id: "cabinet-1", project_id: "project-1" },
    });
    expect(user).not.toHaveProperty("company_id");
    expect(user).not.toHaveProperty("ignored_server_field");
  });

  it("maps explicit top-level identity aliases and memberships", async () => {
    const wire = {
      ...employeeWire(),
      id: "wire-id",
      subject: "wire-subject",
      display_name: "Ada Lovelace",
      company_id: "wire-company-id",
      employee_id: "wire-employee-id",
      memberships: [{ company_id: "wire-membership-company", role: "owner" }],
    };
    const fetcher = vi.fn(async () => meResponse(wire));
    const user = await createApiClient({ fetcher }).me();

    expect(user).toMatchObject({
      id: "wire-id",
      subject: "wire-subject",
      sub: "wire-subject",
      display_name: "Ada Lovelace",
      company_id: "wire-company-id",
      employee_id: "wire-employee-id",
      memberships: [{ company_id: "wire-membership-company", role: "owner" }],
    });
  });

  it("preserves false for a disabled employee", async () => {
    const fetcher = vi.fn(async () => meResponse(employeeWire({
      active: false,
      employee: {
        id: "employee-1",
        status: "disabled",
        email: "ada@example.test",
        contact_email: null,
        memberships: [],
      },
    })));
    const user = await createApiClient({ fetcher }).me();

    expect(user.active).toBe(false);
    expect(user.employee?.status).toBe("disabled");
  });

  it("maps an active company principal without requiring an employee", async () => {
    const fetcher = vi.fn(async () => meResponse(companyWire(true)));
    const user = await createApiClient({ fetcher }).me();

    expect(user).toMatchObject({
      id: "company-1",
      subject: "company-subject",
      display_name: "Northwind",
      roles: ["company"],
      company_id: "company-1",
      active: true,
      company: { id: "company-1", name: "Northwind" },
      employee: null,
      work_context: { cabinet_id: null, project_id: null },
    });
    expect(user).not.toHaveProperty("employee_id");
  });

  it.each([
    ["inactive", null],
    ["deleted", { id: "linked-employee-1", status: "active", email: "admin@example.test", memberships: [] }],
  ])("preserves false for a %s company principal", async (_state, employee) => {
    const fetcher = vi.fn(async () => meResponse({ ...companyWire(false), employee }));
    const user = await createApiClient({ fetcher }).me();

    expect(user.active).toBe(false);
    if (employee) expect(user.employee?.status).toBe("active");
  });

  it.each([
    ["missing", { sub: "employee-subject", roles: [], contours: [], company: null, employee: null, work_context: {} }],
    ["non-boolean", { active: "true", sub: "employee-subject", roles: [], contours: [], company: null, employee: null, work_context: {} }],
  ])("rejects a %s top-level active value", async (_label, body) => {
    const fetcher = vi.fn(async () => meResponse(body));
    const userRequest = createApiClient({ fetcher }).me();

    await expect(userRequest).rejects.toThrow("Invalid /me response: active must be a boolean");
  });
});