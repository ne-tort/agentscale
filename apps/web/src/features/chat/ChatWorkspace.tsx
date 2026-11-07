import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from "react";
import { useT } from "../../i18n/useT";
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

/**
 * Errors the workspace raises on its own, said in the reader's language.
 *
 * They arrive from the chat controller as codes because the controller has no
 * business knowing about locales; anything without a code is a message from the
 * server and is shown as it came.
 */
const ERROR_KEYS: Record<string, string> = {
  cancel_outcome_unknown: "chat.errorStopUnknown",
  approval_outcome_unknown: "chat.errorApprovalUnknown",
  session_create_outcome_unknown: "chat.errorSessionUnknown",
  attachment_list_failed: "chat.errorAttachmentsList",
  attachment_upload_outcome_unknown: "chat.errorUploadUnknown",
  attachment_upload_outcome_ambiguous: "chat.errorUploadAmbiguous",
};

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
  const t = useT();
  return <nav className="chat-tree" aria-label={t("chat.treeLabel")}>
    <div className="chat-tree__heading"><span>{t("chat.projects")}</span><span className="chat-tree__count">{projects.length}</span></div>
    {projects.length === 0 && <p className="chat-empty-hint">{t("chat.projectsEmpty")}</p>}
    {projects.map((project) => <section className="chat-tree__project" key={project.project_id}>
      <div className="chat-tree__project-row">
        <span className="chat-tree__project-name" title={project.project_name}>▾ {project.project_name}</span>
        <button className="chat-icon-button" onClick={() => onNew(project.project_id)} disabled={!project.new_chat_enabled || newChatBlocked} aria-label={t("chat.newChatIn", { project: project.project_name })} title={project.new_chat_enabled ? t("chat.newChat") : t("chat.newChatBlocked")}>＋</button>
      </div>
      <div className="chat-tree__sessions">
        {project.chats.length === 0 && <span className="chat-tree__muted">{t("chat.noChats")}</span>}
        {project.chats.map((chat) => <button key={chat.session_id} className={`chat-tree__session ${chat.session_id === selectedSessionId ? "is-selected" : ""}`} onClick={() => onSelect(chat.session_id)}>
          <span className="chat-tree__session-title">{chat.pinned && <span aria-label={t("chat.pinned")}>★ </span>}{chat.title || t("chat.untitledChat")}</span>
          <span className="chat-tree__session-meta">{chat.has_draft ? t("chat.draft") : chat.last_message_at ? formatTime(chat.last_message_at) : t("chat.newChatMark")}</span>
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
  const t = useT();
  const [open, setOpen] = useState(false);
  const title = block.name || (block.kind === "tool_result" ? t("chat.toolResultTitle") : block.kind === "tool_progress" ? t("chat.toolProgressTitle") : t("chat.toolActivity"));
  const body = block.kind === "tool_call" ? block.input : block.output ?? block.message;
  return <article className={`chat-tool chat-tool--${block.kind}`}>
    <button type="button" className="chat-tool__summary" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      <span className="chat-tool__dot" aria-hidden="true" />
      <span className="chat-tool__title">{title}</span>
      <span className="chat-tool__status">{block.kind === "tool_result" ? (block.is_error ? t("chat.toolStatusFailed") : t("chat.toolStatusDone")) : block.kind === "tool_progress" ? t("chat.toolStatusWorking") : t("chat.toolStatusCalled")}</span>
      <span aria-hidden="true">{open ? "⌃" : "⌄"}</span>
    </button>
    {open && <pre className="chat-tool__details">{blockText(body)}</pre>}
  </article>;
}

function ApprovalBlock({ block, busy, blocked, onDecision }: { block: ChatBlock; busy: boolean; blocked: boolean; onDecision: (id: string, decision: "approve" | "deny") => void }) {
  const t = useT();
  const id = block.approvalId ?? block.id;
  return <article className="chat-approval" aria-label={t("chat.approvalRequestLabel")}>
    <div className="chat-approval__icon" aria-hidden="true">!</div>
    <div className="chat-approval__body">
      <strong>{t("chat.approvalRequired", { tool: block.name || t("chat.toolFallback") })}</strong>
      {block.reason && <p>{block.reason}</p>}
      <pre>{blockText(block.input)}</pre>
      <div className="chat-approval__actions">
        <button type="button" className="chat-button chat-button--primary" disabled={busy || blocked} onClick={() => onDecision(id, "approve")}>{busy ? t("common.saving") : blocked ? t("chat.refreshToConfirm") : t("chat.allow")}</button>
        <button type="button" className="chat-button" disabled={busy || blocked} onClick={() => onDecision(id, "deny")}>{t("chat.deny")}</button>
      </div>
    </div>
  </article>;
}

function Transcript({ blocks, approvingId, approvalBlocked, hasMore, onLoadOlder, onDecision }: { blocks: ChatBlock[]; approvingId?: string; approvalBlocked: boolean; hasMore: boolean; onLoadOlder: () => void; onDecision: (id: string, decision: "approve" | "deny") => void }) {
  const t = useT();
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [blocks.length, blocks.at(-1)?.text]);
  return <div className="chat-transcript" aria-live="polite">
    {hasMore && <button type="button" className="chat-history-button" onClick={onLoadOlder}>{t("chat.loadOlder")}</button>}
    {blocks.length === 0 && <div className="chat-welcome"><span className="chat-welcome__mark">✦</span><h2>{t("chat.welcomeTitle")}</h2><p>{t("chat.welcomeHint")}</p></div>}
    {blocks.map((block) => {
      if (block.kind === "user") return <article className="chat-message chat-message--user" key={block.id}><div className="chat-message__bubble"><MarkdownText text={block.text ?? ""} />{block.attachments?.length ? <AttachmentList attachments={block.attachments} compact /> : null}</div></article>;
      if (block.kind === "assistant") return <article className="chat-message chat-message--assistant" key={block.id}><div className="chat-avatar" aria-hidden="true">✦</div><div className="chat-message__content"><MarkdownText text={block.text ?? ""} />{block.raw?.at && <time>{formatTime(block.raw.at)}</time>}</div></article>;
      if (block.kind === "thinking") return <article className="chat-thinking" key={block.id}><span className="chat-thinking__spinner" aria-hidden="true" />{block.text || t("chat.thinking")}</article>;
      if (block.kind === "approval") return <ApprovalBlock key={block.id} block={block} busy={approvingId === block.approvalId} blocked={approvalBlocked} onDecision={onDecision} />;
      if (["tool_call", "tool_result", "tool_progress"].includes(block.kind)) return <ToolBlock key={block.id} block={block} />;
      if (block.kind === "status") return <div className="chat-status-line" key={block.id}><span className="chat-status-line__spinner" aria-hidden="true" />{block.message || block.phase || t("chat.working")}</div>;
      if (block.kind === "error") return <div className="chat-inline-error" key={block.id}>{block.message || t("chat.agentError")}</div>;
      return <div className="chat-event-line" key={block.id}>{block.message}</div>;
    })}
    <div ref={endRef} />
  </div>;
}

function AttachmentList({ attachments, compact = false, onRemove }: { attachments: ChatAttachment[]; compact?: boolean; onRemove?: (id: string) => void }) {
  const t = useT();
  return <div className={`chat-attachments ${compact ? "chat-attachments--compact" : ""}`} aria-label={t("chat.attachments")}>
    {attachments.map((attachment) => <span className="chat-attachment" key={attachment.id}><span aria-hidden="true">▧</span><span>{attachment.filename}</span>{onRemove && <button type="button" onClick={() => onRemove(attachment.id)} aria-label={t("chat.removeAttachment", { name: attachment.filename })}>×</button>}</span>)}
  </div>;
}

function Composer({ controller }: { controller: ChatWorkspaceController }) {
  const t = useT();
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
    <textarea aria-label={t("chat.messageLabel")} value={state.draft} onChange={(event) => actions.saveDraft(event.target.value)} onKeyDown={onKeyDown} placeholder={t("chat.messagePlaceholder")} disabled={state.status === "stopping"} rows={3} />
    <div className="chat-composer__toolbar">
      <div className="chat-composer__tools">
        <input ref={fileInputRef} type="file" className="chat-visually-hidden" onChange={onFileChange} />
        <button type="button" className="chat-button chat-button--quiet" onClick={() => fileInputRef.current?.click()} disabled={busy || state.errorCode === "attachment_upload_outcome_unknown"} aria-label={t("chat.attachFile")}>⌕ <span>{t("chat.attach")}</span></button>
        <label className="chat-model"><span className="chat-visually-hidden">{t("chat.model")}</span><select value={state.model ?? "Auto"} onChange={(event) => actions.setModel(event.target.value)} disabled={busy}>{state.modelOptions.map((model) => <option value={model} key={model}>{model}</option>)}</select></label>
      </div>
      {busy && state.status !== "stopping" ? <button type="button" className="chat-button chat-button--stop" onClick={() => void actions.stop()} aria-label={t("chat.stopGenerating")}>■ <span>{t("chat.stop")}</span></button> : <button type="submit" className="chat-send" disabled={!state.draft.trim() && !state.attachments.length} aria-label={t("chat.sendMessage")}>↑</button>}
    </div>
    <p className="chat-composer__hint">{t("chat.composerHint")}</p>
  </form>;
}

function SessionHeader({ controller }: { controller: ChatWorkspaceController }) {
  const t = useT();
  const { state, actions } = controller;
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(state.sessionTitle ?? t("chat.newConversation"));
  useEffect(() => setTitle(state.sessionTitle ?? t("chat.newConversation")), [state.sessionTitle, t]);
  const saveTitle = () => { setEditing(false); void actions.patchSession({ title: title.trim() || null }); };
  return <header className="chat-session-header">
    <div className="chat-session-header__title">
      {editing ? <input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} onBlur={saveTitle} onKeyDown={(event) => { if (event.key === "Enter") saveTitle(); if (event.key === "Escape") setEditing(false); }} aria-label={t("chat.chatTitleLabel")} /> : <button className="chat-title-button" onClick={() => setEditing(true)}>{state.sessionTitle || (state.selectedSessionId ? t("chat.conversation") : t("chat.newConversation"))} <span aria-hidden="true">✎</span></button>}
      <span className={`chat-session-status chat-session-status--${state.status}`}>{state.status === "streaming" ? t("chat.statusWorking") : state.status === "error" ? t("chat.statusNeedsAttention") : t("chat.statusReady")}</span>
    </div>
    {state.selectedSessionId && <button className={`chat-pin-button ${state.sessionPinned ? "is-pinned" : ""}`} onClick={() => void actions.patchSession({ pin: !state.sessionPinned })} aria-pressed={state.sessionPinned} aria-label={state.sessionPinned ? t("chat.unpinChat") : t("chat.pinChat")}>★</button>}
  </header>;
}

export type ChatWorkspaceProps = {
  controller: ChatWorkspaceController;
  onNewChat?: (projectId: string) => void;
};

export function ChatWorkspace({ controller, onNewChat }: ChatWorkspaceProps) {
  const t = useT();
  const { state, actions } = controller;
  const [treeOpen, setTreeOpen] = useState(false);
  const selectedProjectName = state.projects.find((project) => project.project_id === state.projectId)?.project_name ?? t("chat.projectChat");
  const errorKey = state.errorCode ? ERROR_KEYS[state.errorCode] : undefined;
  const errorText = errorKey ? t(errorKey) : state.error;
  const newChat = (nextProjectId: string) => {
    if (controller.sessionCreateBlocked) return;
    if (onNewChat) onNewChat(nextProjectId);
    else void actions.startNewChat(nextProjectId);
  };
  return <section className="chat-workspace" aria-label={t("chat.workspaceLabel")}>
    <button className="chat-mobile-tree-toggle" onClick={() => setTreeOpen((open) => !open)} aria-expanded={treeOpen}>☰ <span>{selectedProjectName}</span></button>
    <aside className={`chat-sidebar ${treeOpen ? "is-open" : ""}`}>
      <div className="chat-sidebar__top"><div><p className="chat-eyebrow">{t("chat.workspaceEyebrow")}</p><h1>{t("chat.title")}</h1></div><button className="chat-new-button" onClick={() => newChat(state.projectId)} disabled={controller.sessionCreateBlocked} aria-label={t("chat.newChat")}>＋ {t("chat.new")}</button></div>
      {state.loadingSidebar ? <div className="chat-sidebar__skeleton" aria-label={t("chat.loadingChats")}><span /><span /><span /></div> : <ChatTree projects={state.projects} selectedSessionId={state.selectedSessionId} newChatBlocked={controller.sessionCreateBlocked} onSelect={(sessionId) => { setTreeOpen(false); void actions.selectSession(sessionId); }} onNew={newChat} />}
      <button className="chat-sidebar__refresh" onClick={() => void actions.reload()}>↻ {t("chat.refreshChats")}</button>
    </aside>
    <main className="chat-main">
      <SessionHeader controller={controller} />
      {state.loadingTranscript && <div className="chat-loading-bar" role="status">{t("chat.loadingTranscript")}</div>}
      <Transcript blocks={state.blocks} approvingId={state.approvingId} approvalBlocked={controller.approvalBlocked} hasMore={state.hasMore} onLoadOlder={() => void actions.loadOlder()} onDecision={(id, decision) => void actions.approve(id, decision)} />
      {errorText && <div className="chat-error-banner" role="alert"><span title={state.error ?? undefined}>{errorText}</span>{state.retryable && state.pendingTurn && <button onClick={() => void actions.retry()}>{t("chat.retryMessage")}</button>}{state.errorCode === "cancel_outcome_unknown" && <button onClick={() => void actions.stop()}>{t("chat.retryStop")}</button>}{state.errorCode !== "approval_outcome_unknown" && state.errorCode !== "cancel_outcome_unknown" && state.errorCode !== "session_create_outcome_unknown" && <button className="chat-error-dismiss" onClick={actions.clearError} aria-label={t("chat.dismissError")}>×</button>}</div>}
      <Composer controller={controller} />
    </main>
  </section>;
}
