# Agent tool permissions (канон Prodavan)

Цель: **полный контроль** из Admin (и опционально Company policy) над тем, что агент может делать: файлы, shell, сеть, MCP — независимо от того, какой SDK выбран.

Vendor детали различаются; Prodavan задаёт **единую политику**, адаптеры транслируют её.

## Логическая модель `AgentToolPolicy`

Хранится в platform DB; версионируется; назначается: Platform default → Company override → Project override (сужение only).

| Поле | Тип | Смысл |
|------|-----|--------|
| `fs_read` | bool | Чтение workspace |
| `fs_write` | bool | Создание/правка файлов в workspace |
| `fs_delete` | bool | Удаление файлов |
| `shell_exec` | bool | Запуск команд в контейнере |
| `network` | enum | `deny` \| `allowlist` \| `allow` |
| `network_allowlist` | string[] | Hosts (для allowlist) |
| `mcp` | enum | `deny` \| `manifest_only` \| `allowlist` |
| `mcp_allowlist` | string[] | Server ids |
| `sandbox` | enum | `off` \| `workspace` \| `strict` |
| `approval` | enum | `none` \| `dangerous_only` \| `all_tools` |
| `extra_deny_tools` | string[] | Vendor-agnostic ids |

### Рекомендуемые пресеты Admin

| Preset | Назначение |
|--------|------------|
| `chat_readonly` | Только чтение FS + MCP read tools; shell/network off |
| `workspace_dev` | FS read/write, shell on, network allowlist, sandbox workspace |
| `locked_automation` | FS write + shell, network deny, approval none, sandbox strict |
| `break_glass` | Широкий доступ; только Admin + audit |

Default для новых Company: **`workspace_dev`** с `sandbox=workspace`, `network=allowlist` (пустой = deny).

## UI Employee

Не instructional walls — laconic controls:

- В project settings: selector пресета (если Company разрешила) или read-only badge политики.
- Approval HITL: **full page** (`DangerConfirmPage` / tool-approve page), не modal — при `approval≠none`.

## Трансляция в SDK

### Cursor

| Policy | Mapping |
|--------|---------|
| `sandbox=workspace\|strict` | `local.sandboxOptions.enabled: true` |
| `network=allowlist` | Materialize `.cursor/sandbox.json` hosts |
| `network=deny` | Sandbox on, empty allowlist |
| `shell_exec=false` / `fs_write=false` | Prefer hooks `preToolUse` deny + document gap if SDK can't remove tool | 
| `approval=all_tools` | Hook deny→platform callback (headless нет IDE prompt) |

Cursor headless **не** даёт IDE approve UI — HITL только через наш bridge/hooks.

### Codex

| Policy | Mapping |
|--------|---------|
| `sandbox` | `read_only` / `workspace_write` / avoid `danger-full-access` |
| `network` | `sandbox_workspace_write.network_access` / profile network rules |
| `approval` | `approval_policy`: `never` (locked) \| `on-request` → bridge \| `untrusted` |
| Permission profiles | Предпочтительны на новых версиях CLI; не смешивать со старым `sandbox_mode` без миграции |

### Claude Agent SDK

| Policy | Mapping |
|--------|---------|
| Readonly | `allowedTools: [Read,Glob,Grep,…]` + `permissionMode: "dontAsk"` |
| No shell | `disallowedTools: ["Bash"]` or `Bash(…)` patterns |
| No writes | deny `Edit`/`Write` or `permissionMode: "plan"` |
| HITL | `permissionMode: "default"` + `canUseTool` → Prodavan approve API |
| Lockdown | Never pair broad `bypassPermissions` with partial `allowedTools` (unlisted tools still pass) |

## Enforcement layers (defense in depth)

```text
1. Admin AgentToolPolicy  →  adapter mapping
2. OS/container           →  non-root, read-only rootfs, no docker.sock
3. Network policy         →  egress NetworkPolicy / allowlist
4. MCP gateway            →  manifest allowlist only
5. Audit log              →  every denied/approved tool
```

Адаптер — **не** единственная граница.

## Gaps / честно

| Gap | Mitigation |
|-----|------------|
| Cursor sandbox требует native helper binaries в image | Pin `@cursor/sdk-*` in sidecar image |
| Cursor без sandbox = полный доступ | Admin default sandbox on |
| Codex approval needs interactive reviewer | Bridge to Employee UI or force `never`+tight sandbox |
| Claude `total_cost_usd` estimate | Billing from Anthropic Usage API + our meters |

См. [admin-control-plane.md](admin-control-plane.md).
