# Composition — декомпозиция виджетов

## Правило

Виджеты **не монолитны**. Составной контрол = сборка из более мелких core-атомов и слотов.

## Слои

```text
atoms          AppIconButton, AppCheckbox, AppRadio, tokens
molecules      AppListItem, AppButton, AppPreferenceTile
organisms      AppEntityCollection, AppSelectorPage, AppScaffold, EmptyPlaceholder, preferences/*, DangerConfirmPage
screens        feature pages (только сборка organisms/molecules)
```

| Слой | Где код | Может импортировать |
|------|---------|---------------------|
| atoms / molecules / organisms | `lib/core/` | только core + Flutter |
| screens | `lib/features/…` | core; **не** соседние feature widgets как «свой UI kit» |

## Когда выносить в core

- Используется или будет использоваться в ≥2 местах, **или**
- Это общий chrome (toolbar action, row, section header slot, collection).

Иначе — сначала слот существующего organism; не плодить третий list.

## Запрещено

- Feature-local `StatelessWidget` с собственной кнопкой/list/table «как в core, но чуть иначе».
- Копипаст `ListTile` / raw `DataTable` в features.
- God-widget на 500+ строк без слотов (разбить на organisms/molecules в core).

## Гибко

- Private `_RowSubtitle` внутри core-файла organism — ок.
- Feature может держать **layout-only** private helpers без своих цветов/типографики/кнопок (только `Padding`/`Column` из уже переданных core children).

## Примеры правильной сборки

```text
AdminCompaniesPage
  └── AppScaffold(title: …)
        └── AppEntityCollection(
              toolbar: [AppIconButton(add), AppIconButton(search)],
              …,
            )

ProjectWorkspace
  └── chat organisms from core atoms (composer = fields + AppIconButton send)
  └── secondary tab → AppEntityCollection (prompts / runs)
```
