# MCP packages — агент создаёт переиспользуемые tools

Канон: ИИ **разрабатывает** MCP под задачу (работа с таблицами кабинета + внешние вызовы), упаковывает в zip и сдаёт через **заранее готовый унифицированный** platform tool. Дальше пакет живёт в registry кабинета и подключается к другим проектам.

Паттерны: **plugin package**, **artifact registry**, **sandboxed worker**, **capability install contract**.

---

## Два слоя

| Слой | Кто пишет | Назначение |
|------|-----------|------------|
| Platform MCP `cabinet.*` | Prodavan | DDL/UI/rows/bundle/**deploy package** — строгий контракт |
| User MCP package | Агент / человек | Бизнес-tools: мутации своих таблиц, HTTP к внешним API, доменная логика |

Агент **не** регистрирует произвольный MCP «ссылкой на URL» в обход deploy. Единственный путь кода в кабинет — `cabinet.mcp_packages.deploy`.

---

## Package format (`mcp.package-v1.zip`)

```text
mcp.package-v1.zip
  manifest.json          # обязателен — контракт
  mcp.json               # MCP server descriptor (tools list, command)
  src/                   # python (или разрешённый runtime) + modules
  requirements.txt       # optional, pinned; validated allowlist
  README.md              # optional
```

### manifest.json (строго)

```json
{
  "format": "mcp.package",
  "format_version": 1,
  "name": "suppliers_sync",
  "version": "1.0.0",
  "runtime": "python3.12",
  "entry": { "command": "python", "args": ["-m", "src.server"] },
  "tools": [
    { "name": "suppliers.sync_external", "description": "…" }
  ],
  "permissions": {
    "cabinet_data": ["read", "write"],
    "network_hosts": ["api.example.com"],
    "shell": false
  },
  "platform_events": ["company.suspended", "company.reactivated", "employee.disabled"],
  "content_hash": "sha256:…"
}
```

Optional handler script in zip: `src/on_platform_event.py` — reads event JSON from stdin when `MCP_PLATFORM_EVENT_INVOKE=true` (as-built subset; not full MCP stdio).

Невалидный manifest / hash mismatch / oversized zip → deploy reject.

---

## Platform deploy contract

| Tool | Args | Effect |
|------|------|--------|
| `cabinet.mcp_packages.deploy` | `zip` (bytes/blob ref), optional `replace_if_name` | Validate → store artifact → register in `meta.mcp_packages` → enable for cabinet |
| `cabinet.mcp_packages.list` | — | Registry |
| `cabinet.mcp_packages.disable` | name/version | Soft off |
| `cabinet.mcp_packages.export` | name | Zip out (for cabinet bundle or share) |

Install pipeline:

```text
agent writes files in workspace
  → zip (или platform helper packs dir)
  → cabinet.mcp_packages.deploy(zip)
  → runtime stores in object storage + DB row
  → next project materialize mounts/starts package MCP in sandbox
```

---

## Runtime / sandbox

При materialize проекта:

1. Platform `cabinet.*` всегда.  
2. Enabled packages: start **sandboxed** process (cwd = package extract, env = scoped DB credentials **только** `cab_inst_<id>`, MCP stdio).  
3. Network: только `permissions.network_hosts` ∩ Company/Admin egress policy.  
4. `shell: false` в manifest — нет произвольного host shell снаружи entrypoint.  
5. Secrets: inject через platform vault refs, не хардкод в zip.

Выбор изолятора (bubblewrap / gVisor / micro-VM) — code-wave; контракт одинаков.

**As-built (2026-08-23):** materialize пишет `packages/{name}/.sandbox/run.json` (status `ready|invalid`, entry, tools) и блок `sandbox` в workspace `mcp.json`. Opt-in local spawn: `MCP_SANDBOX_SPAWN=true` → subprocess `python -m …` (pid в run.json); stop при rematerialize/delete. Bubblewrap/k8s — hole.

---

## Связь с таблицами кабинета

Package получает:

- Connection / API token **scoped** к schema instance.  
- Рекомендуемый SDK/helper: вызовы тех же row APIs (HTTP localhost gateway), а не прямой DDL.  
- DDL новых колонок — предпочтительно через platform `cabinet.columns.*` из агента **до** или **вместо** сырого ALTER в package (меньше дрейфа meta/UI).

Package **может** менять данные таблиц кабинета и ходить наружу — это и есть смысл custom MCP.

---

## Переиспользование

| Scope | Поведение |
|-------|-----------|
| Projects одного кабинета | Все видят enabled packages после deploy |
| Другой кабинет | Только через export package / cabinet bundle import (копия) |
| UI | Tab **Tools** — EntityCollection packages (name, version, status, disable) |

---

## Cabinet bundle

`cabinet.bundle` включает `mcp_packages/*.zip` (или refs).  
Import кабинета → новые package rows в новом instance (копия артефактов).

---

## Безопасность / Admin

| Контроль | Кто |
|----------|-----|
| Max package size, max packages/cabinet | Quotas |
| Allow `network_hosts` patterns | Company / Admin policy |
| Ban `runtime` not in allowlist | Platform |
| Disable package globally | Admin |
| Audit deploy | Always |

AgentToolPolicy ([08](../08-agent-providers/permissions-policy.md)) всё ещё режет shell/network на уровне **agent** host; package permissions — второй контур.

---

## Пример сценария

1. `cabinet.tables.create` suppliers.  
2. Агент пишет `src/server.py` (MCP tool sync + upsert rows).  
3. `cabinet.mcp_packages.deploy(suppliers_sync.zip)`.  
4. Новый project в том же кабинете → tool `suppliers.sync_external` уже в MCP.  
5. Export кабинета коллеге → у него своя schema-копия + свой package copy.
