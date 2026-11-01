# Web cutover implementation plan

Статус: migration-stage implementation plan для T07 web facade, SSE bridge и dynamic app UI.

Документ описывает переход от typed application contracts к HTTP routes и browser runtime. Он не
меняет `apps/api`, `apps/web`, ORM, Alembic или core/product docs. Legacy routes остаются
compatibility source до явного partition cutover. План сверяет:

- [`web-sse-bridge-contract.md`](web-sse-bridge-contract.md);
- [`dynamic-ui-implementation-contract.md`](dynamic-ui-implementation-contract.md);
- T07 facade и cross-review в [`implementation-wave-cross-review.md`](implementation-wave-cross-review.md);
- [`dynamic-grants-ownership.md`](dynamic-grants-ownership.md) и
  [`dynamic-app-migration-gaps.md`](dynamic-app-migration-gaps.md);
- typed contracts `web_facade`, `web_facade_adapter`, `web_api`;
- текущие `apps/web` API, work context, capability и dynamic-app contracts.

## 1. Target boundary

Целевая цепочка:

```text
Bearer/JWKS + active employee entitlement
  -> WebApiAuthContext + server-resolved WorkContext
  -> canonical request-context bridge/evidence
  -> web_api route adapter
  -> WebFacadeAdapter (compatibility reads/run during migration)
  -> legacy service
  -> typed DTO / RunStreamEvent
  -> SSE bridge or JSON response
  -> apps/web client, shell route, declarative renderer, isolated plugin host
```

T07 facade is an adapter, not the authorization owner. It may project legacy data and translate a
run while the legacy partition is authoritative. It must not authorize canonical grants, mutate
canonical ownership, or infer tenant/project/session from payloads. A positive mutation or dynamic
capability call is admitted only after the canonical context/grant/generation boundary exists.

The shell owns authentication session, tenant/cabinet/project/session context, route resolution,
cancellation and navigation. Dynamic apps receive only validated snapshots and typed capability
responses. A manifest's requested capabilities are never treated as a grant.

## 2. Implementation waves and ownership

Implementation uses the following exclusive boundaries. Each wave produces typed acceptance
fixtures and evidence; execution of tests/build/lint/compile remains frozen until migration-wide
verification is explicitly lifted.

### Wave A: freeze the wire and context contracts

1. Keep `web_facade.contracts` as the application DTO boundary (`CurrentUser`, `Project`,
   `ChatSidebar`, `ChatTranscript`, `ChatRunRequest`, `RunStreamEvent`). Keep `web_api` as the
   browser transport boundary; do not expose the facade `SseEventEnvelope` directly to the
   browser.
2. Define one immutable request context object for route execution, containing authenticated
   principal, company/tenant, cabinet, project, session, employee entitlement, trace/correlation,
   source of resolution, and an evidence/snapshot reference. Caller claims are retained only as
   assertions for mismatch audit.
3. Resolve route IDs and work context on the server. JWT IDs do not replace route/work context.
   `ScopeBoundary.session(project_id, session_id)` remains mandatory for transcript and run paths;
   a platform admin may address an unselected project only where policy allows, never an unrelated
   explicitly bound session.
4. Make route requirements explicit: current user, cabinet, project, session, replay cursor,
   and (for a mutation) capability, idempotency key and runtime/generation fence. Missing context
   is a typed deny, not an inferred selection.
5. Carry a non-empty trace/correlation ID through auth, legacy call, SSE envelope, audit and
   browser diagnostics. Do not claim canonical positive authorization until the shared evidence
   envelope and trusted transaction composition are available.

**Gate A:** every route can show the exact resolved tenant/cabinet/project/session and scope
before calling a legacy service; no route accepts caller-selected tenant or scope as authority.

### Wave B: HTTP route adapters

Introduce routes around `WebApiFacade`, keeping route mechanics separate from application ports:

