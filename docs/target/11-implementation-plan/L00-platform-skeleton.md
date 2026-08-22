# L00 — Platform skeleton

## Цель

Чистый каркас `apps/api` + `apps/flutter` + Alembic bootstrap под канон. Без доменной логики legacy. Готовность принимать схемы и роуты следующих слоёв.

## Канон

- [STUB.md](../../../STUB.md)
- [AGENTS.md](../../../AGENTS.md)
- Infra docs: `docs/07-infrastructure/` (alembic) — справочно по процессу миграций
- Не следовать: [LEGACY](../../LEGACY.md)

## Зависимости

| Нужно | Даёт |
|-------|------|
| — (старт) | Package layout, CI smoke, migration pipeline, health→versioned API envelope |

## Контракты (публикует)

| Контракт | Описание |
|----------|----------|
| `GET /api/v1/health` | liveness + build/version metadata |
| `AppError` / problem envelope | Единый JSON error shape для API |
| Alembic `stub`→доменные ревизии | Цепочка миграций без legacy tables |
| Flutter `lib/core/` + `lib/features/` empty shells | Место для L02/L04+ |
| Config | env schema: DB, KC later, vault later — без секретов в git |

## Изоляция

Реализуется **полностью** без Identity/Cabinets. Не ждать L01.

## DoD (критерий успеха)

- [x] API поднимается; health стабилен; OpenAPI/schema stub или эквивалент контрактных тестов.
- [x] Alembic: чистый bootstrap; `upgrade head` / `downgrade` на пустой Postgres в CI.
- [x] Flutter: приложение стартует; **нет** Dialog/BottomSheet в core; пустой home → готов к L02.
- [x] Запрет: нет password-login, нет HS256 issuer, нет procurement/S4B кода.
- [x] Документирован layout модулей API (`api` / `application` / `domain` / `infrastructure` или принятый эквивалент).
- [x] Lint/test job в CI зелёный на skeleton.

## Не считать готовым, если…

- Остался «временный» JWT login из legacy.
- Миграции копируют старые tenant/cabinet pack таблицы «чтобы быстрее».
- Flutter feature-экраны уже размазаны без core primitives (обгон L02).
- Только README «как поднять», без автоматической проверки migrate+health.

## Exit gate

CI: migrate + health + flutter analyze. Review: нет legacy domain imports.
