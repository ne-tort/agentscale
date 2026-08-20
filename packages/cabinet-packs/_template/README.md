# Cabinet pack template (без S4B)

Шаблон для нового profile pack:

```text
_template/
├── pack.json              # capabilities.s4b: false, systemCatalogs: []
├── cabinet-profile.json   # без nav s4b_settings, без procurement.s4b
├── prompts/
├── shops/
└── theme/
```

Скопируйте каталог, замените `id`, `displayName`, capabilities и seeds. **Не** включайте `system_catalogs.s4b` unless domain requires S4B.
