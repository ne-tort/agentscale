import { describe, expect, it } from "vitest";
import { createTranslator } from "../../i18n/catalogue";
import {
  filterKeys,
  filterModels,
  formatContext,
  keyAvailability,
  modelCapabilityLabel,
  modelsAvailability,
  probePresentation,
} from "./view-model";
import type { AvailableAiKey, LiveModel } from "./types";

const t = createTranslator("ru");

const key = (overrides: Partial<AvailableAiKey> = {}): AvailableAiKey => ({
  id: "key-1",
  name: "Team OpenAI",
  provider: "openai",
  api_kind: "openai_api",
  status: "active",
  has_secret: true,
  ...overrides,
});

const model = (overrides: Partial<LiveModel> = {}): LiveModel => ({
  id: "gpt-5",
  label: "GPT-5",
  catalog_matched: true,
  ...overrides,
});

describe("models capability acceptance view model", () => {
  it("searches keys by provider/name/id/API kind and models by label/id/publisher", () => {
    const keys = [key(), key({ id: "key-2", name: "Claude team", provider: "claude_code", api_kind: "claude_sdk" })];
    const models = [model(), model({ id: "claude-sonnet", label: "Claude Sonnet", publisher: "Anthropic" })];

    expect(filterKeys(keys, "claude")).toHaveLength(1);
    expect(filterKeys(keys, "key-1", "openai")).toHaveLength(1);
    expect(filterKeys(keys, "claude_sdk", "claude_code")).toEqual([keys[1]]);
    expect(filterKeys(keys, "openai", "claude_code")).toHaveLength(0);
    expect(filterModels(models, "anthropic")).toEqual([models[1]]);
  });

  it("distinguishes fresh, stale, error, unavailable and unchecked probes", () => {
    const now = Date.parse("2026-11-02T12:00:00Z");
    expect(probePresentation({ status: "ok", models: [], checked_at: "2026-11-02T11:00:00Z" }, now)).toBe("verified");
    expect(probePresentation({ status: "ok", models: [], checked_at: "2026-10-31T11:00:00Z" }, now)).toBe("stale");
    expect(probePresentation({ status: "error", models: [], stale: true }, now)).toBe("error");
    expect(probePresentation({ status: "unavailable", models: [] }, now)).toBe("unavailable");
    expect(probePresentation(undefined, now)).toBe("unchecked");

    expect(keyAvailability(key({ last_probe: { status: "ok", models: [] } }), t)).toMatchObject({ tone: "positive", label: "Проверен" });
    expect(keyAvailability(key({ last_probe: { status: "ok", models: [], stale: true } }), t)).toMatchObject({ tone: "warning", label: "Проверка устарела" });
    expect(keyAvailability(key({ last_probe: { status: "error", models: [], error_code: "PROBE_TIMEOUT" } }), t)).toMatchObject({ tone: "warning", label: "Проверка не прошла" });
  });

  it("requires live source and catalog match to claim capability", () => {
    expect(modelsAvailability({ models: [model()], source: "live" }, t)).toMatchObject({ tone: "positive", label: "Модели подтверждены" });
    expect(modelsAvailability({ models: [model()], source: "catalog" }, t)).toMatchObject({ tone: "warning", label: "Список не подтверждён" });
    expect(modelsAvailability({ models: [model()], source: "live", error_code: "MODELS_UNAVAILABLE" }, t)).toMatchObject({ tone: "warning" });
    expect(modelCapabilityLabel(model({ catalog_matched: true }), t)).toMatchObject({ tone: "positive" });
    expect(modelCapabilityLabel(model({ catalog_matched: false }), t)).toMatchObject({ tone: "warning", label: "Совместимость не подтверждена" });
  });

  it("spells out the context window in the reader's own words", () => {
    // `Intl` groups thousands with a non-breaking space; normalising it keeps a typographic
    // choice from reading as a copy regression.
    const plain = (value: string) => value.replace(/\u00a0/g, " ");
    expect(plain(formatContext(128000, t, "ru"))).toBe("128 000 токенов контекста");
    expect(formatContext(undefined, t, "ru")).toBe("");
  });
});
