# Shared UI

Import components and their scoped styles from `src/shared/ui`:

```tsx
import { Button, SearchableTable, StatusBadge, TextField } from "../shared/ui";
```

The barrel imports `foundation.css`; no global stylesheet or router integration is required. Components expose typed React props, use native controls and links, and accept page-specific labels, content, callbacks, and data. `NavigationTabs` uses ordinary `href` navigation; `Breadcrumbs` marks the final item as the current page. `SearchableTable` and `SearchableList` filter locally and can be controlled with `query` and `onQueryChange`.

Use `ConfirmationDialog` as a controlled component: render it with `open`, then update that state from `onConfirm` or `onCancel`. It focuses Cancel on open, traps Tab within its actions, and supports Escape unless `busy`.

All styling is scoped to `.ui-*` classes, supports reduced motion, keeps radii at 8px or less, and does not depend on shell context. Import it where the host shell owns the relevant view; no feature files are changed here.