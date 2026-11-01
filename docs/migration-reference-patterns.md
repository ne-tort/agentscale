# Migration Reference Patterns: AI-agent platforms

Дата среза: 2026-11-01. Документ предназначен для миграционного и архитектурного сравнения, а не для копирования UX конкретного вендора. Внешние материалы ниже являются официальной документацией/репозиториями; доступность отдельных страниц и набор функций могут меняться.

## 1. Контекст Prodavan

Prodavan — SaaS управления изолированными Pod'ами через UI: пользователь выбирает tenant/cabinet/project, проект получает рабочий Pod, а внутри него живёт агент с файлами, MCP/tools и долгоживущими сессиями. Каноническая модель описана в [PRODUCT.md](PRODUCT.md): Postgres является SoT транскрипта, MinIO — durable workspace, Pod — runtime isolation, а module bindings materialize prompts/files/MCP в workspace.

Локальные контракты, с которыми нужно сверять миграцию:

- [PRODUCT.md](PRODUCT.md): multi-chat, streaming transcript blocks, reload/rehydration, attachments, subagents, module scopes and lifecycle.
- [mcp-gateway.md](02-architecture/mcp-gateway.md): session JWT, tenant/cabinet/project claims, capability + profile ACL, audit envelope and rate limits.
- [platform-extensibility.md](02-architecture/platform-extensibility.md): core vs cabinet pack vs domain plugin, signed registry, version pinning and no arbitrary code in agent Pods.
- [multi-tenancy.md](02-architecture/multi-tenancy.md): tenant → cabinet → project isolation, RLS and explicit scope columns.
- [cabinet-profiles.md](02-architecture/cabinet-profiles.md): declarative UI/capability/MCP/seed manifest.

Главный вывод: Prodavan уже ближе к workspace/runtime platform, чем к chat wrapper. При миграции нельзя свести его к одной сущности `conversation` или к workflow canvas без потери изоляции, durability и доменных модулей.

## 2. Сводная карта платформ

