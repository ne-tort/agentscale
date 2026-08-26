# Auto-refresh

Централизованное фоновое обновление UI без кнопки «Обновить» в AppBar.

## Настройка

`Settings → Обновление` (`appSettings.autoRefreshSeconds`):

| Значение | UI |
|----------|-----|
| `0` | Не обновлять |
| `5`…`60` | N с |
| `300` / `600` | 5 мин / 10 мин |

Default: **30 с**. Persist: SharedPreferences.

## Код

| Артефакт | Роль |
|----------|------|
| `AppAutoRefreshBinder` | Timer + lifecycle pause + coalesce in-flight |
| `appAutoRefreshIsActive` | Route current + `TickerMode` (IndexedStack offstage) |
| `appRefreshDataEquals` | Deep `==` — skip `setState` если данные те же |

Паттерн страницы:

```dart
_autoRefresh = AppAutoRefreshBinder(
  onTick: () => _reload(silent: true),
  isActive: () => appAutoRefreshIsActive(context),
)..attach();
```

`silent: true` — без spinner / без snack на ошибке; `setState` только при изменении данных.

## Инварианты

- Нет `Icons.refresh` в шапках.
- Chat: silent tick пропускается при активном stream / send.
- Pull-to-refresh (`RefreshIndicator`) допустим как ручной жест.
