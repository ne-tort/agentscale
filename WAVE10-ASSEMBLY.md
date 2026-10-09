# WAVE10 — Сборки ПК/серверов в «Подборе техники»

Статус: **дизайн + фаза 1 (схема)**. Прод не трогаем — только dev.

Документ сводит замысел (в терминах заказчика) к as-built коду и задаёт целевую
модель связей, чтобы не сломать существующий поток «Позиция → Найденный товар».

---

## 1. Что уже есть в коде (as-built)

| Сущность (label) | table_slug | Ключевые поля | Связь |
|---|---|---|---|
| Позиции заказчика | `request_lines` | `title`, `part_number`, `qty`, `selected_offer_id`, `status` | — |
| Найденные товары (группы кандидатов) | `found_groups` | `part_number`, `aliases_pn`, `aliases_hash`, `match_kind`, `best_offer_id`, `is_best`, `face_*` | `line_id → request_lines` |
| Предложения поставщиков | `found_offers` | `title`, `price`, `seller`, `in_stock`, `is_selected`, `is_best`, `benefit_*` | `group_id → found_groups`, `line_id → request_lines` |
| Типы комплектующих | `equipment_types` | `name`, `sort_order`, `fields_json`, `build_scope` (all/pc/server) | — |
| Характеристики оборудования | `equipment_items` | `offer_id`, `type_id`, `qty`, `attrs` | `offer_id → found_offers`, `type_id → equipment_types` |
| Сборка | `equipment_builds` | `build_kind` (pc/server), `slots` {type_id: item_id}, `components_count`, `price_total` | `slots → equipment_items` |
| Бюджет | `budget_lines` | `line_id`, `price_in`, `markup`, `seller` | `line_id → request_lines` |
| Закупка | `procurement` | агрегат по `seller` | project-wide |

Пайплайн `equipment_offers_service.run()`: материализует `found_offers` из
`found_groups` (поиск в OpenSearch по ключам группы) → ранжирует (best оффер
группы, best группа позиции) → `_sync_budget` (бюджет из выбора позиции) →
`_sync_builds` (пересчёт `components_count`/`price_total`) → `_sync_procurement`.

**Вывод:** «Сборка» и «Типы комплектующих» существуют, но:
- сборка **не привязана** к позиции заказчика (`request_lines`) и не влияет на бюджет/закупку;
- слот сборки указывает на один `equipment_item` (один товар на тип) — нет **альтернатив** и **best** по типу;
- сборка не участвует в материализации офферов (нет сопоставления по P/N/алиасам/хэшам, как у групп).

---

## 2. Сверка замысла с кодом (термины и поправки)

| Формулировка заказчика | Технический эквивалент | Поправка/уточнение |
|---|---|---|
| «Найденный товар» цепляется к Сборке, а не к Позиции | `found_groups` (группа-кандидат) получает `build_id` + `slot_type_id` | Переиспользуем **существующую** пару `found_groups`/`found_offers` — вся материализация/best/benefit работает как есть |
| Тип комплектующего имеет несколько товаров + best | Слот = (`build_id`,`type_id`) → много `found_groups` → у каждой свой best-оффер; одна группа помечена best слота | `is_best` группы уже есть; добавляем смысл «best группы внутри слота» |
| ИИ выбирает дешёвый вариант по P/N/алиасу/хэшу, автоматика группирует и берёт лучшую цену | Ровно механика `found_groups` → `found_offers` | Ничего нового: ИИ пишет группы, платформа материализует офферы и выбирает best |
| Сборка не обязана иметь все компоненты | `slots`/компоненты опциональны | Уже так (json `slots`), нужно закрепить в валидации/UI |
| Несколько сборок на позицию (Intel/AMD/сокеты) | `equipment_builds.line_id` → много сборок; `is_best` + альтернативы | Добавляем `line_id`, `is_best`, `alternatives_count` |
| Бюджет/закупка — в «Найденных товарах», но для сборок сопоставляются со Сборками | `budget_lines` для сборки = сумма best-офферов по слотам; связь с `build_id` | Добавляем `budget_lines.build_id`; для обычных позиций — без изменений |

