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

export function keyAvailability(key: AvailableAiKey, now = Date.now()): KeyAvailability {
  if (key.status && key.status !== "active") {
    return { tone: "danger", label: "Недоступен", detail: `Статус ключа: ${key.status}.` };
  }
  if (!key.has_secret) {
    return { tone: "danger", label: "Нет секрета", detail: "Backend не подтвердил настроенный секрет." };
  }
  const probe = probePresentation(key.last_probe, now);
  if (probe === "error") {
    return { tone: "warning", label: "Ошибка проверки", detail: `Проверка ключа завершилась ошибкой${key.last_probe?.error_code ? ` (${key.last_probe.error_code})` : ""}.` };
  }
  if (probe === "unavailable") {
    return { tone: "warning", label: "Проверка недоступна", detail: "Provider probe не подтвердил capability сейчас." };
  }
  if (probe === "stale") {
    return { tone: "warning", label: "Проверка устарела", detail: `Последний probe старше 24 часов или помечен stale${key.last_probe?.error_code ? ` (${key.last_probe.error_code})` : ""}.` };
  }
  if (probe === "verified") return { tone: "positive", label: "Проверен", detail: "Последняя проверка provider завершилась успешно." };
  return { tone: "neutral", label: "Проверка не выполнялась", detail: "Live capability ещё не подтверждена probe." };
}

export function modelCapabilityLabel(model: LiveModel): { tone: "positive" | "warning" | "neutral"; label: string } {
  if (model.catalog_matched === true) return { tone: "positive", label: "Совместимость подтверждена" };
  if (model.catalog_matched === false) return { tone: "warning", label: "Совместимость не подтверждена" };
  return { tone: "neutral", label: "Capability live" };
}

export function modelsAvailability(response?: LiveModelsResponse): { tone: "positive" | "warning" | "neutral"; label: string; detail: string } {
  if (!response) return { tone: "neutral", label: "Не загружено", detail: "Выберите сохранённый key, чтобы получить live models." };
  if (response.stale || response.error_code) return { tone: "warning", label: "Live список устарел или с ошибкой", detail: response.error_code ? `Backend сообщил ${response.error_code}; перечитайте live capability перед выбором.` : "Backend пометил список моделей stale; перечитайте его перед выбором." };
  if (response.source !== "live") return { tone: "warning", label: "Live models недоступны", detail: "Список не подтверждён live runtime provider." };
  if (!response.models.length) return { tone: "warning", label: "Модели не найдены", detail: "Provider не вернул доступных моделей для выбранного key." };
  return { tone: "positive", label: "Live capability", detail: `${response.models.length} моделей подтверждены endpoint /models/live.` };
}

export function formatContext(value?: number): string {
  if (value === undefined) return "";
  return `${new Intl.NumberFormat("ru-RU").format(value)} токенов контекста`;
}

export function formatPrice(value?: number): string {
  if (value === undefined) return "";
  return `$${value.toFixed(2)} / 1M`;
}
