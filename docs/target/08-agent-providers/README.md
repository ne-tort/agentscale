# 08 — Agent providers

Как запускать ИИ-агентов в Prodavan: SDK capabilities, обёртка, permissions, модели, метрики, Admin control.

**В scope:** Cursor, Codex, Claude Agent SDK; **Platform OpenClaw** (универсальный runtime).  
**Вне scope:** GLM / Z.ai; upstream OpenClaw as dependency; personal CLI subscriptions as runtime.

## Порядок чтения

1. [capabilities-matrix.md](capabilities-matrix.md) — что умеют SDK  
2. [platform-openclaw-runtime.md](../../06-agent-runtime/platform-openclaw-runtime.md) — универсальный runtime  
3. [wrapping.md](wrapping.md) — как обернуть в порт  
4. [permissions-policy.md](permissions-policy.md) — FS/shell/network/MCP  
5. [models-and-routing.md](models-and-routing.md) — выбор моделей  
6. [usage-metrics.md](usage-metrics.md) — токены, $, лимиты  
7. [admin-control-plane.md](admin-control-plane.md) — рычаги Admin UI  
8. [adapter-port.md](adapter-port.md) — `AgentProviderPort` + events  
9. [workspace-context.md](workspace-context.md) — AGENTS/skills/rules/MCP files  
10. Вердикты: [cursor](verdict-cursor.md) · [codex](verdict-codex.md) · [claude](verdict-claude.md)  
11. [analysis.md](analysis.md) — краткое сравнение  
12. [vendor-docs/](vendor-docs/) — локальные снимки официальных доков  

Связь: [02-ai-provider-keys](../02-ai-provider-keys/), [06-projects-runtime](../06-projects-runtime/), [01-platform-admin](../01-platform-admin/).
