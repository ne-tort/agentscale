# Seed prompts (electronics-procurement)

При установке pack копируются в `tenants/{tid}/cabinets/{cid}/prompts/`:

- `AGENTS.md` — master (источник: Commerce `AGENTS.md`, адаптированный под Prodavan MCP namespaces)
- `kp/` — модули профиля kp (источник: Commerce `profiles/kp/*.md`)

Файлы **скопированы** из Commerce (2026-08-20). Seed-installer (M00/I2) копирует as-is, затем BL-07: адаптация `AGENTS.md` (убрать Telegram, `/кп` → UI export).

Mapping: [`docs/08-migration/commerce-semantics.md`](../../../docs/08-migration/commerce-semantics.md).
