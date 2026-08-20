# M03 — MCP tools: промпты

Server: `prodavan-prompts`

## list_prompt_files

```json
{
  "cabinet_id": "optional — default active",
  "prefix": "profiles/kp/"
}
```

## read_prompt_file

```json
{ "path": "AGENTS.md" }
```

Returns content + sha256 (not for huge dumps in chat — truncate with offset/limit optional).

## get_prompt_version

```json
{ "version_id": "ver_01JXYZ" }
```

## diff_prompt_version

```json
{
  "version_id": "ver_01JXYZ",
  "against": "working"
}
```

## save_prompt_file (operator role)

```json
{
  "path": "profiles/kp/04-search.md",
  "content": "...",
  "etag": "\"abc...\""
}
```

Agent **read-mostly**; write requires elevated scope `prompts:write` (usually operator UI, not autonomous agent).

## Policy

- Agent bootstrap: read AGENTS.md + current profile module for phase
- Autonomous edits discouraged — audit if enabled via config flag

## Negative test IDs

| Test ID | Call |
| --- | --- |
| NEG-PRM-MCP-001 | read other cabinet prompts |
| NEG-PRM-MCP-002 | save without etag |
