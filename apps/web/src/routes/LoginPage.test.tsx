// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../app/context";
import {
  LOCALE_STORAGE_KEY,
  THEME_STORAGE_KEY,
  parseLocale,
  parseTheme,
  resolveTheme,
} from "../preferences/settings";
import { STRINGS } from "../preferences/strings";
import { LoginPage } from "./LoginPage";

let host: HTMLDivElement;
let root: Root | undefined;

function stubMatchMedia(matches: boolean) {
  const listeners = new Set<() => void>();
  const query = {
    matches,
    media: "(prefers-color-scheme: dark)",
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
  };
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: () => query,
  });
  return {
    flip(next: boolean) {
      query.matches = next;
      listeners.forEach((listener) => listener());
    },
  };
}

async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(
      <MemoryRouter initialEntries={["/login"]}>
        <AppProviders>
          <LoginPage />
        </AppProviders>
      </MemoryRouter>,
    );
  });
}

function text(): string {
  return document.body.textContent ?? "";
}

function button(label: string): HTMLButtonElement {
  const match = [...document.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!match) throw new Error(`no button labelled ${label}`);
  return match as HTMLButtonElement;
}

async function press(target: HTMLElement) {
  await act(async () => {
    target.click();
  });
}

beforeEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  vi.unstubAllGlobals();
});

describe("preferences", () => {
  it("defaults to Russian and the system theme", () => {
    expect(parseLocale(undefined)).toBeUndefined();
    expect(parseTheme(undefined)).toBeUndefined();
    expect(resolveTheme("system", undefined)).toBe("light");
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("keeps both locales complete", () => {
    expect(Object.keys(STRINGS.ru).sort()).toEqual(Object.keys(STRINGS.en).sort());
    expect(STRINGS.ru.signIn).toBe("Вход");
    expect(STRINGS.ru.login).toBe("Логин");
    expect(STRINGS.ru.password).toBe("Пароль");
  });
});

describe("LoginPage", () => {
  it("shows only the heading, both fields and the submit row", async () => {
    stubMatchMedia(false);
    await mount();

    expect(text()).toContain("Вход");
    // No marketing subtitle: the card says what it is and nothing else.
    expect(text()).not.toContain("Employee workspace");
    expect(document.querySelector(".login-title")?.textContent).toBe("Вход");
    expect(document.querySelectorAll(".login-page input")).toHaveLength(2);
  });

  it("opens with the seeded platform admin instead of empty fields", async () => {
    stubMatchMedia(false);
    await mount();

    const inputs = document.querySelectorAll<HTMLInputElement>(".login-page input");
    expect(inputs[0].value).toBe("admin");
    expect(inputs[1].value).toBe("admin");
  });

  it("offers language and theme behind the settings button", async () => {
    stubMatchMedia(false);
    await mount();

    const gear = document.querySelector<HTMLButtonElement>(".login-header .icon-button");
    expect(gear?.getAttribute("aria-label")).toBe("Настройки");
    await press(gear!);

    const panel = document.querySelector('[role="dialog"]');
    expect(panel).not.toBeNull();
    expect(panel?.textContent).toContain("Язык");
    expect(panel?.textContent).toContain("Тема");
    // Russian and system appearance are the defaults, so those are the pressed
    // choices when the menu first opens.
    expect(button("Русский").getAttribute("aria-pressed")).toBe("true");
    expect(button("Системная").getAttribute("aria-pressed")).toBe("true");
  });

  it("switches the interface language and remembers the choice", async () => {
    stubMatchMedia(false);
    await mount();

    await press(document.querySelector<HTMLButtonElement>(".login-header .icon-button")!);
    await press(button("English"));

    expect(document.querySelector(".login-title")?.textContent).toBe("Sign in");
    expect(text()).toContain("Login");
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
  });

  it("applies a manual theme and keeps following the system when asked to", async () => {
    const media = stubMatchMedia(false);
    await mount();

    expect(document.documentElement.dataset.theme).toBe("light");

    await act(async () => media.flip(true));
    expect(document.documentElement.dataset.theme).toBe("dark");

    await press(document.querySelector<HTMLButtonElement>(".login-header .icon-button")!);
    await press(button("Светлая"));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");

    // An explicit choice wins over the OS.
    await act(async () => media.flip(true));
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("closes the settings menu on Escape", async () => {
    stubMatchMedia(false);
    await mount();

    await press(document.querySelector<HTMLButtonElement>(".login-header .icon-button")!);
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });
});