**Ключевая архитектурная идея:** не вводим параллельную сущность «товар сборки»,
а **переиспользуем `found_groups`/`found_offers`** — группа может принадлежать
либо позиции (`line_id`), либо слоту сборки (`build_id` + `slot_type_id`).
Тогда материализация офферов, ранжирование, выгода, наличие и сопоставление —
один и тот же код для обычного поиска и для комплектующих сборки.

---

## 3. Целевая модель связей

```
request_lines (Позиция заказчика)
├── found_groups (line_id)          ← обычный поиск (как сейчас)
│     └── found_offers (group_id)
└── equipment_builds (line_id)      ← СБОРКА (best + альтернативы)
      └── found_groups (build_id, slot_type_id)   ← кандидаты по типу комплектующего
            └── found_offers (group_id)           ← офферы, best, выгода, наличие
```

Правила:
1. **Позиция → одна best Сборка + альтернативы.** `equipment_builds.is_best`
   (best среди сборок позиции), `alternatives_count`. Аналогично `found_groups.is_best`.
2. **Сборка → слоты по типам комплектующих.** Слот = (`build_id`,`slot_type_id`);
   в слоте ≥0 групп-кандидатов, одна из них `is_best` (по точности→цене→наличию).
3. **Обычный поток не ломается:** группы с `line_id` и без `build_id` ведут себя как раньше.
4. **Расчёты.** `price_total` сборки = Σ(best-оффер best-группы слота × qty).
   `budget_lines` для сборки получает `build_id` и сумму; для обычных позиций — как есть.
   `procurement` агрегирует по seller как и раньше (seller берётся из офферов компонентов).

Инвариант: группа принадлежит **либо** позиции, **либо** слоту сборки
(`line_id` XOR (`build_id`+`slot_type_id`)) — валидируем, но допускаем переходное
состояние (сборка внутри позиции может иметь и `line_id` для контекста чата).

---

## 4. План по фазам

**Фаза 1 — схема (этот PR).**
- `found_groups`: + `build_id` (ref equipment_builds), + `slot_type_id` (ref equipment_types).
- `equipment_builds`: + `line_id` (ref request_lines), + `is_best`, + `alternatives_count`, + `match_kind`, + face-поля.
- `budget_lines`: + `build_id` (ref equipment_builds).
- Миграция + правка сидов + тесты схемы.

**Фаза 2 — пайплайн.** Материализация офферов для групп-компонентов (по build_id),
ранжирование внутри слота, best-группа слота, `price_total` из best-групп,
best-сборка позиции, бюджет из сборки.

**Фаза 3 — UI.** Экран сборки: слоты по типам, список альтернатив по слоту с
выбором best (переиспользовать `benefit`/`selection`/`match_label`), плашка сборки
в позиции, суммарная стоимость.

**Фаза 4 — MCP-инструменты.** CRUD сборок/слотов/кандидатов, оптимизированный
под сборку (совместимость + цена), аудит свойств типов комплектующих.

**Фаза 5 — промпты + регресс.** Инструкции агенту по сборкам, проверка, что
обычный поток «Позиция → Найденный товар» не регрессировал.

---

## 5. Аудит типов комплектующих (черновик, уточняется в фазе 4)

Все `fields_json` — **опциональны** (флаг required отсутствует как класс; UI
пишет attrs свободно). 13 типов: cpu, motherboard, ram, storage, gpu, psu,
cooling, case, case_fans, nic (scope=all) + raid_hba, backplane, bmc (scope=server).
Замечания к уточнению: несогласованные label'ы (`ram.ram_type` = «Тип» vs
`motherboard.ram_type` = «Тип ОЗУ»); нет полей под совместимость (cpu: family/gen,
ecc; motherboard: pcie-слоты; gpu: чипсет; nic: контроллер; raid: внешние порты).
