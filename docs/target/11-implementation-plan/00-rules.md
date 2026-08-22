# Правила плана реализации

Эти правила обязательны для любого слоя `L00`…`L09`. Нарушение = слой нельзя помечать `done` в [checklist-master](checklist-master.md).

---

## 1. Контракты раньше фич

Каждый слой публикует **стабильный контракт** (API / port / headers / event / widget API), к которому цепляются следующие слои.

| Требование | Смысл |
|------------|--------|
| Именованный контракт | Зафиксирован в файле слоя + [contracts-index](contracts-index.md) |
| Версия / совместимость | Ломающие изменения — major note в contracts-index |
| Тесты на границе | Контракт проверяется без UI соседнего слоя (HTTP/contract/unit) |
| Заглушки соседей | Допустимы **только** за контрактом (fake Principal, fake Company id), не «в обход» |

Канон продукта не дублировать здесь — только **границы реализации**. Продукт: [`00-principles`](../00-principles.md), модули `01`…`10`.

---

## 2. Изоляция: делать полностью, если можно без соседей

Если слой не требует живого соседнего runtime — реализовать **весь** DoD слоя, а не «тонкий happy-path».

Примеры полной изоляции:

- **L02 UI core** — виджеты, breakpoints, EntityCollection, запрет модалок; golden/widget tests.
- **L03 Keys** — CRUD + vault ref + resolve policy с тестовым `company_id`.
- **L06 Runtime** — create schema, meta CRUD, data, bundle import/export, `cabinet.*` MCP **без** agent chat.
- **L08 Adapter** — Cursor/Codex/Claude wrap на локальном `cwd` fixture + `AgentEvent` schema.

Запрещено откладывать «на потом внутри слоя» то, что уже описано в каноне этого BC и не блокируется чужим контрактом.

---

## 3. Анти-«готово» (жёстко)

Слой **не** считается готовым, если выполнено только одно из:

- health / hello / один экран-заглушка;
- schema без enforcement (headers, ACL, peer isolation);
- UI без EntityCollection / с Dialog/BottomSheet «временно»;
- Keys без `secret_ref` / с секретом в API response;
- Cabinet как static `profile_id` module;
- Agent без обязательных `AgentEvent` (`usage`, `done`, …);
- «Работает у меня локально» без автоматизированных проверок контракта.

Каждый слой имеет секцию **«Не считать готовым, если…»** — это veto-list для чеклиста.

---

## 4. Зависимости только через контракты

```text
Потребитель  --uses-->  Контракт поставщика  --implemented-by-->  Поставщик
```

Нельзя:

- импортировать внутренности pack/domain соседнего модуля в обход публичного API;
- читать чужую Postgres schema кабинета из Admin/Company;
- класть cabinets/projects в JWT;
- передавать `cli_subscription` в AgentProviderPort.

См. [09-gap-map](../09-gap-map.md) «Запрещено» по BC.

---

## 5. Чеклист = единственный статус готовности

| Статус пункта | Значение |
|---------------|----------|
| `todo` | Не начато |
| `doing` | В работе |
| `blocked` | Ждёт контракт другого слоя (указать ID) |
| `done` | Пункт DoD выполнен + есть доказательство (тест/PR/дока API) |

Слой `done` ⟺ все DoD `done` и veto-list пуст по факту **и** as-built карточка слоя в [`12-layer-docs`](../12-layer-docs/) актуальна (`Status: done`, заполнены § Что/Как сделано, **Quality ≥ 8** по [quality-score](../12-layer-docs/quality-score.md)).

---

## 6. Инфра кластера vs продуктовые слои

k3s / Terraform / Argo / CI images — **уже есть**, не ломать без задачи ([STUB](../../../STUB.md)).  
План `L00+` — продуктовая платформа (apps, schema, контракты). Инфра-изменения только если слой явно требует (например KC realm для L01).

---

## 7. As-built документация обязательна

Параллельно с кодом ведётся [`12-layer-docs/`](../12-layer-docs/) — сжатая картина слоя (семантика, что/как сделано, контракты, связи, инварианты, gaps).

| Правило | |
|---------|--|
| PR меняет слой | Обновить `12-layer-docs/LNN-*.md` в том же PR |
| Слой → `done` в checklist-master | Карточка `Status: done`, § Что/Как сделано заполнены, **Quality ≥ 8** |
| Читать при работе | Сначала карточка `12`, потом код |

Принципы разделов: [12/00-principles.md](../12-layer-docs/00-principles.md).
