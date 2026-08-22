# L05 — Employee shell + cabinet entry

## Цель

Контур сотрудника: post-login contour choice, cabinet selector, empty states create/import, вход в dynamic shell host. Без доменных hardcoded вкладок.

## Канон

- [04-employees/](../04-employees/) — domain, ui, ux-contract
- [session.md](../10-identity-keycloak/session.md) lifecycle
- [05 frontend](../05-cabinets/frontend.md), [dynamic-cabinets](../05-cabinets/dynamic-cabinets.md)
- [07](../07-ui-mobile-core/) для selector/collections

## Зависимости

| Нужно | Даёт |
|-------|------|
| L01; L02; L06 instance CRUD/list (минимум) | Employee app shell; `X-Cabinet-Id` wiring |

## Контракты (публикует)

| Контракт | Описание |
|----------|----------|
| Cabinet list for employee | owned/accessible only (peers isolated) |
| Enter cabinet | client sets `X-Cabinet-Id`; API rejects чужой id |
| Contour chooser | company.admin vs employee work — `AppSelectorPage` |
| Host slot | `DynamicCabinetShell` mounts tabs from meta (потребляет L06) |

## Изоляция

Shell chrome + selector на mock list — ок для UI. Закрытие слоя — с реальным ACL L01+L06.

## DoD

- [ ] Session flow 0/1/many cabinets по domain.md.
- [ ] Create from Base / Import bundle UI → L06 API.
- [ ] Peer isolation: employee A не видит cabinet B (API+UI test).
- [ ] Dynamic shell host: вкладки из meta.tabs, не hardcoded procurement screens.
- [ ] Projects entry point (список) — коллекция; создание может быть thin до L07 close.
- [ ] UX без модалок; laconic.

## Не считать готовым, если…

- Hardcoded feature tabs «Прогоны/КП» как Flutter module.
- Cabinet id только в memory без header enforcement.
- Empty state ведёт в «выбрать profile_id из allowlist».

## Exit gate

ACL tests + ux-contract 04; shell открывает Base cabinet tabs from meta.