| Платформа | Основная UI-модель | Сессии/состояние | MCP / tools | Файлы / runtime | Automations / webhooks | Approvals / traces | Plugin / tenant модель |
|---|---|---|---|---|---|---|---|
| OpenAI Agents / Responses | Hosted Agent API плюс Agents/SDK surface; API docs разделяют agent config, sessions, environments, tools and multi-agent | Sessions/events/items, continuation, background runs; Responses conversation state | Built-in tools, functions, MCP connections, plugins/vaults | Hosted/self-hosted sandboxes, lifecycle, files/artifacts | Scheduled automations and webhooks are first-class session concerns | Event/item stream and run lifecycle; approval is a tool/policy concern | Provider-hosted resources; application owns tenant mapping and authorization |
| Anthropic Claude Code | Terminal/IDE-oriented agent view with project configuration; less of a generic SaaS tenant console | Local/project conversation context, subagents and resumable work; SDK integration needed for SaaS durability | MCP server config, per-project/user scopes; tool permissions | Local workspace and coding tools; runtime controlled by host | Hooks/automation are host integration patterns rather than a unified workflow SaaS | Permission prompts, hooks and tool result stream; external observability needed | Settings/plugins/MCP distributed across user/project/org scopes |
| LangGraph + LangSmith | Developer graph/runtime API; Studio/trace UI is operational, not an end-user product shell | Checkpointer = thread-scoped graph snapshots; Store = cross-thread durable memory; interrupts resume state | Arbitrary nodes/tools; MCP is composition layer, not core tenancy model | Runtime is application/server-owned; Agent Server handles persistence | App/server scheduler and webhooks, not a single opinionated automation UI | `interrupt()` gives durable HITL; LangSmith gives trace/run hierarchy and evaluation | Graph code/deployment owned by developer; tenant boundaries must be imposed by app |
| Dify | Workspace app builder: Chatflow/Workflow/Agent canvas, publish/test/monitor | Conversation/app runs, logs and published app endpoints | Tool plugins, model/datasource/agent strategy plugins; MCP both consume and expose | Knowledge/files, app inputs and provider integrations | Published API/web app; triggers/webhooks depend on workflow/integration surface | Run logs/monitoring; workflow nodes can encode review/branching | Workspace/team members, app-level config, plugin marketplace; tenant boundary is workspace |
| Flowise | Visual Chatflow/Agentflow canvas with embedded chat and API | Flow execution/session IDs; persistence and deployment are app/database-owned | Nodes/integrations and MCP-related components; capability policy is app config | Attachments/vector stores and external runtime | Prediction API/webhook integrations; generic orchestrator patterns | Execution logs and human-input nodes where configured; tracing via integrations | Self-host/enterprise deployment; workspace/RBAC varies by edition |
| n8n | Workflow canvas; AI Agent is a node inside a broader automation platform | Executions are durable workflow runs; chat memory is a node/resource | Tool nodes, credentials and AI Agent tool graph; MCP integrations available | Binary data, credentials, external stores; no mandatory per-agent sandbox | Webhook/trigger/schedule/queue execution is core | Execution inspector, retries/error branches, human fallback/approval patterns | Projects, credentials, RBAC and instance/workspace boundaries |
| Relevance AI | Agents, tools, workforces and triggers in a hosted operations UI | Agent/task history and state are platform-managed | Tools/workforces/integrations, typically configured in platform | Hosted tool execution and data connectors | Triggers, schedules and API/webhook entry points are central | Task/run history and human escalation patterns | Workspace/team-oriented SaaS with managed tool catalog |
| LibreChat | Chat shell with agent builder, projects, skills, scheduled chats and admin panel | Conversations plus resumable streams; agent config is reusable | Per-chat MCP palette or agent tool library, tool selection and OAuth status | File/RAG/artifacts/code interpreter integrations | Scheduled chats and Agents API; webhook/host integration varies | Tool status/errors and message-level event UI; external tracing usually needed | User/org/admin ACL; per-user MCP credentials and server visibility |
| AnythingLLM | Workspace-centric chat UI; Agent Flows/AI Agents and embedded widgets | Workspace/chat history, agent task/event logs | Agent tools, browser, RAG and integrations; MCP depends on version/deployment | Workspace documents, vector DB, local/self-hosted execution | API/widget and agent flow invocation; scheduler is less central | Event logs and agent steps; approval model is lighter than workflow systems | Workspaces, multi-user/security and admin controls; plugin/community ecosystem |

## 3. Устойчивые профессиональные паттерны

### 3.1 Разделяйте Session, Run, Event и Projection

Платформы сходятся на том, что session/thread — стабильный контейнер контекста, а run — конкретное выполнение. Внутри run нужен append-only event/item stream: model output, tool call/result, subagent, approval, retry, cancellation, usage and final answer. UI transcript — projection этого журнала, а не единственная форма хранения.

LangGraph делает это явным через thread checkpointer и resume после interrupt. OpenAI Agents docs отдельно выделяют sessions, events/items, manage sessions, automations and webhooks. Это подтверждает локальное решение Prodavan хранить `agent_events` в Postgres, показывать block/turn projection и поддерживать rehydrate после Pod recreation.

**Миграционный контракт:**

```text
tenant/cabinet/project
  -> agent_profile (versioned)
  -> session (durable context)
  -> run (one user turn / trigger execution)
  -> events (append-only, idempotent, ordered)
  -> transcript/metrics/audit projections
```

Не смешивать `session_id` с `run_id`: один чат имеет много runs; один automation trigger может создать отдельный run в существующей session или новую session по policy.

### 3.2 Session state имеет два горизонта

Короткая память должна быть thread/session scoped; долгосрочные preferences/facts — tenant/user/project scoped. LangGraph прямо разделяет checkpointer (single thread) и store (cross-thread). В Prodavan аналогично нужны отдельные retention и ACL для transcript, workspace files, profile data и module rows.

Практическая схема:

