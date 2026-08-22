# 08 — Agent providers

Как запускать ИИ-агентов в Prodavan: SDK capabilities, обёртка, permissions, модели, метрики, Admin control.

**В scope:** Cursor, Codex, Claude Agent SDK.  
**Вне scope:** GLM / Z.ai; personal CLI subscriptions as runtime.

## Порядок чтения

1. [capabilities-matrix.md](capabilities-matrix.md) — что умеют SDK  
2. [wrapping.md](wrapping.md) — как обернуть в порт  
3. [permissions-policy.md](permissions-policy.md) — FS/shell/network/MCP  
4. [models-and-routing.md](models-and-routing.md) — выбор моделей  
5. [usage-metrics.md](usage-metrics.md) — токены, $, лимиты  
6. [admin-control-plane.md](admin-control-plane.md) — рычаги Admin UI  
7. [adapter-port.md](adapter-port.md) — `AgentProviderPort` + events  
8. [workspace-context.md](workspace-context.md) — AGENTS/skills/rules/MCP files  
9. Вердикты: [cursor](verdict-cursor.md) · [codex](verdict-codex.md) · [claude](verdict-claude.md)  
10. [analysis.md](analysis.md) — краткое сравнение  
11. [vendor-docs/](vendor-docs/) — локальные снимки официальных доков  

Связь: [02-ai-provider-keys](../02-ai-provider-keys/), [06-projects-runtime](../06-projects-runtime/), [01-platform-admin](../01-platform-admin/).
