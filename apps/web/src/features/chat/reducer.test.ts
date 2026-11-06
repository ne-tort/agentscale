import { describe, expect, it } from "vitest";
import { appendEventForTest, chatReducer, EMPTY_CHAT_STATE, makeUserBlock } from "./reducer";
import type { ChatEvent, PendingTurn } from "./types";

const turn: PendingTurn = {
  turnId: "turn-1",
  sessionId: "session-1",
  text: "Inspect the workspace",
  attachmentRefs: [],
  submittedAt: 1,
};

const event = (type: string, data: Record<string, unknown>, seq: number): ChatEvent => ({ type, data, seq, at: "2026-11-02T00:00:00Z" });

describe("chatReducer", () => {
  it("clears stale attachments and approval requests when leaving their session", () => {
    let state = chatReducer(EMPTY_CHAT_STATE("project-1"), { type: "select_session", sessionId: "session-1" });
    state = chatReducer(state, { type: "attachments_changed", attachments: [{ id: "attachment-1", filename: "first.txt" }] });
    state = chatReducer(state, { type: "stream_event", event: event("tool_approval_request", { id: "approval-1", name: "shell", input: {} }, 1) });
    state = chatReducer(state, { type: "select_session", sessionId: "session-2" });
    expect(state.selectedSessionId).toBe("session-2");
    expect(state.blocks).toEqual([]);
    expect(state.attachments).toEqual([]);
    expect(state.approvingId).toBeUndefined();
    expect(state.lastSeq).toBe(0);
  });

  it("accepts a successful transcript after a retryable load failure", () => {
    let state = chatReducer(EMPTY_CHAT_STATE("project-1"), { type: "select_session", sessionId: "session-2" });
    state = chatReducer(state, { type: "session_loading" });
    state = chatReducer(state, { type: "transcript_failed", message: "Transcript unavailable" });
    expect(state.error).toBe("Transcript unavailable");
    expect(state.retryable).toBe(true);
    state = chatReducer(state, { type: "session_loading" });
    state = chatReducer(state, { type: "transcript_loaded", payload: {
      session_id: "session-2", blocks: [{ kind: "user", text: "Second session prompt", seq: 2, metadata: {} }],
      oldest_seq: 2, newest_seq: 2, has_more: false, turn_in_progress: false,
    } });
    expect(state.error).toBeUndefined();
    expect(state.retryable).toBe(false);
    expect(state.loadingTranscript).toBe(false);
    expect(state.blocks.map((block) => block.text)).toContain("Second session prompt");
    state = chatReducer(state, { type: "select_session", sessionId: "session-3" });
    expect(state.blocks).toEqual([]);
    expect(state.lastSeq).toBe(0);
    state = chatReducer(state, { type: "session_loading" });
    state = chatReducer(state, { type: "transcript_loaded", payload: {
      session_id: "session-3", blocks: [{ kind: "user", text: "Third session prompt", seq: 1, metadata: {} }],
      oldest_seq: 1, newest_seq: 1, has_more: false, turn_in_progress: false,
    } });
    expect(state.blocks.map((block) => block.text)).toEqual(["Third session prompt"]);
  });

  it("keeps the optimistic bubble until the durable event replaces it and merges cumulative text deltas", () => {
    let state = chatReducer(EMPTY_CHAT_STATE("project-1"), { type: "send_started", turn, userBlock: makeUserBlock(turn) });
    state = appendEventForTest(state, event("user_message", { text: turn.text, turn_id: turn.turnId }, 1));
    state = appendEventForTest(state, event("text_delta", { text: "Hello" }, 2));
    state = appendEventForTest(state, event("text_delta", { text: "Hello from the agent" }, 3));
    expect(state.blocks.filter((block) => block.kind === "user")).toHaveLength(1);
    expect(state.blocks.find((block) => block.kind === "assistant")?.text).toBe("Hello from the agent");
  });

  it("replaces the optimistic user bubble when the durable user_message arrives", () => {
    const optimistic = makeUserBlock(turn);
    let state = chatReducer(EMPTY_CHAT_STATE("project-1"), { type: "send_started", turn, userBlock: optimistic });
    state = appendEventForTest(state, event("user_message", { text: turn.text, turn_id: turn.turnId }, 1));
    expect(state.blocks.filter((block) => block.kind === "user")).toHaveLength(1);
    expect(state.blocks[0].raw?.type).toBe("user_message");
  });
  it("ignores already applied sequence numbers after reconnect", () => {
    let state = appendEventForTest(EMPTY_CHAT_STATE("project-1"), event("text_delta", { text: "one" }, 4));
    state = appendEventForTest(state, event("text_delta", { text: "two" }, 4));
    expect(state.blocks.filter((block) => block.kind === "assistant")).toHaveLength(1);
    expect(state.blocks[0].text).toBe("one");
  });

  it("represents tools, approvals, errors and terminal status without inventing run data", () => {
    let state = EMPTY_CHAT_STATE("project-1");
    state = appendEventForTest(state, event("tool_call", { id: "tool-1", name: "read_file", input: { path: "README.md" } }, 1));
    state = appendEventForTest(state, event("tool_approval_request", { id: "approval-1", name: "shell", input: { command: "git status" } }, 2));
    state = appendEventForTest(state, event("error", { code: "MODEL_ERROR", message: "Try again" }, 3));
    expect(state.blocks.map((block) => block.kind)).toEqual(["tool_call", "approval", "error"]);
    expect(state.blocks[1].approvalId).toBe("approval-1");
    expect(state.status).toBe("error");
  });
});
