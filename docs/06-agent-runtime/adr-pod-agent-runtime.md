# ADR: Single-container Pod agent-runtime

| Status | accepted (as-built phase) |
|--------|-----------------------------|
| Date | 2026-09-01 |

## Context

Project Pod раньше использовал `sandbox` (`sleep infinity`) + sidecar `agent-bridge`. Секреты попадали в env/send body; FS шёл через `kubectl exec`.

## Decision

1. **Один контейнер** `agent-runtime` (`ghcr.io/ne-tort/prodavan-agent-runtime`) — agent loop, bridge HTTP, workspace FS, credential store in-memory.
2. **Init `hydrate`** — по-прежнему из `prodavan-api` image; материализует `/workspace` из object store.
3. **Credential broker** — API создаёт lease и пушит `POST /v1/credentials/leases` в runtime; read-back запрещён; named keys по `key_id`.
4. **Workspace** — `GET/POST/DELETE /v1/workspace/*` на runtime; API использует `HttpAgentRuntimeWorkspaceAdapter` вместо exec по умолчанию при `POD_AGENT_RUNTIME_ENABLED=true`.
5. **Agent BC** — `AgentSession` + sidechain (subagent scope в bridge); chat UI — projection transcript, не отдельная сущность Chat.

## Consequences

- Legacy alias env: `POD_AGENT_BRIDGE_*` → `POD_AGENT_RUNTIME_*` (`AliasChoices` в settings).
- Без runtime image — stub holder (`sleep infinity`) для dev без образа.
- HITL: API forwards `resolve_approval` в runtime; synthetic events только при недоступности bridge.

## References

- [`platform-openclaw-runtime.md`](platform-openclaw-runtime.md)
- [`docs/PRODUCT.md`](../PRODUCT.md) — agent workspace
- `apps/api/src/prodavan/infrastructure/k8s/sandbox/pod_spec.py`
