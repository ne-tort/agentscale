import type { Translate } from "../../i18n/catalogue";
import type { Locale } from "../../preferences/settings";
import type {
  AiKeyProbe,
  AvailableAiKey,
  KeyAvailability,
  LiveModel,
  LiveModelsResponse,
  ProbePresentation,
} from "./types";

export function providerOptions(keys: AvailableAiKey[]): string[] {
  return [...new Set(keys.map((key) => key.provider.trim()).filter(Boolean))].sort((left, right) => left.localeCompare(right));
}

function searchableKeyText(key: AvailableAiKey): string {
  return [key.name, key.id, key.provider, key.api_kind].filter(Boolean).join(" ").toLocaleLowerCase();
}

function searchableModelText(model: LiveModel): string {
  return [model.id, model.label, model.publisher].filter(Boolean).join(" ").toLocaleLowerCase();
}

export function filterKeys(keys: AvailableAiKey[], query: string, provider = "all"): AvailableAiKey[] {
  const needle = query.trim().toLocaleLowerCase();
  return keys.filter((key) => {
    const providerMatches = provider === "all" || key.provider === provider;
    return providerMatches && (!needle || searchableKeyText(key).includes(needle));
  });
}

export function filterModels(models: LiveModel[], query: string): LiveModel[] {
  const needle = query.trim().toLocaleLowerCase();
  return models.filter((model) => !needle || searchableModelText(model).includes(needle));
}

function probeIsStale(probe: AiKeyProbe, now: number): boolean {
  if (probe.stale === true || probe.status === "stale") return true;
  if (!probe.checked_at) return false;
  const checkedAt = Date.parse(probe.checked_at);
  return Number.isFinite(checkedAt) && now - checkedAt > 24 * 60 * 60 * 1000;
}

export function probePresentation(probe?: AiKeyProbe, now = Date.now()): ProbePresentation {
  if (!probe || probe.status === "none") return "unchecked";
  if (probe.status === "error" || probe.status === "failed") return "error";
  if (probe.status === "unavailable") return "unavailable";
  if (probeIsStale(probe, now)) return "stale";
  if (probe.status === "ok" || probe.status === "success") return "verified";
  return "unchecked";
}

/** The support code a person can quote, appended only when the backend sent one. */
export function probeCodeNote(probe: AiKeyProbe | undefined, t: Translate): string {
  return probe?.error_code ? ` ${t("models.errorTrace", { code: probe.error_code })}` : "";
}

export function keyAvailability(key: AvailableAiKey, t: Translate, now = Date.now()): KeyAvailability {
  if (key.status && key.status !== "active") {
    return { tone: "danger", label: t("models.availabilityUnavailable"), detail: t("models.availabilityUnavailableDetail") };
  }
  if (!key.has_secret) {
    return { tone: "danger", label: t("models.availabilityNoSecret"), detail: t("models.availabilityNoSecretDetail") };
  }
  const probe = probePresentation(key.last_probe, now);
  if (probe === "error") {
    return {
      tone: "warning",
      label: t("models.availabilityCheckError"),
      detail: `${t("models.availabilityCheckErrorDetail")}${probeCodeNote(key.last_probe, t)}`,
    };
  }
  if (probe === "unavailable") {
    return { tone: "warning", label: t("models.availabilityCheckUnavailable"), detail: t("models.availabilityCheckUnavailableDetail") };
  }
  if (probe === "stale") {
    return {
      tone: "warning",
      label: t("models.availabilityStale"),
      detail: `${t("models.availabilityStaleDetail")}${probeCodeNote(key.last_probe, t)}`,
    };
  }
  if (probe === "verified") return { tone: "positive", label: t("models.availabilityVerified"), detail: t("models.availabilityVerifiedDetail") };
  return { tone: "neutral", label: t("models.availabilityUnchecked"), detail: t("models.availabilityUncheckedDetail") };
}

export function modelCapabilityLabel(model: LiveModel, t: Translate): { tone: "positive" | "warning" | "neutral"; label: string } {
  if (model.catalog_matched === true) return { tone: "positive", label: t("models.capabilityConfirmed") };
  if (model.catalog_matched === false) return { tone: "warning", label: t("models.capabilityUnconfirmed") };
  return { tone: "neutral", label: t("models.capabilityLive") };
}

export function modelsAvailability(response: LiveModelsResponse | undefined, t: Translate): { tone: "positive" | "warning" | "neutral"; label: string; detail: string } {
  if (!response) {
    return { tone: "neutral", label: t("models.modelsNotLoaded"), detail: t("models.modelsNotLoadedDetail") };
  }
  if (response.stale || response.error_code) {
    return {
      tone: "warning",
      label: response.stale ? t("models.modelsStale") : t("models.modelsSourceStale"),
      detail: response.error_code
        ? `${t("models.modelsStaleDetail")} ${t("models.errorTrace", { code: response.error_code })}`
        : t("models.modelsStaleDetail"),
    };
  }
  if (response.source !== "live") return { tone: "warning", label: t("models.modelsSourceStale"), detail: t("models.modelsSourceStaleDetail") };
  if (!response.models.length) return { tone: "warning", label: t("models.modelsNone"), detail: t("models.modelsNoneDetail") };
  return { tone: "positive", label: t("models.modelsLive"), detail: t("models.modelsLiveDetail", { count: response.models.length }) };
}

export function formatContext(value: number | undefined, t: Translate, locale: Locale): string {
  if (value === undefined) return "";
  return t("models.contextTokens", { count: new Intl.NumberFormat(locale).format(value) });
}

export function formatPrice(value?: number): string {
  if (value === undefined) return "";
  return `$${value.toFixed(2)} / 1M`;
}
