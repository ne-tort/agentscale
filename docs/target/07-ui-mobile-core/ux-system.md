# UX system — профессиональный стандарт

Дополняет [design-rules.md](design-rules.md) и [principles.md](principles.md). Цель — SaaS-уровень, не учебный CRUD.

## Три shell'а

| Contour | IA doc | Эталон |
|---------|--------|--------|
| Admin | [01 ux-contract](../01-platform-admin/ux-contract.md) | Stripe ops |
| Company | [03 ux-contract](../03-companies/ux-contract.md) | Notion members |
| Employee | [04 ux-contract](../04-employees/ux-contract.md) | Claude / Cursor |

## Паттерны

| Паттерн | Правило Prodavan |
|---------|------------------|
| Primary surface | [EntityCollection](entity-collection.md): list (narrow) / table (wide) |
| Density | Dense rows; `AppCard` только для interaction clusters |
| Navigation | Отдельный shell + bottom nav / rail; sticky context в слоте AppBar |
| Empty | Laconic: короткий факт + noun-label действия («Создать») — без обучающих абзацев |
| Titles | Только слот контейнера (`AppScaffold` / section header); не free-floating Text |
| Chat | Streaming + attachments + tool disclosure + stop/retry (не EntityCollection) |
| Feedback | Snack = soft; InlineBanner = blocking; danger = full page |
| Progressive disclosure | Collection → detail sections → secondary |
| Overview | Alerts first, then stats with drill-down |
| Buttons | [buttons.md](buttons.md): icon-first; labeled = короткие существительные |
| Responsive | [responsive.md](responsive.md): breakpoints только в core |
| A11y | semantic labels / tooltip на icon actions; ≥48px hit; live region на stream |

## Запреты (дополнение)

- `ListTile` / свой DataTable в features — EntityCollection + `AppListItem`
- Instructional / help copy в chrome и EmptyPlaceholder (Help — отдельный будущий модуль)
- Заглушки «Скоро» без CTA / без ссылки на канон-экран — дефект относительно UX contract
- Password fields в Admin/Company create flows

## Spacing

Канон tokens: [spacing-tokens.md](spacing-tokens.md). Код `AppSpacing` обязан совпадать с docs.
