# Prodavan Agent

Ты работаешь над **Prodavan** — облачной платформой автоматизации задач (универсальный SaaS), не над Telegram Commerce-ботом.

Канон продукта: [`docs/target/`](docs/target/). Код `apps/*` сейчас **stub** — см. [`STUB.md`](STUB.md). Legacy docs — [`docs/LEGACY.md`](docs/LEGACY.md); **не** расширять и **не** копировать доменную логику из git history.

## Субагенты (Cursor Task)

**Всегда** запускай субагентов с моделью **Auto** = параметр `model: "inherit"` (или не передавай `model` — inherit по умолчанию).

- **Запрещено** выбирать конкретные slug'и (`gpt-5.6-sol-*`, `composer-*`, `fast` как явный override и т.п.), если пользователь **сам** не попросил другую модель.
- Сложность задачи **не** оправдывает смену модели субагента.

## Суть продукта (не терять)

1. **Универсальный облачный сервис** автоматизации задач агентами (не только закупки).
2. **Иерархия:** Platform Admin → Company → Employee (модель менеджмента).
3. **Кабинеты (dynamic):** instance с ownership Employee+Company+Admin; schema-per-instance (изоляция пиров). UI из meta. ИИ создаёт таблицы/вкладки и **MCP packages** (zip код+контракт → deploy). Export/import. См. [`dynamic-cabinets.md`](docs/target/05-cabinets/dynamic-cabinets.md), [`mcp-packages.md`](docs/target/05-cabinets/mcp-packages.md).
4. **Проект** — контейнер агента в кабинете; контекст из кабинета; агент может расширять экосистему кабинета (переиспользуемые MCP tools между проектами).

Подробности: [`docs/target/00-principles.md`](docs/target/00-principles.md).

## Язык и границы

- Ответы пользователю — на русском, если не сказано иное.
- Новые продуктовые требования — только в `docs/target/`.
- Infra (k3s, Terraform, Argo, CI) не ломать без явной задачи.
