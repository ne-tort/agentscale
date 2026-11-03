import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from "react";
import type { ChatBlock, ChatProject, ChatAttachment } from "./types";
import type { ChatWorkspaceController } from "./hooks";
import "./chat.css";

function formatTime(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function blockText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "";
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function ChatTree({
  projects,
  selectedSessionId,
  newChatBlocked,
  onSelect,
  onNew,
}: {
  projects: ChatProject[];
  selectedSessionId?: string;
  newChatBlocked: boolean;
  onSelect: (sessionId: string) => void;
  onNew: (projectId: string) => void;
}) {
  return <nav className="chat-tree" aria-label="Projects and chats">
    <div className="chat-tree__heading"><span>Projects</span><span className="chat-tree__count">{projects.length}</span></div>
    {projects.length === 0 && <p className="chat-empty-hint">No active projects yet.</p>}
    {projects.map((project) => <section className="chat-tree__project" key={project.project_id}>
      <div className="chat-tree__project-row">
        <span className="chat-tree__project-name" title={project.project_name}>▾ {project.project_name}</span>
        <button className="chat-icon-button" onClick={() => onNew(project.project_id)} disabled={!project.new_chat_enabled || newChatBlocked} aria-label={`New chat in ${project.project_name}`} title={project.new_chat_enabled ? "New chat" : "Project runtime is not ready"}>＋</button>
      </div>
      <div className="chat-tree__sessions">
        {project.chats.length === 0 && <span className="chat-tree__muted">No chats</span>}
        {project.chats.map((chat) => <button key={chat.session_id} className={`chat-tree__session ${chat.session_id === selectedSessionId ? "is-selected" : ""}`} onClick={() => onSelect(chat.session_id)}>
          <span className="chat-tree__session-title">{chat.pinned && <span aria-label="Pinned">★ </span>}{chat.title || "Untitled chat"}</span>
          <span className="chat-tree__session-meta">{chat.has_draft ? "Draft" : chat.last_message_at ? formatTime(chat.last_message_at) : "New"}</span>
        </button>)}
      </div>
    </section>)}
  </nav>;
}

function MarkdownText({ text }: { text: string }) {
  const paragraphs = text.split(/\n{2,}/g);
  return <div className="chat-markdown">{paragraphs.map((paragraph, index) => <p key={`${index}-${paragraph.slice(0, 12)}`}>{paragraph}</p>)}</div>;
}

function ToolBlock({ block }: { block: ChatBlock }) {
  const [open, setOpen] = useState(false);
  const title = block.name || "Tool activity";
  const body = block.kind === "tool_call" ? block.input : block.output ?? block.message;
  return <article className={`chat-tool chat-tool--${block.kind}`}>
    <button type="button" className="chat-tool__summary" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      <span className="chat-tool__dot" aria-hidden="true" />
      <span className="chat-tool__title">{title}</span>
      <span className="chat-tool__status">{block.kind === "tool_result" ? (block.is_error ? "Failed" : "Done") : block.kind === "tool_progress" ? "Working" : "Called"}</span>
      <span aria-hidden="true">{open ? "⌃" : "⌄"}</span>
    </button>
    {open && <pre className="chat-tool__details">{blockText(body)}</pre>}
  </article>;
}

function ApprovalBlock({ block, busy, blocked, onDecision }: { block: ChatBlock; busy: boolean; blocked: boolean; onDecision: (id: string, decision: "approve" | "deny") => void }) {
  const id = block.approvalId ?? block.id;
  return <article className="chat-approval" aria-label="Tool approval request">
    <div className="chat-approval__icon" aria-hidden="true">!</div>
    <div className="chat-approval__body">
      <strong>Approval required for {block.name || "tool"}</strong>
      {block.reason && <p>{block.reason}</p>}
      <pre>{blockText(block.input)}</pre>
      <div className="chat-approval__actions">
        <button type="button" className="chat-button chat-button--primary" disabled={busy || blocked} onClick={() => onDecision(id, "approve")}>{busy ? "Saving…" : blocked ? "Refresh to confirm" : "Allow"}</button>
        <button type="button" className="chat-button" disabled={busy || blocked} onClick={() => onDecision(id, "deny")}>Deny</button>
      </div>
    </div>
  </article>;
}

function Transcript({ blocks, approvingId, approvalBlocked, hasMore, onLoadOlder, onDecision }: { blocks: ChatBlock[]; approvingId?: string; approvalBlocked: boolean; hasMore: boolean; onLoadOlder: () => void; onDecision: (id: string, decision: "approve" | "deny") => void }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [blocks.length, blocks.at(-1)?.text]);
  return <div className="chat-transcript" aria-live="polite">
    {hasMore && <button type="button" className="chat-history-button" onClick={onLoadOlder}>Load older messages</button>}
    {blocks.length === 0 && <div className="chat-welcome"><span className="chat-welcome__mark">✦</span><h2>What can we work on?</h2><p>Ask about your project, inspect files, or let the agent use its tools.</p></div>}
    {blocks.map((block) => {
      if (block.kind === "user") return <article className="chat-message chat-message--user" key={block.id}><div className="chat-message__bubble"><MarkdownText text={block.text ?? ""} />{block.attachments?.length ? <AttachmentList attachments={block.attachments} compact /> : null}</div></article>;
      if (block.kind === "assistant") return <article className="chat-message chat-message--assistant" key={block.id}><div className="chat-avatar" aria-hidden="true">✦</div><div className="chat-message__content"><MarkdownText text={block.text ?? ""} />{block.raw?.at && <time>{formatTime(block.raw.at)}</time>}</div></article>;
      if (block.kind === "thinking") return <article className="chat-thinking" key={block.id}><span className="chat-thinking__spinner" aria-hidden="true" />{block.text || "Thinking…"}</article>;
      if (block.kind === "approval") return <ApprovalBlock key={block.id} block={block} busy={approvingId === block.approvalId} blocked={approvalBlocked} onDecision={onDecision} />;
      if (["tool_call", "tool_result", "tool_progress"].includes(block.kind)) return <ToolBlock key={block.id} block={block} />;
      if (block.kind === "status") return <div className="chat-status-line" key={block.id}><span className="chat-status-line__spinner" aria-hidden="true" />{block.message || block.phase || "Working…"}</div>;
      if (block.kind === "error") return <div className="chat-inline-error" key={block.id}>{block.message || "Agent error"}</div>;
      return <div className="chat-event-line" key={block.id}>{block.message}</div>;
    })}
    <div ref={endRef} />
  </div>;
}