| Route family | Required context | Adapter result |
|---|---|---|
| current user | authenticated principal/employee | validated `CurrentUserResponse` |
| projects and container | cabinet/project | validated project/status DTO |
| chat sidebar | cabinet | grouped sidebar and persistent selections |
| transcript | project + session + bounded page cursor | validated block transcript |
| chat stream | project + session + `web:chat_stream`/`web:chat_run` | SSE bridge output |
| dynamic registry | app/version + tenant/cabinet/project policy | signed registry snapshot |
| capability invoke | app instance + grant + resource context | typed operation response |

Route adapters must map failures to stable HTTP envelopes (`code`, `title`, `status`, safe
detail, optional trace ID). Recommended pre-stream mappings are `401 UNAUTHENTICATED`, `403
FORBIDDEN`, policy-dependent `404 NOT_FOUND`, `422 VALIDATION_ERROR`, `400 INVALID_LAST_EVENT_ID`
or `AMBIGUOUS_LAST_EVENT_ID`, and `409 RUN_NOT_RESUMABLE`. A route must not leak `str(exc)`, stack
traces, database identifiers, internal URLs, token material or secret values.

For stream start, `turn_id` is the client idempotency key for `(project_id, session_id, turn_id)`;
it is not, by itself, proof that the server has a durable detached-turn binding. The transport and
adapter must atomically resolve or claim that key against a persisted turn/run record, with the
same owner/generation and redacted correlation evidence used by replay. A retry must reattach/replay
the existing turn or return an explicit non-resumable response; it must not silently create a
second turn. Until that durable claim exists, the route remains compatibility/shadow-only and the
browser must not assume that a repeated POST is safe after an unknown outcome.

**Gate B:** route contract fixtures prove scope denial occurs before the legacy service call and
that JSON errors are redacted and stable.

## 3. SSE bridge and legacy translation

### 3.1 Two envelopes, one browser wire

The application event is `RunStreamEvent(type, run_id, seq, text, block, data)`. The browser wire is
`web_api.SseEventEnvelope` with `event_id`, `event_type`, `schema_version`, `occurred_at`,
`project_id`, `session_id`, `run_id`, `sequence`, `payload`, and `resume_after`. The current
contract permits nullable `session_id`, `run_id`, `sequence` and `resume_after`; canonical
replayable run events must tighten these to the authorized context and valid run sequence before
serialization. Null identity is permitted only for an explicitly non-replayable terminal protocol
error, never a normal event that advances the cursor.

The bridge serializes every canonical browser event as:

```text
id: <event_id>
event: <event_type>
data: <JSON envelope>

```

`event_id` is UUIDv5 of `prodavan:web-run:{run_id}:{sequence}`. The same canonical `(run_id,
sequence)` must therefore produce the same ID after reconnect. This alone does not make the full
envelope replay-identical: current `SseEventEnvelope.from_facade_event()` assigns `occurred_at` at
conversion time. For persisted replay, `occurred_at` must come from stable event evidence (or be
explicitly documented as delivery-time metadata); it must not be mistaken for part of the stable
idempotency key. `resume_after` is informational sequence data; the next `Last-Event-ID` is the
event ID, never `resume_after` alone.

### 3.2 Legacy frame normalizer

Normalize a legacy mapping before validating it as a `RunStreamEvent`:

| Legacy type | Application event | Browser event |
|---|---|---|
| `start`, `started` | `run_started` | `run_started` |
| `text_delta`, `delta` | `text_delta` | `text_delta` |
| `block` | `block` | `transcript_block` |
| `tool_call` | `tool_call` | `tool_call` |
| `tool_result` | `tool_result` | `tool_result` |
| `done`, `complete` | `run_completed` | `done` |
| `error`, `_error` | `run_failed` | `error` |
| `cancel` | `run_cancelled` | `status` |
| `keepalive` | `heartbeat` | `keepalive` |

The type is read from `raw.type`, then `raw.event`, then `data.type`; the control alias is removed
from `data`. `text`, `block`, `run_id`, and `seq` may be top-level or inside `data`; `ChatBlock` is
validated before serialization. Unknown types, non-mappings, invalid blocks, missing/negative/non-
numeric sequence, and invalid payloads are rejected as a whole frame.

