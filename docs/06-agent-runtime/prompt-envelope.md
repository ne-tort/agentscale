# Prompt envelope

Обёртка пользовательского сообщения перед отправкой в LLM-провайдер. Наследует практику Commerce F-09 (`wrapUserPrompt`) — system instructions + locale hint + structured user block.

---

## Purpose

1. **Inject platform rules** без изменения user-visible chat text в UI
2. **Locale hint** — ответ на русском по умолчанию
3. **Structured delimiters** — модель чётко отделяет operator request от system context
4. **Security boundary marker** — downstream filters могут парсить `<user_request>`

---

## Format

```text
{envelope_intro}

{answer_locale_instruction}

<user_request>
{raw_user_text}
</user_request>
```

### Components

| Part | Source | Example (ru) |
|------|--------|--------------|
| `envelope_intro` | i18n template | «Ты агент платформы Prodavan в контексте проекта закупок.» |
| `answer_locale_instruction` | locale | «Отвечай на русском языке.» |
| `raw_user_text` | UI composer | Unmodified operator input |

---

## Implementation

### Python (Prodavan)

```python
# application/agent/prompt_envelope.py

LOCALE_ANSWER_KEYS = {
    "ru": "Отвечай на русском языке.",
    "en": "Answer in English.",
    "zh": "请用中文回答。",
}

def wrap_user_prompt(raw: str, locale: str = "ru") -> str:
    intro = _i18n.t("prompt.envelope_intro")
    answer = LOCALE_ANSWER_KEYS.get(locale, LOCALE_ANSWER_KEYS["en"])
    return "\n".join([
        intro,
        answer,
        "<user_request>",
        raw,
        "</user_request>",
    ])
```

### Commerce reference (TypeScript)

```typescript
// bot/src/core/orchestrator/promptEnvelope.ts
export function wrapUserPrompt(raw: string, locale: Locale): string {
  return [
    i18n.t("prompt.envelopeIntro"),
    i18n.t(langKey),
    "<user_request>",
    raw,
    "</user_request>",
  ].join("\n");
}
```

---

## What NOT to include in envelope

| Data | Reason |
|------|--------|
| S4B password | Secret leak to model logs |
| JWT tokens | Credential leak |
| Other tenant/project data | Cross-context leak |
| Full MCP response bodies | PII / size |
| System prompt AGENTS.md | Already in workspace `settingSources: project` |

AGENTS.md загружается Cursor SDK из workspace — **не дублировать** в envelope.

---

## System prompt layering

```text
Layer 1: Platform guardrails (hardcoded, minimal)
Layer 2: AGENTS.md (from cabinet prompts/, M03)
Layer 3: Profile modules (profiles/kp/*.md)
Layer 4: Prompt envelope (per message)
Layer 5: <user_request> raw text
```

Orchestrator responsibility:
- Layers 1-3 — at session create via workspace files
- Layer 4-5 — at each `send()`

---

## UI behavior

Flutter chat displays **only** `raw_user_text` in user bubble.

Database `agent.messages`:
- `content_text` = raw (what operator typed)
- `content_parts` may store envelope hash for audit, not full envelope to UI

Optional debug mode (M07, admin): show wrapped prompt in debug drawer.

---

## Multimodal

Images attached separately — **outside** `<user_request>` text block:

```python
await session.send(
    wrap_user_prompt(text),
    images=[ImagePart(data=..., mime_type="image/png")],
)
```

Commerce: `RuntimeAgent.send(text, { images })`.

---

## Locale selection

Priority:
1. User preference (M08 profile)
2. Cabinet timezone/locale default
3. Tenant default
4. `ru`

---

## Testing

```python
def test_wrap_preserves_raw():
    raw = "Обработай спеку"
    wrapped = wrap_user_prompt(raw, "ru")
    assert raw in wrapped
    assert wrapped.index("<user_request>") < wrapped.index(raw)
    assert "Отвечай на русском" in wrapped

def test_no_secrets_in_envelope():
    raw = "password=secret"
    # raw passed through — but separate DLP scan on upload paths
```

Commerce: `bot/tests/unit/promptEnvelope.test.ts`.

---

## Provider-specific notes

| Provider | Notes |
|----------|-------|
| Cursor SDK | Pass wrapped string to `send()` |
| Codex CLI | stdin to `codex exec` |
| Claude Code CLI | `-p` argument |

All adapters call shared `wrap_user_prompt()` — single implementation.

---

## Связанные документы

- [cursor-sdk-adapter.md](cursor-sdk-adapter.md)
- [provider-port.md](provider-port.md)
- [../03-modules/M03-prompts/](../03-modules/M03-prompts/)
