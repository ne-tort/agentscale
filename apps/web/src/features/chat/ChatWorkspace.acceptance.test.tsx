// @vitest-environment jsdom
import { act, useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatWorkspace } from "./ChatWorkspace";
import { useChatWorkspace } from "./hooks";
import type { ChatWorkspaceController } from "./hooks";
import { SettingsProvider } from "../../preferences/SettingsProvider";
import type { ChatTranscriptResponse } from "../../api/types";
import type { ChatEvent } from "./types";
import { POLL_BUDGET_MS } from "../../test-support/poll-budget";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

type TestService = {
  getSidebar: ReturnType<typeof vi.fn>;
  getTranscript: ReturnType<typeof vi.fn>;
  getDraft: ReturnType<typeof vi.fn>;
  saveDraft: ReturnType<typeof vi.fn>;
  clearDraft: ReturnType<typeof vi.fn>;
  streamTurn: ReturnType<typeof vi.fn>;
  listSessions: ReturnType<typeof vi.fn>;
  createSession: ReturnType<typeof vi.fn>;
  approve: ReturnType<typeof vi.fn>;
  cancel: ReturnType<typeof vi.fn>;
  listAttachments: ReturnType<typeof vi.fn>;
  uploadAttachment: ReturnType<typeof vi.fn>;
  patchSession: ReturnType<typeof vi.fn>;
};

function makeTranscript(sessionId: string, text: string): ChatTranscriptResponse {
  return {
    session_id: sessionId,
    blocks: [{ kind: "user", text, seq: 1, metadata: {} }],
    oldest_seq: 1,
    newest_seq: 1,
    has_more: false,
    turn_in_progress: false,
  };
}

