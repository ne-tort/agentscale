# M00 — UI: кабинеты

## Экраны

### 1. Селектор кабинета (global header)

**Расположение:** верхняя панель, слева от селектора проекта.

**Элементы:**

- Dropdown: список active кабинетов tenant
- Badge профиля: «Электроника», «Универсальный»
- Иконки capabilities: S4B (молния) — **только** electronics-procurement
- Кнопка «+ Новый кабинет» → wizard

**Поведение switch:**

1. Confirm dialog если есть несохранённые изменения в M03 editor
2. POST switch API
3. Invalidate: projects list, catalogs, agent stream
4. Toast: «Кабинет «Закупки 2026» активен»

---

### 2. Wizard создания кабинета

**Шаги:**

1. **Профиль** — карточки из реестра; для electronics — описание S4B и specs-kp
2. **Имя и slug** — live validation slug uniqueness
3. **Seed preview** — что будет скопировано из pack
4. **Progress** — pack seed pipeline steps с checkmarks
5. **Done** — auto-switch на новый cid

**Ошибки UI:**

| API code | Сообщение пользователю |
| --- | --- |
| `SLUG_CONFLICT` | «Такой идентификатор уже занят» |
| `SEED_FAILED` | «Не удалось инициализировать кабинет. Поддержка: {request_id}» |
| `CAPABILITY_FORBIDDEN` | не показывается (override недоступен в UI) |

---

### 3. Настройки кабинета

**Route:** `/settings/cabinet/{cid}`

- Display name (edit)
- Profile (read-only + tooltip «создайте новый кабинет для другого профиля»)
- Capabilities table (read-only)
- Archive button (destructive confirm)
- Storage usage meter

---

### 4. Архивные кабинеты

**Route:** `/settings/cabinets/archived`

- Restore action
- Read-only browse projects (M01)

---

## Состояния и empty states

| Состояние | UI |
| --- | --- |
| Нет кабинетов | CTA «Создайте первый кабинет» |
| Seed in progress | Blocking overlay на wizard |
| Archived active attempt | Redirect + banner |

---

## Accessibility

- Keyboard: Alt+C — focus cabinet selector
- Screen reader: announce profile and S4B availability on switch
- Color: S4B badge не только цветом (icon + text)

---

## Cross-cabinet UX guards

- URL `/projects/{pid}` валидирует `pid` принадлежит active cid
- Deep link с чужим cid → «Переключить кабинет?» dialog
- Breadcrumb: `{Cabinet} / {Project} / …`

---

## Negative test IDs (UI)

| Test ID | Сценарий |
| --- | --- |
| NEG-CAB-UI-001 | Switch во время seed — кнопка disabled |
| NEG-CAB-UI-002 | S4B badge на generic cabinet — absent |
| NEG-CAB-UI-003 | Edit profile_id field — not rendered |
| NEG-CAB-UI-004 | Archive active cabinet — force switch prompt |