- `session`: title, owner, project, agent profile version, lifecycle, selected model.
- `run`: input, trigger, status, started/finished, idempotency key, cost/usage.
- `event`: seq, kind, parent tool/subagent, payload, trace/span IDs.
- `memory`: explicit scope (`user`, `cabinet`, `project`, `session`) and retention.
- `artifact/file`: immutable content reference plus workspace path and provenance.

### 3.3 UI — shell + operational workspace, а не только чат

Профессиональные продукты имеют несколько поверхностей: project/workspace navigation, agent/profile editor, tool/MCP catalog, files/knowledge, run history, approvals, settings and admin/tenant controls. LibreChat exposes MCP palette, Agent Builder, Projects, Scheduled Chats, Skills, Artifacts and Admin; Dify separates build/publish/monitor/workspace; n8n centers workflow canvas and execution inspector.

Для Prodavan это поддерживает current UI direction: project tree + multi-chat, management/data hubs, live container status, Files, prompts/MCP tiles and module-owned chat-header actions. Новая UI-архитектура должна сохранять стабильный workspace shell и подключаемые module views, а не прятать все capabilities в composer меню.

### 3.4 MCP — capability attachment, не просто URL

MCP implementations показывают три уровня:

1. **Server registration:** transport, endpoint, auth/OAuth, health/status.
2. **Tool selection:** allowlist конкретных tools, deferred/on-demand loading, per-agent or per-chat attachment.
3. **Execution policy:** tenant/session identity, rate limits, audit, secrets, cancellation, approval and result redaction.

LibreChat позволяет включить MCP server в конкретный chat через composer, а в Agent Builder выбрать отдельные tools; соединения per-user и OAuth status отображаются в UI. Dify может публиковать приложение как MCP server и принимать MCP tools внутри app/workflow. OpenAI docs выделяют MCP connections среди tool integrations.

**Вывод для Prodavan:** оставлять Bridge/Gateway единственной точкой Pod → platform. `mcp.json`/profile — декларативный desired state, но runtime должен выдавать session-scoped identity и проверять `tenant_id/cabinet_id/project_id/session_id`, capability + profile ACL на каждый invoke. Нельзя давать агенту прямой доступ к tenant secrets или произвольной сети.

### 3.5 Files — отдельный lifecycle и provenance

OpenAI Agent API docs выделяют environments/sandboxes, lifecycle, files and artifacts; AnythingLLM и Dify строят knowledge/files как отдельные workspace resources; LibreChat разделяет RAG/file upload, artifacts and code interpreter. Это общий сигнал: attachment в сообщении не равен durable workspace file.

Минимальная модель:

- upload asset: immutable blob, checksum, MIME, size, uploader and tenant scope;
- message attachment: reference to asset, not copied bytes in event payload;
- workspace materialization: explicit path, generation and source asset;
- output artifact: provenance (`run_id`, tool, parent asset), download/share ACL;
- cleanup/retention: independent from session transcript and Pod lifecycle.

Локальный `upload → MinIO inbox → hot-push в running Pod → path-prefixed prompt`, а также `hydrate/dehydrate` — правильный platform pattern; его нельзя заменять только provider file IDs.

### 3.6 Prompt profiles — versioned, scoped, materialized

У зрелых систем prompt/agent config является reusable resource: Dify app/agent, LibreChat Agent Builder, Claude Code project/user settings, OpenAI agent configuration, AnythingLLM workspace system prompt. Конфигурация должна быть отделена от run и иметь published/versioned state.

Рекомендуемый контракт для Prodavan уже близок к этому:

```text
platform/company/cabinet/project scope
  -> profile definition + revision
  -> ordered prompt fragments/files
  -> effective profile resolution
  -> immutable profile_version on each run
```

При изменении профиля старые runs остаются воспроизводимыми. `global`/`local` module binding и `project_ids` должны влиять на effective profile resolution, но не переписывать исторический snapshot.

### 3.7 Automations, tasks и webhooks — отдельный control plane

