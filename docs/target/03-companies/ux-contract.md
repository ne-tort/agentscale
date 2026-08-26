# Companies — UX contract

Эталон IA: **тот же паттерн, что Admin shell**, но локальный org.  
EntityCollection + laconic UI. Не Notion-only metrics page.

## IA (bottom NavigationBar)

| Tab | Экран | Как у Admin |
|-----|-------|-------------|
| Сводка | `CompanyOverviewPage` | Alerts + usage (сотрудники, ключи, контейнеры, подписка) |
| Сотрудники | `CompanyEmployeesPage` → detail | Как Admin→Companies: invite, enable/disable, assign cabinets |
| Контейнеры | `CompanyContainersPage` → detail | Как Admin→Containers: list/pause/resume/delete **своих** сотрудников |
| Ключи ИИ | `CompanyAiKeyListPage` → detail | Как Admin→Keys: CRUD **local**; Admin-bound — badge RO, без edit |
| Кабинеты | `CompanyCabinetsPage` | Admin-assigned: list/detail **RO** (MVP). Future: local CRUD |
| Профиль | `CompanyProfilePage` | Org profile / subscription read |

Не открывать Employee dynamic shell как основной путь Company.

## Ключи ИИ (одна таблица / один list)

| Строка | Происхождение | UI |
|--------|---------------|-----|
| Local | Company создала (`owner_scope=company`) | Full CRUD, rotate, renew — паритет Admin forms (SDK + API key) |
| Linked | Admin bound platform key | Видна в том же list; chip «от платформы»; **нельзя** edit/rotate/delete |

## Контейнеры

List всех Project/Container сотрудников компании.  
Actions: pause / resume / delete — те же confirm pages, что Admin.  
Chat/rows кабинета — default off.

## Кабинеты (MVP)

List Admin-assigned (+ org-visible). Tap → RO summary (не meta edit).  
CTA «Создать» — **не** в MVP (future local cabinets).

## Invite / disable

Form: email, display name; без password.  
Enable/disable: full pages / `DangerConfirmPage`.

## Density / feedback

Dense lists; Snack soft; InlineErrorBanner blocking.
