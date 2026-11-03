# Form UI

Import reusable form components and their scoped styles from `src/shared/ui/forms`:

```tsx
import { ConfirmationDialog, SearchableCombobox, TableToolbar, TextField, Tabs } from "../shared/ui/forms";
```

Text, select, textarea, and checkbox fields use native input semantics and share `label`, optional `hint`, and optional `error` props. The searchable combobox accepts string-valued `options`; provide `name` to submit the selected option as a form value. It can be controlled with `value` and `onValueChange`, or initialized with `defaultValue`. In controlled mode, use `null` as the empty `value` after `onValueChange` reports `undefined`.

`ConfirmationDialog` is controlled by `open`, restores focus on close, traps keyboard focus, and makes sibling content inert while open. `Tabs` supports controlled or default selection, arrow/Home/End keyboard navigation, and automatic or manual activation. `TableToolbar` accepts filter controls as children. `Breadcrumbs` marks the last item as the current page.

Styles are imported by this directory's `index.ts` and scoped to `.form-*` classes. No router, shell context, global stylesheet, or root shared UI export is required.