Legacy `_session` and `_turn_complete` are control frames, not browser event names. The bridge must
map session metadata into an allowed `run_started` event and turn completion into `run_completed`,
or emit their metadata on an already valid event. If no valid run/sequence context exists, do not
invent a replayable event: before headers return `502 UPSTREAM_STREAM_MALFORMED`; after headers emit
a redacted terminal protocol error and close.

An idle `: keepalive` comment has no cursor and is not an application event. It may remain a
comment. A replayable heartbeat is allowed only when it has its own valid sequence.

### 3.3 Session and cross-run filtering

The bridge receives `project_id` and `session_id` from the authorized request context, never from
an event payload. Before serialization it verifies that the event's run belongs to that project
and session, including on an initial stream with no cursor. T07's current adapter only filters
`run_id` when a cursor supplies one; this is not proof of initial-stream membership. The legacy
`AgentEventRow` exposes session-local sequence and no per-row run identity, so until a detached-turn
run index/context binds each row to a run and project/session, the bridge cannot safely claim
canonical replay identity or cross-run filtering. Such streams remain compatibility-only and must
be denied/quarantined from canonical cutover. An event from another run/session/project is
classified as an integrity failure; it must not become an empty replay or reach the browser.

For a cursor containing a run ID:

1. resolve that run to the authorized project/session and pass `after_seq` to a run-aware tail service;
2. translate and require the same `run_id` for every delivered event, including the initial stream;
3. drop `seq <= after_seq` after translation as a second defense;
4. never apply the cursor run ID to a frame that explicitly names a different run.

The current T07 adapter passes only `after_seq` to `LegacyServices.chat_run()`, and its run filter
is conditional on a run-bearing cursor. It therefore does not establish the required run-aware tail
or initial-stream identity. A missing run ID may use `fallback_run_id` only where a trusted detached-
turn context/index proves that the legacy frame belongs to this run and authorized
project/session; a cursor alone is not such proof. A mixed-run stream is malformed. Reconnect with
a cursor from an old run must reattach to that run or return `409 RUN_NOT_RESUMABLE`; it must not
start another run and continue the old cursor. Until run identity is durable and resolvable, do not
emit deterministic UUIDv5 IDs or claim canonical replay for session-local legacy sequence.

## 4. Cursor resolver

Implement a transport-level cursor resolver above the current `parse_last_event_id` compatibility
parser. It accepts:

- legacy `run_id:sequence`;
- bare non-negative sequence only when the request is unambiguously tied to one newly created run;
- canonical UUID `event_id` only after deterministic lookup to `(run_id, sequence)` in the known
  stream/run index.

A UUID is never parsed as an integer or compared lexicographically. Unknown UUID, malformed
sequence, empty run ID, negative sequence, and ambiguous bare sequence map to typed `400` errors.
The resolver returns an exclusive lower bound. It must preserve the run identity and a resolver
source/version for audit and debugging without exposing internal storage details.

The browser parser keeps the last successfully processed `event_id`, deduplicates duplicate IDs or
sequences, and advances the cursor only after envelope validation and UI handling succeed. It
reconnects with the same `turn_id`, bounded backoff and the last cursor; it never auto-POSTs a new
turn after EOF. `run_completed`, `run_failed`, and `run_cancelled` are terminal.

The SSE parser must process `id`, `event`, multiple `data` lines joined by `\n`, CRLF, blank frame
termination, and comments. Comments update liveness only and never advance the cursor. The `event`
transport field and JSON `event_type` must both be checked for consistency.

**Gate C:** replay fixtures cover legacy cursor, UUID cursor, duplicate delivery, cross-run frame,
ambiguous bare sequence, and reconnect after a terminal event.

## 5. Error redaction and observability

Define a single redaction policy shared by route errors, stream terminal errors, plugin RPC errors,
audit events and browser diagnostics:

- public payload: stable code, safe title/detail, HTTP status where applicable, trace ID;
- server log/audit: policy-approved reason and correlation, with secret-safe metadata;
- never public or plugin-visible: access/refresh tokens, cookies, secret values, SQL, DSNs,
  internal hostnames/URLs, stack traces, raw exception text, object-store URLs, Kubernetes data,
  unbounded user prompt/content, or capability bearer material.

