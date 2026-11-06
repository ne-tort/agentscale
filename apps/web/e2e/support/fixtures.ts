/**
 * Fixtures for the browser E2E layers.
 *
 * Nothing here talks to a backend: every value is a literal shape copied from the
 * API contracts the screens parse (`apps/web/src/api/types.ts`), so the smoke layer
 * stays hermetic and the live layer stays honest about what it asserts.
 */
import { AUTH_STORAGE_KEY } from "../../src/app/auth/lifecycle";

export interface E2ESession {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

/**
 * Structurally valid tokens with no `exp` claim.
 *
 * `AuthLifecycle.accessTokenExpiry()` derives a refresh timer from `exp`; omitting it
 * keeps E2E runs free of background token rotation, which is exercised by the unit
 * tests for the lifecycle rather than by a browser test.
 */
export const E2E_SESSION: E2ESession = {
  access_token: "eyJhbGciOiJub25lIn0.eyJzdWIiOiJlMmUtZW1wbG95ZWUiLCJlbWFpbCI6ImUyZUBleGFtcGxlLnRlc3QifQ.",
  refresh_token: "e2e-refresh-token",
  token_type: "Bearer",
  expires_in: 3600,
};

export const STORAGE_KEY = AUTH_STORAGE_KEY;

export const E2E_EMPLOYEE = {
  id: "employee-e2e",
  display_name: "E2E Employee",
  email: "e2e@example.test",
  status: "active",
};

export const E2E_COMPANY_ID = "company-e2e";

/** `/api/v1/me` wire shape — `active`, `roles`, `contours` and `work_context` are required. */
export function currentUserResponse(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    active: true,
    id: E2E_EMPLOYEE.id,
    sub: "e2e-employee-sub",
    employee_id: E2E_EMPLOYEE.id,
    company_id: E2E_COMPANY_ID,
    email: E2E_EMPLOYEE.email,
    display_name: E2E_EMPLOYEE.display_name,
    roles: ["employee"],
    contours: ["employee"],
    company: { id: E2E_COMPANY_ID, name: "E2E Co" },
    employee: { id: E2E_EMPLOYEE.id, display_name: E2E_EMPLOYEE.display_name, email: E2E_EMPLOYEE.email, status: "active" },
    work_context: { cabinet_id: null, project_id: null },
    ...overrides,
  };
}

export const E2E_CABINETS = [
  { id: "cabinet-e2e", name: "E2E Cabinet", company_id: E2E_COMPANY_ID, projects_count: 2 },
];

export function projectFixture(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "project-e2e",
    cabinet_id: "cabinet-e2e",
    name: "E2E Project",
    status: "draft",
    company_id: E2E_COMPANY_ID,
    selected: false,
    new_chat_enabled: true,
    updated_at: "2026-01-05T10:00:00Z",
    ...overrides,
  };
}

export const E2E_PROJECTS = [
  projectFixture(),
  projectFixture({ id: "project-e2e-active", name: "E2E Active Project", status: "active" }),
];