function makeService(overrides: Partial<TestService> = {}): TestService {
  return {
    getSidebar: vi.fn(async () => ({ cabinet_id: "cabinet-1", projects: [], project_chats: [] })),
    getTranscript: vi.fn(async (_projectId: string, sessionId: string) => makeTranscript(sessionId, `${sessionId} prompt`)),
    getDraft: vi.fn(async () => ({ text: "" })),
    saveDraft: vi.fn(async () => ({})),
    clearDraft: vi.fn(async () => ({})),
    streamTurn: vi.fn(async function* (): AsyncGenerator<ChatEvent> { yield { type: "_turn_complete", data: {} }; }),
    listSessions: vi.fn(async () => ({ items: [] })),
    createSession: vi.fn(async () => ({ id: "created-session" })),
    approve: vi.fn(async () => ({})),
    cancel: vi.fn(async () => ({})),
    listAttachments: vi.fn(async () => ({ items: [] })),
    uploadAttachment: vi.fn(async () => ({ id: "attachment-1", filename: "upload.txt" })),
    patchSession: vi.fn(async () => ({})),
    ...overrides,
  };
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;
let latestController: ChatWorkspaceController | undefined;

function Harness({ service, cabinetId }: { service: TestService; cabinetId?: string }) {
  const [sessionId, setSessionId] = useState<string | undefined>("session-1");
  const controller = useChatWorkspace({ projectId: "project-1", cabinetId, initialSessionId: "session-1", service: service as never });
  latestController = controller;
  useEffect(() => { void controller.actions.selectSession(sessionId); }, [controller.actions.selectSession, sessionId]);
  return <>
    <button type="button" onClick={() => setSessionId("session-2")}>Open second session</button>
    <output data-testid="selected-session">{controller.state.selectedSessionId}</output>
    <ChatWorkspace controller={controller} />
  </>;
}

async function mount(service: TestService, locale = "en", cabinetId?: string): Promise<HTMLDivElement> {
  // The workspace reads its copy through `useT`, so the tests pin a locale and
  // the assertions below stay about chat behaviour rather than translation.
  window.localStorage.setItem("agentscale.locale", locale);
  host = document.createElement("div");
  document.body.append(host);
  if (!HTMLDivElement.prototype.scrollIntoView) {
    Object.defineProperty(HTMLDivElement.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
  }
  root = createRoot(host);
  await act(async () => root?.render(<SettingsProvider><Harness service={service} cabinetId={cabinetId} /></SettingsProvider>));
  return host;
}

async function waitFor(condition: () => boolean): Promise<void> {
  const deadline = Date.now() + POLL_BUDGET_MS;
  while (!condition()) {
    if (Date.now() >= deadline) throw new Error("Timed out waiting for chat acceptance condition");
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
  }
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  latestController = undefined;
  host?.remove();
  host = undefined;
  window.sessionStorage.clear();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("Chat session workflow acceptance", () => {
  it("renders the workspace in Russian for a Russian reader", async () => {
    const container = await mount(makeService(), "ru");
    await waitFor(() => (container.textContent ?? "").includes("Чаты"));
    expect(container.querySelector("h1")?.textContent).toBe("Чаты");
    expect(container.textContent).toContain("Проекты");
    expect(container.querySelector("textarea")?.getAttribute("placeholder")).toBe("Напишите агенту…");
    expect(container.textContent).toContain("Рабочее пространство");
  });

  it("writes the last-message clock in the interface's language, not the browser's", async () => {
    // `formatTime` used `toLocaleTimeString([], …)`, which follows the browser, so a
    // Russian screen could show "02:05 PM" and an English one "14:05".
    const sidebar = {
      cabinet_id: "cabinet-1",
      projects: [{
        project_id: "project-1",
        project_name: "Project",
        new_chat_enabled: true,
        chats: [{ session_id: "session-1", title: "Chat", has_draft: false, pinned: false, last_message_at: "2026-10-01T14:05:00Z" }],
      }],
      project_chats: [],
    };
    const russianContainer = await mount(makeService({ getSidebar: vi.fn(async () => sidebar) }), "ru", "cabinet-1");
    await waitFor(() => russianContainer.querySelector(".chat-tree__session-meta") !== null);
    const russian = russianContainer.querySelector(".chat-tree__session-meta")?.textContent ?? "";
    expect(russian).toMatch(/^\d{2}:\d{2}$/);

    if (root) await act(async () => root?.unmount());
    root = undefined;
    host?.remove();
    host = undefined;

    const englishContainer = await mount(makeService({ getSidebar: vi.fn(async () => sidebar) }), "en", "cabinet-1");
    await waitFor(() => englishContainer.querySelector(".chat-tree__session-meta") !== null);
    const english = englishContainer.querySelector(".chat-tree__session-meta")?.textContent ?? "";
    expect(english).toMatch(/\d{1,2}:\d{2}/);
    expect(english).toMatch(/[AP]M/i);
  });

  it("ignores a late transcript success from the previous session and loads the selected session", async () => {
    let resolveFirst!: (transcript: ChatTranscriptResponse) => void;
    const firstTranscript = new Promise<ChatTranscriptResponse>((resolve) => { resolveFirst = resolve; });
    const service = makeService({
      getTranscript: vi.fn(async (_projectId: string, sessionId: string) => sessionId === "session-1"
        ? firstTranscript
        : makeTranscript("session-2", "Second session history")),
    });
    const container = await mount(service);
    await waitFor(() => service.getTranscript.mock.calls.some((call) => call[1] === "session-1"));
    const switchButton = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "Open second session");
    await act(async () => switchButton?.click());
    await waitFor(() => container.textContent?.includes("Second session history") ?? false);
    await act(async () => { resolveFirst(makeTranscript("session-1", "Stale first session response")); });
    expect(container.textContent).toContain("Second session history");
    expect(container.textContent).not.toContain("Stale first session response");
    expect(container.querySelector("[data-testid='selected-session']")?.textContent).toBe("session-2");
  });

  it("keeps a failed transcript retryable, then replaces it with the successful retry response", async () => {
    let attempts = 0;
    const service = makeService({
      getTranscript: vi.fn(async (_projectId: string, sessionId: string) => {
        attempts += 1;
        if (attempts === 1) throw new Error("temporary transcript failure");
        return makeTranscript(sessionId, "Recovered session history");
      }),
    });
    const container = await mount(service);
    await waitFor(() => container.textContent?.includes("temporary transcript failure") ?? false);
    await act(async () => latestController?.actions.reload());
    await waitFor(() => container.textContent?.includes("Recovered session history") ?? false);
    expect(service.getTranscript).toHaveBeenCalledTimes(2);
    expect(container.textContent).not.toContain("temporary transcript failure");
  });

  it("adopts the backend-created session after the pending draft reaches the materialization threshold", async () => {
    let resolveFirstSave!: (payload: Record<string, unknown>) => void;
    const firstSave = new Promise<Record<string, unknown>>((resolve) => { resolveFirstSave = resolve; });
    let saveCalls = 0;
    const service = makeService({
      getDraft: vi.fn(async (_projectId: string, sessionId?: string) => ({ text: sessionId === "created-session" ? "materialized draft text" : "" })),
      saveDraft: vi.fn(async (_projectId: string, text: string, sessionId?: string) => {
        saveCalls += 1;
        if (saveCalls === 1 && !sessionId) return firstSave;
        if (text.length >= 5 && !sessionId) return { text, session_id: "created-session", materialized: true };
        return { text, session_id: sessionId, materialized: false };
      }),
    });
    const container = await mount(service);
    await waitFor(() => container.querySelector("[data-testid='selected-session']")?.textContent === "session-1");
    await act(async () => latestController?.actions.selectSession(undefined));
    await act(async () => latestController?.actions.saveDraft("first five"));
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 400)); });
    await act(async () => latestController?.actions.saveDraft("first five and more"));
    await act(async () => { resolveFirstSave({ text: "first five", session_id: "created-session", materialized: true }); });
    await waitFor(() => container.querySelector("[data-testid='selected-session']")?.textContent === "created-session");
    // Draft persistence is debounced (~350 ms) and serialized behind the first
    // write, so the second save can only land after that timer fires. Waiting a
    // single tick asserted a race, not the behaviour under test.
    await waitFor(() => service.saveDraft.mock.calls.some(([, , sessionId]) => sessionId === "created-session"));
    expect(service.saveDraft).toHaveBeenCalledWith("project-1", "first five and more", "created-session");
    expect(service.createSession).not.toHaveBeenCalled();
  });

  it("retains a pending draft after a transient persistence failure and retries it against its original session", async () => {
    let attempts = 0;
    const service = makeService({
      saveDraft: vi.fn(async (_projectId: string, text: string, sessionId?: string) => {
        attempts += 1;
        if (attempts === 1) throw new Error("temporary draft failure");
        return { text, session_id: sessionId };
      }),
    });
    const container = await mount(service);
    await waitFor(() => container.querySelector("[data-testid='selected-session']")?.textContent === "session-1");
    await act(async () => latestController?.actions.saveDraft("draft for one"));
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 400)); });
    expect(attempts).toBe(1);
    await act(async () => latestController?.actions.reload());
    await waitFor(() => service.saveDraft.mock.calls.length === 2);
    expect(service.saveDraft.mock.calls[1]).toEqual(["project-1", "draft for one", "session-1"]);
  });

  it("ignores a late transcript failure after switching sessions", async () => {
    let rejectFirst!: (error: Error) => void;
    const firstTranscript = new Promise<ChatTranscriptResponse>((_resolve, reject) => { rejectFirst = reject; });
    const service = makeService({
      getTranscript: vi.fn(async (_projectId: string, sessionId: string) => sessionId === "session-1"
        ? firstTranscript
        : makeTranscript("session-2", "Current session history")),
    });
    const container = await mount(service);
    await waitFor(() => service.getTranscript.mock.calls.some((call) => call[1] === "session-1"));
    const switchButton = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "Open second session");
    await act(async () => switchButton?.click());
    await waitFor(() => container.textContent?.includes("Current session history") ?? false);
    await act(async () => { rejectFirst(new Error("stale first session failure")); });
    expect(container.textContent).toContain("Current session history");
    expect(container.textContent).not.toContain("stale first session failure");
  });
});
