# LEGACY — политика документации

## Источник правды

| Статус | Путь | Назначение |
|--------|------|------------|
| **Продукт** | [**PRODUCT.md**](PRODUCT.md) | Что строим: Pod UI + агенты с файлами в Pod |
| **As-built** | [target/12-layer-docs/](target/12-layer-docs/) | Что уже есть в коде (слои L00–L09) |
| **Ops** | [07-infrastructure/](07-infrastructure/) | k3s, Terraform, CI, Alembic, e2e runbooks |
| **LEGACY** | всё остальное ниже | Справочно, не расширять |

## Что считается legacy

1. **`docs/01-vision/` … `docs/10-implementation/`** — старая tenant/M00–M09 модель.
2. **`docs/target/`** (кроме **`12-layer-docs/`**) — **AI-generated «канон»**, не совпадает с видением продукта.  
   Не использовать как блокер («gap map says…»). Не дописывать новые BC-модули без явной задачи от оператора.
3. Корневой [`00-glossary.md`](00-glossary.md) — superseded.

Исключение: **`docs/07-infrastructure/`** — операционные runbook'и **актуальны** (деploy, e2e, WSL k3s).

## Правила

1. **Новые продуктовые требования** — в [**PRODUCT.md**](PRODUCT.md).
2. Legacy **не удалять** (история, ссылки из PR).
3. При правке legacy-файла — баннер в начале:

   ```markdown
   > **LEGACY.** Продукт: [docs/PRODUCT.md](../PRODUCT.md). Не расширять.
   ```

4. **`09-gap-map.md`** — исторический diff «канон ↔ код»; для решений смотреть код, тесты, PRODUCT.md.

## Карта (архив)

| Legacy | Заметка |
|--------|---------|
| `docs/target/00-entities.md`, `01…15` | AI canon → legacy |
| `docs/target/09-gap-map.md` | не gospel |
| `docs/01-vision/*`, `03-modules/M*` | tenant-era |
| `04-frontend/widget-catalog.md` | desktop shell |

Термины legacy **Tenant** ≈ **Company**, **Operator** ≈ **Employee** — только при чтении старых docs.