function AttachmentList({ attachments, compact = false, onRemove }: { attachments: ChatAttachment[]; compact?: boolean; onRemove?: (id: string) => void }) {
  return <div className={`chat-attachments ${compact ? "chat-attachments--compact" : ""}`} aria-label="Attachments">
    {attachments.map((attachment) => <span className="chat-attachment" key={attachment.id}><span aria-hidden="true">▧</span><span>{attachment.filename}</span>{onRemove && <button type="button" onClick={() => onRemove(attachment.id)} aria-label={`Remove ${attachment.filename}`}>×</button>}</span>)}
  </div>;
}

function Composer({ controller }: { controller: ChatWorkspaceController }) {
  const { state, actions } = controller;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const busy = state.status === "streaming" || state.status === "loading" || state.status === "stopping" || state.status === "uploading";
  const onSubmit = (event: FormEvent) => { event.preventDefault(); void actions.send(); };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault(); void actions.send();
    }
  };
  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void actions.chooseFile(file);
  };
  return <form className="chat-composer" onSubmit={onSubmit}>
    {state.attachments.length > 0 && <AttachmentList attachments={state.attachments} onRemove={actions.removeAttachment} />}
    <textarea aria-label="Message" value={state.draft} onChange={(event) => actions.saveDraft(event.target.value)} onKeyDown={onKeyDown} placeholder="Message your agent…" disabled={state.status === "stopping"} rows={3} />
    <div className="chat-composer__toolbar">
      <div className="chat-composer__tools">
        <input ref={fileInputRef} type="file" className="chat-visually-hidden" onChange={onFileChange} />
        <button type="button" className="chat-button chat-button--quiet" onClick={() => fileInputRef.current?.click()} disabled={busy || state.errorCode === "attachment_upload_outcome_unknown"} aria-label="Attach a file">⌕ <span>Attach</span></button>
        <label className="chat-model"><span className="chat-visually-hidden">Model</span><select value={state.model ?? "Auto"} onChange={(event) => actions.setModel(event.target.value)} disabled={busy}>{state.modelOptions.map((model) => <option value={model} key={model}>{model}</option>)}</select></label>
      </div>
      {busy && state.status !== "stopping" ? <button type="button" className="chat-button chat-button--stop" onClick={() => void actions.stop()} aria-label="Stop generating">■ <span>Stop</span></button> : <button type="submit" className="chat-send" disabled={!state.draft.trim() && !state.attachments.length} aria-label="Send message">↑</button>}
    </div>
    <p className="chat-composer__hint">Enter to send · Shift + Enter for a new line</p>
  </form>;
}