OpenAI Agents docs explicitly list scheduled automations and webhooks under sessions; n8n treats trigger/schedule/webhook/queue as first-class workflow entry points; Dify/AnythingLLM/LibreChat expose published APIs, scheduled or externally invoked surfaces. Общий паттерн — automation не является «скрытым chat message».

Нужны сущности:

- `automation`: owner/scope, trigger, enabled, agent_profile_version, input schema, concurrency policy;
- `task/run`: queued/running/waiting_approval/succeeded/failed/cancelled, retry and timeout;
- `webhook_endpoint`: secret rotation, signature verification, replay/idempotency handling;
- `delivery`: outbound webhook attempts with backoff, status and redacted payload;
- `scheduler/queue`: durable claim/lease, dead-letter and tenant quotas.

Webhook handlers должны только аутентифицировать, записать idempotency key и enqueue run; они не должны синхронно удерживать HTTP connection на время агента.

### 3.8 Approvals — durable state machine

HITL нельзя моделировать boolean `approved` на tool call. LangGraph interrupts persist graph state and resume; Claude Code uses permission prompts/hooks; LibreChat and n8n expose tool/error/human fallback patterns. Для рискованных actions нужен approval request с policy snapshot, requester, approver, expiry, decision, reason and audit event.

```text
RUNNING -> WAITING_APPROVAL -> RUNNING
                         \-> REJECTED / EXPIRED
RUNNING -> CANCEL_REQUESTED -> CANCELLED
```

Approval UI должна показывать input arguments, affected tenant/project, requested scopes, predicted side effects and links to trace/events. Повторный submit должен быть idempotent; после timeout запрещено автоматически исполнять старый approval без policy check.

### 3.9 Traces и usage — cross-cutting, не transcript

LangSmith специализируется на run hierarchy/observability; n8n — execution inspector; OpenAI/OpenAI Agents — events/items and usage; LibreChat and AnythingLLM expose event/tool history. Для platform-grade продукта trace должен связывать `trace_id → run → model call/tool call/subagent/approval`, но UI transcript может показывать только безопасную projection.

Обязательные dimensions: tenant, cabinet, project, session, run, agent profile revision, provider/model, tool server/name, status/error, latency, token/cost, artifact IDs. Payloads need redaction policy; secrets and large binary content must be references/hashes. Metrics (billing/quotas) should consume immutable facts, not scrape rendered transcript.

## 4. Сравнение решений по фокусу

### OpenAI Agents / Responses

**Что взять:** explicit hosted agent lifecycle, sessions/events/items, background execution, scheduled automations, webhooks, sandbox lifecycle and files/artifacts taxonomy. Official navigation itself separates agent config, sessions, environments, tools/MCP and multi-agent, which is a useful bounded-context map.

**Ограничение:** provider-managed primitives do not replace Prodavan tenant/RLS, Pod isolation, module bindings or domain ACL. Keep provider adapter behind `AgentProviderPort`; persist canonical events and artifacts in platform SoT.

