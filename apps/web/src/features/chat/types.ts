import type { ChatSidebarProject, ChatSidebarItem, ChatTranscriptResponse } from "../../api/types";

export type ChatEvent = { type: string; data: Record<string, unknown>; at?: string | null; seq?: number; created_at?: string | null };

export type ChatAttachment = {
  id: string;
  filename: string;
  content_type?: string | null;
  size?: number;
  size_bytes?: number;
  storage_ref?: string;
};

export type ChatBlock = {
  id: string;
  kind: string;
  text?: string;
  name?: string;
  input?: unknown;
  output?: unknown;
  is_error?: boolean;
  approvalId?: string;
  attachments?: ChatAttachment[];
  reason?: string;
  phase?: string;
  message?: string;
  attempt?: number;
  max_attempts?: number;
  next_model?: string;
  retry_in_ms?: number;
  tool_use_id?: string;
  raw?: ChatEvent;
};

export type ChatItem = ChatSidebarItem & { created_at?: string; model?: string | null };
export type ChatProject = ChatSidebarProject;

export type ChatStatus = "idle" | "loading" | "uploading" | "streaming" | "stopping" | "error";

export type PendingTurn = {
  turnId: string;
  sessionId: string;
  text: string;
  attachmentRefs: string[];
  model?: string;
  submittedAt: number;
};

export type ChatState = {
  projectId: string;
  cabinetId?: string;
  projects: ChatProject[];
  projectChats: ChatItem[];
  selectedSessionId?: string;
  sessionTitle?: string;
  sessionPinned: boolean;
  model?: string;
  modelOptions: string[];
  blocks: ChatBlock[];
  draft: string;
  attachments: ChatAttachment[];
  status: ChatStatus;
  error?: string;
  errorCode?: string;
  retryable: boolean;
  pendingTurn?: PendingTurn;
  lastSeq: number;
  hasMore: boolean;
  loadingSidebar: boolean;
  loadingTranscript: boolean;
  oldestSeq?: number;
  approvingId?: string;
};

export type ChatAction =
  | { type: "sidebar_loading" }
  | { type: "sidebar_loaded"; payload: { sidebar: { cabinet_id: string; selected_project_id?: string; selected_session_id?: string; projects: ChatProject[]; project_chats: ChatItem[] }; projectId: string } }
  | { type: "sidebar_failed"; message: string }
  | { type: "select_session"; sessionId?: string; title?: string | null; pinned?: boolean; preserveTurn?: boolean }
  | { type: "start_new_chat"; projectId?: string }
  | { type: "session_loading" }
  | { type: "transcript_loaded"; payload: ChatTranscriptResponse; replace?: boolean; prefix?: string }
  | { type: "transcript_failed"; message: string }
  | { type: "older_transcript_loaded"; payload: ChatTranscriptResponse; prefix?: string }
  | { type: "draft_changed"; draft: string }
  | { type: "attachments_changed"; attachments: ChatAttachment[] }
  | { type: "attachment_upload_started" }
  | { type: "attachment_upload_finished" }
  | { type: "model_changed"; model?: string }
  | { type: "session_patched"; payload: { title?: string | null; pinned?: boolean } }
  | { type: "send_started"; turn: PendingTurn; userBlock: ChatBlock }
  | { type: "stream_event"; event: ChatEvent }
  | { type: "stream_done" }
  | { type: "stream_failed"; message: string; code?: string; retryable: boolean; turn?: PendingTurn; clearPending?: boolean }
  | { type: "pending_restored"; turn: PendingTurn }
  | { type: "stop_started" }
  | { type: "stop_finished" }
  | { type: "approval_started"; id: string }
  | { type: "approval_finished"; id: string }
  | { type: "approval_reconciled" }
  | { type: "retry_started"; turn: PendingTurn }
  | { type: "clear_error" };

export const EMPTY_CHAT_STATE = (projectId: string): ChatState => ({
  projectId,
  projects: [],
  projectChats: [],
  sessionPinned: false,
  model: "Auto",
  modelOptions: [],
  blocks: [],
  draft: "",
  attachments: [],
  status: "idle",
  retryable: false,
  lastSeq: 0,
  hasMore: false,
  loadingSidebar: false,
  loadingTranscript: false,
  oldestSeq: undefined,
});
