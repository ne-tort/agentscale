# L09 — Vertical integration

## Цель

Сшивка слоёв в рабочий продукт: chat UI ↔ triggers ↔ agent; metrics end-to-end; starter bundle (optional); idle policy; hardening. **Не** место, куда откладывали DoD L01…L08.

## Канон

- [09-gap-map.md](../09-gap-map.md)
- [00-principles.md](../00-principles.md)
- Все DoD L01…L08
- Chat/attachments/triggers из [06](../06-projects-runtime/)
- Starter: [default-cabinets](../05-cabinets/default-cabinets.md) / bundle-format

## Зависимости

| Нужно | Даёт |
|-------|------|
| Контракты L01…L08 = done | E2E сценарии; ops policies; checklist-master all-green path |

## Контракты (публикует)

| Контракт | Описание |
|----------|----------|
| E2E smoke suite | Admin→Company→Employee→Cabinet→Project→Agent ping |
| Metrics pipeline | AgentEvent.usage → Admin/Company cards |
| Platform event bus vs project triggers | явная граница |
| Release gate checklist | ссылка на checklist-master |

## Изоляция

Нет. Только после поставщиков.

## DoD

- [ ] E2E happy path автоматизирован (или scripted + CI nightly).
- [ ] Negative: peer cabinet access denied; disabled employee; expired key.
- [ ] Chat + attachment → agent turn → persisted transcript + usage visible in Admin.
- [ ] Re-materialize after MCP package deploy reflected in next turn.
- [ ] Idle pause policy configurable (даже если default off in dev).
- [ ] Optional equipment starter **bundle** import works (не Flutter module).
- [ ] Gap-map «явно не делать» — regression checklist в CI comments/docs.

## Не считать готовым, если…

- «Демо на ноуте» без suite.
- Слои L0x всё ещё с открытыми veto.
- Vertical работает только с bypass auth.

## Exit gate

checklist-master: все слои `done`; E2E green; sign-off по gap-map запретам.
