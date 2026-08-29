# 01 — Entry flow

## После login

1. `SessionGatePage` → restore token → `GET /me`
2. `navigateAfterMe(me)`:
   - `platform_admin` без memberships → `AdminShell`
   - company principal → `CompanyShell`
   - employee:
     - memberships > 1 и нет `companyId` → `ContourSelectorPage` (выбор компании)
     - `GET /cabinets` (через picker или prefetch)
     - **1 cabinet** → `CabinetShell(cabinetId)` сразу
     - **>1 cabinet** → `CabinetPickerPage`
     - **0 cabinets** → empty state + hint

## Cabinet picker

- Таблица/список кабинетов с assignment (`GET /cabinets`)
- Tap → `workContext.enterCabinet(id)` → `CabinetShell`
- Не создавать кабинеты здесь (company admin / policy)

## Headers

- `X-Cabinet-Id` — после enter cabinet
- `X-Project-Id` — на страницах проекта / agent (позже)
