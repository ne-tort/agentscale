# M01 — UI: проекты

## Project selector (header)

- Dropdown после cabinet selector
- Recent projects (last_opened_at)
- «+ Новый проект» modal: slug + name
- Badge: inbox pending count, active runs

## Project dashboard

**Route:** `/c/{cid}/p/{pid}`

**Widgets:**

- Inbox dropzone (M02 upload)
- Runs list with phase chips
- Quick actions: new run, export KP
- Stats from GET …/stats

## Create flow

1. Modal slug/name
2. POST /projects
3. Auto open_project
4. Navigate to dashboard empty state

## Archive

- Settings → Archive project
- Confirm: «Прогоны сохранятся, новые спеки будут недоступны»
- Redirect to project list

## Empty states

| State | Message |
| --- | --- |
| No projects | «Создайте проект для загрузки спеки» |
| No runs | «Перетащите xlsx в inbox или нажмите Загрузить» |

## Deep links

`/c/{cid}/p/{pid}/runs/{run_id}` — validates cabinet + project membership.

## Negative test IDs (UI)

| Test ID | Сценарий |
| --- | --- |
| NEG-PRJ-UI-001 | pid in URL ≠ session → prompt switch |
| NEG-PRJ-UI-002 | Upload on archived → disabled |