function SessionHeader({ controller }: { controller: ChatWorkspaceController }) {
  const { state, actions } = controller;
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(state.sessionTitle ?? "New conversation");
  useEffect(() => setTitle(state.sessionTitle ?? "New conversation"), [state.sessionTitle]);
  const saveTitle = () => { setEditing(false); void actions.patchSession({ title: title.trim() || null }); };
  return <header className="chat-session-header">
    <div className="chat-session-header__title">
      {editing ? <input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} onBlur={saveTitle} onKeyDown={(event) => { if (event.key === "Enter") saveTitle(); if (event.key === "Escape") setEditing(false); }} aria-label="Chat title" /> : <button className="chat-title-button" onClick={() => setEditing(true)}>{state.sessionTitle || (state.selectedSessionId ? "Conversation" : "New conversation")} <span aria-hidden="true">✎</span></button>}
      <span className={`chat-session-status chat-session-status--${state.status}`}>{state.status === "streaming" ? "Working" : state.status === "error" ? "Needs attention" : "Ready"}</span>
    </div>
    {state.selectedSessionId && <button className={`chat-pin-button ${state.sessionPinned ? "is-pinned" : ""}`} onClick={() => void actions.patchSession({ pin: !state.sessionPinned })} aria-pressed={state.sessionPinned} aria-label={state.sessionPinned ? "Unpin chat" : "Pin chat"}>★</button>}
  </header>;
}

export type ChatWorkspaceProps = {
  controller: ChatWorkspaceController;
  onNewChat?: (projectId: string) => void;
};

export function ChatWorkspace({ controller, onNewChat }: ChatWorkspaceProps) {
  const { state, actions } = controller;
  const [treeOpen, setTreeOpen] = useState(false);
  const selectedProjectName = state.projects.find((project) => project.project_id === state.projectId)?.project_name ?? "Project chat";
  const newChat = (nextProjectId: string) => {
    if (controller.sessionCreateBlocked) return;
    if (onNewChat) onNewChat(nextProjectId);
    else void actions.startNewChat(nextProjectId);
  };
  return <section className="chat-workspace" aria-label="Agent chat workspace">
    <button className="chat-mobile-tree-toggle" onClick={() => setTreeOpen((open) => !open)} aria-expanded={treeOpen}>☰ <span>{selectedProjectName}</span></button>
    <aside className={`chat-sidebar ${treeOpen ? "is-open" : ""}`}>
      <div className="chat-sidebar__top"><div><p className="chat-eyebrow">Workspace</p><h1>Chats</h1></div><button className="chat-new-button" onClick={() => newChat(state.projectId)} disabled={controller.sessionCreateBlocked} aria-label="New chat">＋ New</button></div>
      {state.loadingSidebar ? <div className="chat-sidebar__skeleton" aria-label="Loading chats"><span /><span /><span /></div> : <ChatTree projects={state.projects} selectedSessionId={state.selectedSessionId} newChatBlocked={controller.sessionCreateBlocked} onSelect={(sessionId) => { setTreeOpen(false); void actions.selectSession(sessionId); }} onNew={newChat} />}
      <button className="chat-sidebar__refresh" onClick={() => void actions.reload()}>↻ Refresh chats</button>
    </aside>
    <main className="chat-main">
      <SessionHeader controller={controller} />
      {state.loadingTranscript && <div className="chat-loading-bar" role="status">Loading transcript…</div>}
      <Transcript blocks={state.blocks} approvingId={state.approvingId} approvalBlocked={controller.approvalBlocked} hasMore={state.hasMore} onLoadOlder={() => void actions.loadOlder()} onDecision={(id, decision) => void actions.approve(id, decision)} />
      {state.error && <div className="chat-error-banner" role="alert"><span>{state.error}</span>{state.retryable && state.pendingTurn && <button onClick={() => void actions.retry()}>Retry same message</button>}{state.errorCode === "cancel_outcome_unknown" && <button onClick={() => void actions.stop()}>Retry stop</button>}{state.errorCode !== "approval_outcome_unknown" && state.errorCode !== "cancel_outcome_unknown" && state.errorCode !== "session_create_outcome_unknown" && <button className="chat-error-dismiss" onClick={actions.clearError} aria-label="Dismiss error">×</button>}</div>}
      <Composer controller={controller} />
    </main>
  </section>;
}
