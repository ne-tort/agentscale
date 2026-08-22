# Вердикт: Cursor

## Итог

| Вопрос | Ответ |
|--------|-------|
| Есть ли SDK уровня «агент в процессе»? | **Да** — `@cursor/sdk` |
| Можно ли обернуть как в Prodavan bot? | **Да — уже сделано** |
| CLI-only? | Нет; SDK — основной программный путь |

## Что это

Официальный SDK Cursor для программного создания/resume агента, стрима событий, MCP, sandbox options.

Пакет: `@cursor/sdk` (npm). В Commerce: `bot/src/core/orchestrator/cursorSdkRuntime.ts` → `Agent.create` / `Agent.resume`.

## Auth / billing

- `CURSOR_API_KEY` (или эквивалент из AiProviderKey с `api_kind=cursor_sdk`).
- Привязка к компаниям — через bindings модуля 02.

## Рекомендация Prodavan

| Роль | Primary production adapter |
|------|----------------------------|
| `api_kind` | `cursor_sdk` |
| Adapter | `CursorSdkAdapter` |
| Статус | **Готов к platform port** (перенос логики с bot → project container) |

Глубокий разбор: [capabilities-matrix](capabilities-matrix.md) · [wrapping](wrapping.md) · [vendor-docs/cursor](vendor-docs/cursor/).

## Риски

- Зависимость от Cursor cloud / квот.
- Версии SDK нужно пинить (как в bot `package.json`).
- Paths skills/rules — см. [workspace-context.md](workspace-context.md).
