---
name: file-tools
description: File editing etiquette and PowerShell syntax for this Windows repo. Use whenever you read, write, or edit files, or run any bash command. Covers why to prefer native read/write/edit/glob/grep over bash cat/sed/heredoc, and PowerShell-specific pitfalls (&&, slashes, quotes, encoding).
---

# File tools etiquette + PowerShell (Windows)

This repo runs on Windows. The shell is **PowerShell 5.1** (confirmed: $PSVersionTable.PSVersion = 5.1). The opencode global config sets `"shell": "powershell"`. The project `opencode.json` reinforces it.

## 1. Use native file tools, NOT bash

For any file operation, use the **native opencode tools**, not bash commands.

| Task | Use this | NOT this (bash) | Why |
|------|----------|-----------------|-----|
| Read a file | `read` tool | `cat`, `Get-Content`, `head`, `tail` | bash output mangles non-ASCII ( Cyrillic becomes mojibake ) and returns line numbers + handles long lines |
| Create a file | `write` tool | `echo >`, `Set-Content`, heredoc `cat <<EOF` | native write handles UTF-8, multiline, emoji, backticks, quotes correctly |
| Edit a file | `edit` tool | `sed`, `powershell -replace`, `Set-Content` | native edit does exact string replacement, preserves encoding |
| Find files | `glob` tool | `find`, `Get-ChildItem -Recurse` | glob is faster and returns paths sorted by mtime |
| Search content | `grep` tool | `rg`, `Select-String` | grep is ripgrep-backed, respects .gitignore |

### Demonstrated problem (verified in this build)

Writing Cyrillic to a file via PowerShell, then reading via `Get-Content` in bash, returns mojibake ( `привет мир` -> garbage ). The native `read` tool returns correct UTF-8. This is the single strongest reason to never use bash for reading files in this repo.

### When bash IS the right tool

- `git` commands ( status, diff, add, commit, push )
- `npm`, `bun`, `pnpm`, `yarn` commands
- Running tests, builds, scripts
- `gh` ( GitHub CLI )
- `wsl` to enter the k3s dev cluster
- Anything that is genuinely a terminal / system operation, not a file read/write

bash is for **executing commands**, not for **file I/O**.

## 2. PowerShell syntax rules ( Windows, PS 5.1 )

This is PowerShell 5.1, NOT bash. The following bash-isms break here:

### Forbidden bash-isms

| Bash | PowerShell 5.1 | Note |
|------|----------------|------|
| `cmd1 && cmd2` | `cmd1; if ($?) { cmd2 }` | `&&` is NOT supported in PS 5.1 ( only PS 7+ ). It silently treats `&&` as text or errors. |
| `cmd1 || cmd2` | `cmd1; if (-not $?) { cmd2 }` | `||` likewise unsupported in PS 5.1. |
| `export VAR=val` | `$env:VAR = "val"` | Use env vars with `$env:` prefix. |
| `VAR=val cmd` | `$env:VAR="val"; cmd` | No inline env prefix before a command. |
| `~` in paths | `$env:USERPROFILE` or `"$env:USERPROFILE\..."` | `~` does not always expand in PS paths; prefer the env var. |
| `cat <<EOF ... EOF` | ( do not use ) | Heredocs are bash; for file writes use the `write` tool instead. |
| `echo "..."` to communicate | ( never ) | Never use bash echo to talk to the user. Output text directly in the response. |

### Slashes and paths

- Native opencode tools ( read / write / edit / glob ) accept **both** `\` and `/` on Windows, but for **absolute** paths use **backslashes** `\` ( e.g. `C:\Users\...\file` ). Forward slashes usually work too, but backslashes are the safe default.
- In PowerShell strings, prefer **single quotes** `'...'` for literal paths ( no interpolation, no escape headaches ). Use double quotes `"..."` only when you need variable interpolation.
- Backslash in a double-quoted string is NOT an escape ( PS treats `\` literally, unlike bash ). But `` ` `` ( backtick ) IS the escape char in PS double-quoted strings, so avoid backticks in PS string literals.

### Quoting traps in bash tool calls

When passing a command to the bash tool, the command is a JSON string. Inside it:
- Prefer **single quotes** `'...'` for PowerShell string literals. They avoid JSON-escaping pain with embedded double quotes.
- Avoid embedding **backticks** and **double quotes** together in one long value. If a value has both, the JSON payload can fail to parse with `Unterminated string in JSON`. Workarounds: split into multiple smaller `Add-Content` calls, or write the file with the native `write` tool instead.

### Chain commands safely

```powershell
# dependent ( bash && equivalent )
cmd1; if ($?) { cmd2 }

# independent
cmd1; cmd2

# suppress stderr
cmd 2>$null

# merge stderr into stdout
cmd 2>&1 | Out-Null
```

### Line continuation

- PS line continuation is backtick at end of line `` ` `` — but avoid it; prefer putting each statement on its own line or wrapping in `()`.

## 3. glob pitfalls

- glob **excludes dot-directories** ( `.opencode`, `.github`, `.claude` ) by default. To find files inside them, pass the `path` argument pointing at that directory, e.g. glob with path `C:\...\.opencode` and pattern `**/*.md`.
- glob honors `.gitignore` ( ripgrep-backed ). To search ignored paths, create a `.ignore` file with `!path/` entries.
- Patterns are case-insensitive on Windows but keep them lowercase for portability.

## 4. read / write / edit specifics

- `read`: pass an absolute path. Supports `offset` and `limit` for large files. Always prefer this over bash cat.
- `write`: overwrites. You MUST `read` an existing file first before `write` ( enforced ). For new files, just `write`.
- `edit`: exact string replacement. `oldString` must be unique in the file ( or use `replaceAll` ). Match indentation exactly as shown after the line-number prefix.
- These tools return **line-number-prefixed** output ( e.g. `1: foo` ). When copy-pasting content into `edit` `oldString`, strip the `N: ` prefix — the prefix is not part of the file.

## 5. Encoding

- Files are UTF-8. Native tools preserve UTF-8. PowerShell `Set-Content` / `Add-Content` default to UTF-8 with BOM in PS 5.1 when `-Encoding utf8` is used; prefer the native `write`/`edit` tools to avoid BOM surprises.
- Never use bash `Get-Content` / `cat` to inspect non-ASCII content — it will show mojibake and you will misread the file.

## 6. Quick decision table

| You want to... | Use |
|----------------|-----|
| See file contents | `read` |
| Create a new file | `write` |
| Change a few lines in an existing file | `edit` |
| Find a file by name | `glob` |
| Find text inside files | `grep` |
| Run a shell command ( git, npm, test, wsl ) | `bash` |
| Load reusable instructions | `skill` |
