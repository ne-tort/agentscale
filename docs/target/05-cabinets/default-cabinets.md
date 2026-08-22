# Default cabinets & starters

## Base cabinet (обязательный шаблон)

При «Создать кабинет» Employee получает instance, склонированный с **Base**:

| System area | Содержание |
|-------------|------------|
| Projects | EntityCollection проектов |
| Chat | Agent chat + attachments |
| Context | Prompts, skills, rules, seeds, AGENTS |
| Tables | UI над meta.tables (создать/архив) |
| Tools | UI над mcp_tools registry |
| MCP contracts | Platform `cabinet.*` уже подключены |

Base **независим** и достаточен для универсальной автоматизации.  
Домен появляется только как **новые tables/tabs/tools** (человек или ИИ).

## Starter bundles (не code modules)

| Bundle | Бывший смысл |
|--------|----------------|
| `equipment-procurement` starter | Спека/поиск/КП как seed tables + views + declarative MCP wrappers |
| others | По мере появления |

Ставятся через **Import** из platform catalog или файла — тот же [bundle-format](bundle-format.md).

## Больше не канон

Отдельные деревья `cabinets/electronics_procurement` в коде приложения как способ добавить домен.