After SSE headers are sent, use a canonical `error` envelope with `project_id`, `session_id`,
known `run_id`/sequence and a safe payload (`code`, `title`, `detail`, `status`). Retryability is
explicit. The browser stops automatic retry for non-retryable errors and shows only the trace ID
and safe diagnostic. Malformed upstream frames are logged with a digest/classification, not raw
payloads.

**Gate D:** every error path has a stable code and redaction fixture; no exception string is copied
to a browser response or plugin message.

## 6. Dynamic app registry and route resolution

### 6.1 Registry snapshot

`apps/web` resolves `(appId, version, registered route)` from a signed registry snapshot fetched
through the authenticated API. Cache keys include app digest, tenant/cabinet/project/session scope
and schema version. Honor ETag/TTL; invalidate on disable, rollback, grant revocation, context
change and logout. A route cannot supply an arbitrary URL or version.

Registry resolution order:

1. authenticate and resolve server context;
2. verify signature/status/digest and schema protocol;
3. filter by tenant/cabinet/project and effective grant;
4. validate route context requirements;
5. load a typed view model or create an isolated plugin instance.

Unknown app type, route, protocol, signature, digest or context dimension yields an explicit
`invalid_app`, `capability_denied`, `stale_revision` or `validation_error` state. There is no
privileged hardcoded fallback.

The current `apps/web/src/dynamic-apps` registry contract is a useful client boundary for list,
detail, publish, grant and revoke, but these operations remain capability-backed and must not be
considered server authorization merely because `platform.queries` or `platform.commands` was
requested. Canonical grant ownership remains `dynamic_app_capability_grants`; the legacy JSONB
projection is read-only compatibility data during the rollback window.

### 6.2 Declarative app UI

Validate the manifest as a closed discriminated schema before React state. Keep the initial node
allowlist small and reject unknown nodes, duplicate IDs, undeclared query/action names,
expressions, embedded URLs, JavaScript, SQL, arbitrary HTML/CSS and unbounded filters. Each query
and action has typed input/output schema, required capability, scope requirement, server limits and
idempotency semantics. Validate responses before rendering.

The shell passes an immutable `RequestContext` snapshot to the declarative runtime. On cabinet,
project or session change, cancel in-flight work, discard old responses and resolve a new grant and
cache key. A `scope.active_chat: required` route is unavailable without explicit selected session;
`chats=current` data scope does not implicitly select a chat.

Typed domain operations include bounded rows/list, row patch with idempotency key, equipment
selection, and file download by `file_ref`. Never pass connection strings, arbitrary table names,
raw object URLs, secret values or generic infrastructure calls. File download returns typed metadata
and a short-lived action, not credentials.

## 7. Preview and code-plugin isolation

Preview mode is a separate capability and origin boundary, not a relaxed production route. A
preview may use a draft manifest/artifact only in a tenant-scoped, expiring preview instance with
synthetic or explicitly approved fixture data. It cannot read production session data, reuse a
production grant, invoke production mutations, or bypass schema/signature validation.

For an isolated code plugin:

- build/scan/sign and pin an immutable artifact digest before loading;
- use a dedicated unprivileged origin that shares no shell cookies, storage or service worker;
- create iframe with `sandbox="allow-scripts"`, `referrerpolicy="no-referrer"`, empty `allow` by
  default; do not accept a plugin-provided URL;
- accept `postMessage` only from the exact registered origin and exact iframe `WindowProxy`;
- bind handshake to protocol, instance nonce, plugin/version/digest and expiry;
- use opaque instance/grant handles, never raw auth tokens;
- validate closed RPC envelopes, method allowlist, request IDs, payload size and context fields;
- return only safe typed errors and cancel all requests on route/context/logout/grant change;
- deny `allow-same-origin`, forms, popups, top navigation, downloads, pointer lock and arbitrary
  network unless a reviewed capability-specific policy explicitly grants it.

Preview and production must use distinct audit event types and cache namespaces. A preview crash,
malformed message, quota violation or kill switch destroys the iframe and revokes its preview
handle without affecting the shell route or production deployment.