Sources: [Agents](https://developers.openai.com/api/docs/guides/agents), [MCP/tools](https://developers.openai.com/api/docs/guides/tools-connectors-mcp), [Background mode](https://developers.openai.com/api/docs/guides/background), [file inputs](https://developers.openai.com/api/docs/guides/file-inputs), [webhooks](https://developers.openai.com/api/docs/guides/webhooks).

### Anthropic / Claude Code

**Что взять:** project/user/org configuration layers, MCP server attachment, permission prompts, hooks and subagent-oriented coding UX. Claude Code is a strong reference for an agent working against a real filesystem, where tools and permissions are visible operational capabilities rather than chat decorations.

**Ограничение:** terminal/IDE-first scope is not a tenant SaaS control plane. Add an API-side session/event model, durable queue, RLS and central audit around any SDK/CLI runtime.

Sources: [Claude Code MCP](https://code.claude.com/docs/en/mcp), [Claude Code overview](https://code.claude.com/docs/en/overview), [subagents](https://code.claude.com/docs/en/sub-agents), [agent view](https://code.claude.com/docs/en/agent-view).

### LangGraph / LangSmith

**Что взять:** checkpointer vs store split, thread_id, durable interrupt/resume, graph/subgraph namespaces and trace hierarchy. This is the clearest reference for making runs recoverable and approvals first-class.

**Ограничение:** LangGraph is a runtime/library, not a complete multi-tenant product shell. Tenant authorization, module registry, file pipeline and end-user navigation remain application responsibilities.

Sources: [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence.md), [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts), [LangSmith observability](https://docs.langchain.com/langsmith/observability).

### Dify

**Что взять:** workspace-oriented visual app builder, separate build/publish/monitor surfaces, plugin type taxonomy (tool/model/agent strategy/extension/datasource/trigger), and publishing an app as an MCP server.

**Ограничение:** app/workspace abstraction is less granular than Prodavan tenant → cabinet → project + Pod. Do not flatten local/global module SoT into one workspace JSON blob.

Sources: [Dify plugin model](https://docs.dify.ai/en/develop-plugin/getting-started/getting-started-dify-plugin), [MCP Server publishing](https://docs.dify.ai/en/cloud/use-dify/publish/publish-mcp), [Dify docs](https://docs.dify.ai/en/cloud/use-dify/getting-started/introduction), [Dify repository](https://github.com/langgenius/dify).

### Flowise

**Что взять:** visual composition/canvas as an optional authoring surface, deployable flow endpoint, separation of flow definition from invocation. It is useful for admin/operator design tooling, not as the canonical runtime model.

**Ограничение:** Flowise-style node graph alone does not provide Prodavan's Pod isolation, durable workspace, tenant ACL or domain module ownership. Keep graph execution behind the same session/run/event and Gateway contracts.

Sources: [Flowise documentation](https://docs.flowiseai.com/), [Flowise repository](https://github.com/FlowiseAI/Flowise).

### n8n

**Что взять:** trigger/schedule/webhook/queue as first-class automation entities; execution inspector; credentials separation; retries/error branches and human fallback. This is the best reference for task operations around agents.

**Ограничение:** workflow execution is not identical to an agent session. A workflow may start an agent run, wait for approval, and resume; do not make every workflow node a chat session or expose credentials to Pod.

Sources: [n8n docs](https://docs.n8n.io/), [Webhook node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/), [AI workflow tutorial](https://docs.n8n.io/advanced-ai/intro-tutorial/).

### Relevance AI

**Что взять:** operational framing of agents + tools + workforces + triggers: reusable agent definitions, managed tool catalog and task-oriented execution surface.

**Ограничение:** hosted workforce abstraction is not a substitute for explicit project workspace and artifact durability; preserve platform-owned scopes and audit.

Sources: [Relevance AI docs](https://relevanceai.com/docs), [Relevance AI platform](https://relevanceai.com/).

### LibreChat

**Что взять:** best UI reference for a chat shell that exposes MCP correctly: composer attachment, searchable server catalog, per-user OAuth/connection status, per-agent tool selection, deferred tools, Projects, Scheduled Chats, Skills, Artifacts, resumable streams and admin visibility controls.

**Ограничение:** chat-first architecture needs augmentation for Pod lifecycle, durable workspace and domain module SoT. MCP connection status must not be confused with authorization to a tenant resource.

Sources: [MCP](https://www.librechat.ai/docs/features/mcp), [Agents](https://www.librechat.ai/docs/features/agents), [Scheduled Chats](https://www.librechat.ai/docs/features/scheduled_chats), [Agent Plugins](https://www.librechat.ai/docs/features/agent_plugins), [Projects](https://www.librechat.ai/docs/features/projects).

### AnythingLLM

**Что взять:** workspace-centric files/knowledge, embedded chat/API, agent tools and event-log framing; useful for a self-hosted workspace UX and local/private deployments.

**Ограничение:** workspace is a coarser scope than tenant/cabinet/project and its approval/automation model is lighter. Keep AnythingLLM-like workspace convenience as a projection over Prodavan scopes, not a replacement.

Sources: [AI Agents](https://docs.anythingllm.com/features/ai-agents), [Agent Flows](https://docs.anythingllm.com/agent-flows/overview), [Security & Access](https://docs.anythingllm.com/features/security-and-access), [API](https://docs.anythingllm.com/features/api).

## 5. Migration decisions for Prodavan

### P0: Preserve canonical boundaries

1. Keep `tenant → cabinet → project → Pod → session → run → events` explicit in API, DB and UI routes.
2. Treat Postgres events/transcript, MinIO workspace/assets and Pod filesystem as different stores with explicit sync/rehydration contracts.
3. Keep `AgentProviderPort`; provider-specific sessions, tool IDs and file IDs are adapter metadata, never the platform primary key.
4. Make every tool call carry tenant/cabinet/project/session/run context and pass Gateway ACL/audit/rate-limit checks.

### P1: Add operational control plane

1. Introduce durable `runs`, `approvals`, `automations`, `webhook_endpoints` and `deliveries` projections around the existing agent session BC.
2. Standardize event envelope: `event_id`, `seq`, `trace_id`, `run_id`, `parent_event_id`, `kind`, `payload_ref`, `created_at`, `actor`, `redaction_version`.
3. Make background work claimable and idempotent: queue lease, retries, cancellation, dead-letter, tenant quotas.
4. Expose a unified run detail view: timeline, tool calls, subagents, approvals, artifacts, usage/cost, errors and retry/cancel actions.

### P1: Make profiles and modules reproducible

1. Version prompt/profile definitions and stamp immutable effective profile revision on every run.
2. Keep module `local/global` SoT resolution separate from project materialization; materialization must be an explicit, observable operation.
3. Extend module manifests with optional capabilities: `ui`, `tools`, `triggers`, `approvals`, `artifacts`, `metrics`; validate against schema and tenant policy.
4. Use signed/pinned packs and deny dynamic arbitrary code in Pod, consistent with [platform-extensibility.md](02-architecture/platform-extensibility.md).

### P2: Improve UI architecture

1. Keep workspace shell: project rail, session list, files, management/data hubs, agent/profile settings.
2. Add reusable side panels for MCP/tool catalog, run trace, approval queue and artifacts; avoid encoding these as chat-only bubbles.
3. Offer optional visual workflow authoring for automations, but compile it to durable task/run contracts rather than making the canvas the source of truth.
4. Use explicit states in UI: `draft`, `queued`, `running`, `streaming`, `waiting_approval`, `paused`, `succeeded`, `failed`, `cancelled`, `expired`.

## 6. Risks and non-goals

- **Vendor lock-in:** hosted session IDs, MCP connection IDs, file IDs and trace schemas must be mapped through provider adapters.
- **Scope confusion:** user-level MCP OAuth, cabinet profile ACL and project materialization are different authorization decisions.
- **Event leakage:** event payloads can contain credentials, prompts or customer data; redact before cross-tenant observability/export.
- **Duplicate execution:** webhook retries and UI refreshes must not run a side-effecting tool twice; use idempotency keys and tool policy.
- **Transcript as database:** rendered transcript cannot answer durable approval, billing, replay or audit requirements by itself.
- **Canvas as architecture:** visual workflows are an authoring UX, not a replacement for runtime contracts, queues and Pod lifecycle.

## 7. Short recommendation

Adopt a hybrid reference architecture: OpenAI/Claude-style provider adapters for model/tool execution, LangGraph-style durable checkpoint/interrupt semantics for runs and approvals, n8n-style automation control plane, LibreChat-style MCP/file UX, and Dify-style versioned app/plugin/publish surfaces. Keep Prodavan's differentiators as non-negotiable: tenant/cabinet/project isolation, Pod runtime, MinIO/Postgres durability, module SoT/materialization, Gateway-only MCP and signed extension model.
