# 09 — Legacy deletion

Hard-deleted (не переносить поведение):

- `project_workspace_page.dart` — chat-first UI
- `tool_approve_page.dart`
- `dynamic_cabinet_shell.dart` — flat ListView
- `cabinet_list_page.dart` → replaced by `CabinetPickerPage`
- `project_list_page.dart`, `project_create_page.dart`, `project_settings_page.dart`
- `cabinet_create_page.dart` (employee create — out of scope picker)
- `contour_selector_page.dart` — keep if multi-company
- `widgets/*` attachment preview, project status chips
- `meta/runtime/cabinet_module_runtime_page.dart` — replaced by `CabinetModuleHost`
- `test/employee_widgets_test.dart`

Moved to `features/auth/`:

- `login_page.dart`
- `session_gate_page.dart`