**Gate E:** browser fixtures demonstrate no cookie/token/DOM access, exact-origin checks, stale
instance rejection, preview data isolation, revoked grant behavior and no generic network/API path.

## 8. Capability grants and gateway cutover

The effective grant pipeline is:

```text
manifest requested capabilities
  -> signed registry/version/artifact
  -> tenant policy
  -> cabinet/project grant
  -> user role and resource ACL/RLS
  -> quota and preview/production policy
  -> expiring instance-bound effective grant
```

The gateway repeats these checks on every call, including direct call, replay, route swap and
reconnect. It derives tenant/user/scope from authenticated server context; plugin-supplied values
are assertions only. The present `apps/web` dynamic-app registry gateway maps all registry queries
to `platform.queries` and publish/grant/revoke commands to `platform.commands`; this is a client
capability boundary only and does not prove server grant identity, resource ACL, or canonical
`GrantSet.name` preservation. In particular, the persistence mapping gap for v1 capability names
and constraints remains deny/quarantine, not an implicit allow through these broad capability
names. Mutations require an idempotency key, bounded input and a typed result or stable
conflict/deny code. Audit is appended before side effects where the trusted transaction contract
requires it; currently no T07/T08 cross-domain transaction proves that composition.

Requested and effective grants remain separate. A denied or missing canonical grant is deny-by-
default. Effective grants bind app/version/digest, tenant/cabinet/project, user, instance,
capability version and expiry; they are not transferable between tabs, users or projects. On
revocation, expiry, policy update, context change or kill switch, drain and destroy affected
instances and discard late responses.

Do not promote a capability path while the following T07/T08 blockers remain unresolved:

- no shared immutable migration evidence context and snapshot reuse;
- no durable owner/installation anchor and runtime generation;
- canonical capability identity/constraint mapping cannot preserve v1 `GrantSet.name`;
- optional quarantine/audit hooks allow a negative decision without durable evidence;
- in-memory plugin handles are not restart/replay safe;
- no trusted transaction composes context, grant, audit and side effect.

## 9. Cutover and rollback protocol

Cut over by tenant plus app definition/version and, where required, cabinet/project partition:

1. inventory routes, manifests, legacy ownership, run/cursor evidence, grant and quarantine counts;
2. deploy typed route/bridge in shadow or read-only mode;
3. compare legacy and canonical DTOs, transcript blocks, event IDs, event ordering, scope decisions,
   redacted errors and grant decisions;
4. repair/quarantine mismatches; do not convert quarantine to allow;
5. freeze legacy writes or fence in-flight writers for the partition;
6. final delta/backfill and zero-unexplained-mismatch review;
7. switch one route/capability partition to canonical owner with an immutable checkpoint;
8. retain legacy readers/projection for the rollback window;
9. drain old plugin instances and reconnect streams only through the selected owner;
10. remove compatibility ownership only after evidence, retention and rollback criteria pass.

A partition is not promotable merely because DTO/event parity appears equal. Its immutable promotion
record must bind the exact tenant/app definition/version and any cabinet/project/session partition,
source snapshot/schema, source/target digests, final checkpoint, unresolved/quarantine counts,
correlation, decision owner and writer/runtime generation. Any unknown mapping, unclassified
negative, stale generation or unexplained mismatch blocks that partition. The shared evidence
context and trusted transaction composition identified by T07/T08 cross-review are prerequisites;
this web plan cannot itself establish them.

Rollback is a forward routing/ownership transition. Before canonical ownership, disable the new
path and keep legacy authoritative while retaining evidence. After canonical ownership, fence the
canonical writer generation, continue from the last valid canonical snapshot with deny-by-default,
and repair through canonical commands. Never re-enable two competing authorization or side-effect
owners and never start a compensating second turn for an unknown outcome.

## 10. Acceptance inventory and verification freeze

The implementation PRs must add focused, non-executed-during-freeze acceptance fixtures for:

- route auth, tenant/cabinet/project/session resolution and deny-before-legacy-call;
- typed DTO extra-field rejection and response validation;
- legacy aliases, `_session`, `_turn_complete`, `_error`, malformed frames and redaction;
- `run_id:sequence`, UUID and ambiguous cursor resolution;
- exclusive replay, duplicate IDs, cross-run/session/project filtering and terminal reconnect;
- full SSE framing (`id/event/data/comments/CRLF/multiline data`);
- turn idempotency and no duplicate detached run;
- manifest/schema/registry signature/digest/route rejection;
- declarative unknown-node/expression/generic-endpoint rejection;
- grant requested-versus-effective separation, expiry/revoke, direct/replayed deny and quotas;
- iframe exact-origin/source/nonce/RPC checks, no cookie/token/DOM/network access;
- preview fixture/data/cache/audit isolation and kill-switch cleanup;
- context change cancellation and cache separation.

During this migration task, tests, browser tests, build, lint, formatting, compilation, Alembic,
shared-environment changes and git verification are intentionally not run. Formal verification may
start only after implementation evidence is complete, cross-review is repeated, all unresolved
ownership/quarantine blockers have an explicit decision, and the parent migration owner lifts the
freeze.

## 11. Current blockers and handoff

1. `web_facade_adapter` currently accepts `WebRequestContext` and typed scopes but no canonical
   owner/tenant evidence, capability context, runtime generation or durable correlation snapshot.
   `WebApiAuthContext` carries principal, employee and work context, but its current
   `to_facade_context()` does not carry a request `session_id`; transport wiring must set and verify
   that route session before `ScopeBoundary.session()` and cannot rely on JWT claims.
2. The facade envelope uses `run_id:seq`, while `web_api` defines UUIDv5 browser IDs; the bridge
   must keep these application and wire envelopes separate. The current legacy event row/public
   mapping has session-local seq and no per-row run binding, so UUIDv5 browser identity is not
   currently evidence-backed for legacy replay. Do not enable canonical replay IDs until a durable
   run mapping establishes `(project, session, run, sequence)`.
3. Current legacy parser rejects `_session`, `_turn_complete` and `_error` as unknown types, and
   bare terminal `error` lacks sequence; translation needs explicit run/sequence policy. The SSE
   bridge contract does not authorize synthesizing a replayable sequence: absent trusted persisted
   run/sequence context, return pre-header `502 UPSTREAM_STREAM_MALFORMED` or a redacted
   non-replayable terminal protocol error after headers.
4. Legacy event rows do not reliably carry run identity, so safe UUID resolution and multi-run
   filtering require a run index/context, not a guessed session ID.
5. Current web `runChat` parser reads `data:` only, splits frames on `\n\n`, does not preserve
   transport `id`/`event`, and treats CRLF, comments and a final unterminated frame incompletely; it
   needs a standards-complete parser and cursor commit-after-processing behavior. It must not
   advance a cursor from an unvalidated transport frame.
6. `WebApiFacade.run_chat` does not yet expose a separate transport cursor parameter and has no
   explicit idempotent turn lookup/claim contract; wiring must propagate `Last-Event-ID` and
   durable `(project, session, turn_id) -> run_id` resolution without moving authorization into the
   parser. `WebApiScope.CHAT_STREAM` and adapter `CHAT_RUN` are separate required checks for the
   transport and application boundary; check both before legacy service invocation, including
   reconnect/replay. The adapter's current cursor parser only accepts `run_id:sequence` or bare
   sequence and raises `ValueError`; transport must map malformed/ambiguous forms to stable 400
   errors and resolve UUIDs only through a known run/event index.
7. Dynamic-app v1 protocol and current canonical persistence shape do not yet prove a durable
   capability-name/constraint mapping; positive grant import and authorization remain blocked.
8. Current capability host transport permits wildcard origin by default; production plugin and
   preview paths require exact origin/source and dedicated isolation policy.
9. Existing `WorkContext`/React state does not by itself prove immutable, server-resolved
   tenant/cabinet/project/session context; shell context must be treated as UX input until the
   server bridge resolves and rechecks it.

No code, core documentation, ORM, migration or shared environment is changed by this plan.
