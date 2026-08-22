# L04 — Admin + Company control plane

## Цель

Полные UI+API контуры Platform Admin и Company по UX-контрактам. Квоты/policy кабинетов, keys bind, invite, metrics read models. **Без** раздачи static `profile_id` modules.

## Канон

- [01-platform-admin/](../01-platform-admin/) — domain, ui, **ux-contract**, metrics
- [03-companies/](../03-companies/) — domain, ui, **ux-contract**
- [02](../02-ai-provider-keys/) (потребление)
- [08 admin-control-plane](../08-agent-providers/admin-control-plane.md)
- Dynamic cabinets ownership: [05 dynamic](../05-cabinets/dynamic-cabinets.md)

## Зависимости

| Нужно | Даёт |
|-------|------|
| L01 Principal/authz; L02 widgets; L03 keys | Admin/Company shells; company quotas; org cabinet metrics views |

## Контракты (публикует)

| Контракт | Описание |
|----------|----------|
| Admin Company API | create company + invite company.admin; quotas; key bindings; agent policy |
| Company Employee API | invite/disable; **нет** static cabinet grant multi-select |
| `CompanyCabinetQuota` | лимиты create/import/packages |
| Metrics read DTOs | [metrics.md](../01-platform-admin/metrics.md) |
| Optional `StarterBundleCatalog` | read-only starters — не gate на create |
| Org cabinets list | read-mostly: id, name, owner — **не** rows чужой schema |

## Изоляция

UI можно вести на mock API, но слой **не** `done` без реальных L01/L03 контрактов. Не блокировать L06.

## DoD

- [ ] Admin NavigationBar и потоки по ux-contract.md (EntityCollection, DangerConfirmPage, Selector).
- [ ] Company NavigationBar и invite **без** password и **без** profile grants.
- [ ] Create company: quotas + AI bind + invite.
- [ ] Metrics endpoints + UI карточки (не «TODO placeholder» как done).
- [ ] Agent policy / model allowlist API по admin-control-plane (хотя бы preset assign).
- [ ] Нет экранов «выдать equipment-procurement module».
- [ ] E2E или API+widget tests критичных flows.

## Не считать готовым, если…

- Grants/`profile_id` allowlist снова в UX.
- Admin/Company логика вперемешку с Employee feature files.
- Сводка без реальных метрик (пустые карточки навсегда).
- Модалки в flows.

## Exit gate

UX DoD из 01/03 ux-contract отмечены; authz matrix покрыта тестами.
