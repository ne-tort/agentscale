# Seed prompts (electronics-procurement)

При установке pack копируются в `tenants/{tid}/cabinets/{cid}/prompts/`:

- `AGENTS.md` — master (источник: Commerce `AGENTS.md`, адаптированный под Prodavan MCP namespaces)
- `kp/` — модули профиля kp (источник: Commerce `profiles/kp/*.md`)

Файлы добавляются при реализации seed-installer (M00). Документация mapping: [`docs/08-migration/commerce-semantics.md`](../../../docs/08-migration/commerce-semantics.md).
