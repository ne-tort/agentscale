# Companies — UX contract

Эталон IA: **тот же паттерн, что Admin shell**, но локальный org.  
EntityCollection + laconic UI. Не Notion-only metrics page.

## IA (bottom NavigationBar)

| Tab | Экран | Как у Admin |
|-----|-------|-------------|
| Сводка | `CompanyOverviewPage` | Alerts + usage (сотрудники, ключи, контейнеры, подписка) — **только через logo**, не в rail |
| Сотрудники | `CompanyEmployeesPage` → `CompanyEmployeeDetailPage` | Inline «Добавить сотрудника» + password create; таблица: login, email, проекты, кабинеты, онлайн; disabled — warning row |
| Настройки | `CompanySettingsBody` | ID (copy) + пароль первыми; затем language/theme |
| Контейнеры | `CompanyContainersPage` → detail | Как Admin→Containers: list/pause/resume/delete **своих** сотрудников |
| Ключи ИИ | `CompanyAiKeyListPage` → detail | CRUD **local**; platform-bound — info-строка в таблице, поля read-only на detail |
| Кабинеты | `CompanyCabinetsPage` | Inline create + copy; platform-assigned — info row; delete только local |
| Профиль | `CompanyProfilePage` | Org profile / subscription read |

Не открывать Employee dynamic shell как основной путь Company.

Overview скрыт из rail/bottom nav; logo → overview (как Admin shell).

## Ключи ИИ (одна таблица / один list)

| Строка | Происхождение | UI |
|--------|---------------|-----|
| Local | Company создала (`owner_scope=company`) | Full CRUD, rotate, renew — паритет Admin forms (SDK + API key) |
| Linked | Admin bound platform key | Тот же list; **info-цвет строки + bold title**; поля detail read-only; без info-banner |

Suspended/expired keys: **warning** row color (приоритет над info).

## Контейнеры

List всех Project/Container сотрудников компании.  
Actions: pause / resume / delete — те же confirm pages, что Admin.  
Chat/rows кабинета — default off.

## Кабинеты

List org-visible + inline «Добавить кабинет». Long-press: copy (duplicate API), delete только `writable`.  
Platform-assigned: info row, no delete.

## Сотрудники / pause

Inline create: login + auto password (clipboard snack). Detail: login (copy), password, email, кабинеты.  
Toggle pause/resume на detail (confirm); список — warning row при `status=disabled`.

## Credentials

- **Company self-service:** ID (= `company.id`, copy) + password — вкладка **Настройки**.
- **Admin company hub:** inline ID (copy) + password на hub; **без** editable org login (username = company id для ROPC).
- **Platform admin:** `AdminSettingsBody` — login + password (Keycloak profile API).

## Platform-assigned entities (modules, keys, cabinets)

Единый list styling: `rowColor: info`, `titleBold: true`.  
Без верхних info-бanner на detail/json. Lock / отсутствие delete в mutate mode.

## Density / feedback

Dense lists; Snack soft; InlineErrorBanner blocking.
