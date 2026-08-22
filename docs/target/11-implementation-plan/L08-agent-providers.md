# L08 — Agent providers

## Цель

Стабильный `AgentProviderPort`, нормализованные `AgentEvent`, adapters (Cursor primary), usage persistence, tool/policy wrapping. Без GLM/OpenClaw; без `cli_subscription` как credential.

## Канон

- [08-agent-providers/](../08-agent-providers/) — особенно:
  - [adapter-port.md](../08-agent-providers/adapter-port.md)
  - [wrapping.md](../08-agent-providers/wrapping.md)
  - [permissions-policy.md](../08-agent-providers/permissions-policy.md)
  - [capabilities-matrix.md](../08-agent-providers/capabilities-matrix.md)
  - [models-and-routing.md](../08-agent-providers/models-and-routing.md)
  - [usage-metrics.md](../08-agent-providers/usage-metrics.md)
  - [admin-control-plane.md](../08-agent-providers/admin-control-plane.md)
- Credentials: L03 resolve

## Зависимости

| Нужно | Даёт |
|-------|------|
| L03 resolve; L07 cwd/mcp (для close); policy from L04 | Streaming agent turns + persisted events/usage |

**Изоляция:** adapter + port + event schema на **fixture workspace** — полная реализация до L07; интеграция — после.

## Контракты (публикует)

| Контракт | Описание |
|----------|----------|
| `AgentProviderPort` | create/resume/send/cancel/close/(getUsage) |
| `AgentEvent` frozen schema | text_delta, tool_*, usage, error, done, … |
| Sidecar protocol | Node (или принятый) ↔ API; не Telegram bot |
| Usage records | → Admin/Company metrics |
| HITL | `tool_approval_request` → DangerConfirm / full-page approve |
| Policy apply | toolPolicy + sandbox/network из permissions-policy |

## DoD

- [ ] Port interface стабилен; contract tests с fake adapter.
- [ ] Cursor adapter: create/send/stream/cancel на fixture; resume если SDK умеет.
- [ ] Все обязательные event types эмитятся; **usage** когда vendor отдаёт.
- [ ] Events persist (replay UI / audit).
- [ ] Resolve credential only via L03; ban cli_subscription test.
- [ ] MCP servers list = materialize ∩ policy.
- [ ] Codex/Claude: минимум stub adapter или phased — **отметить в contracts-index**; Cursor не «почти».
- [ ] Budget/cancel paths.

## Не считать готовым, если…

- Прямой вызов SDK из Flutter.
- События только `text` без schema.
- Usage не пишется → Admin metrics пустые навсегда.
- OpenClaw/GLM «временный» backend.
- Один happy-path без cancel/error events.

## Exit gate

Fake + Cursor fixture tests; event golden JSON; L09 может подписать chat UI на stream.
