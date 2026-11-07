import { describe, expect, it } from "vitest";
import { buildCatalogue, createTranslator } from "./catalogue";
import { defineStrings, format, pluralForm } from "./core";

describe("catalogue", () => {
  it("has every key in both locales", () => {
    const catalogue = buildCatalogue();
    const ru = Object.keys(catalogue.ru).sort();
    const en = Object.keys(catalogue.en).sort();
    expect(ru.length).toBeGreaterThan(40);
    expect(en).toEqual(ru);
  });

  it("declares no empty translation", () => {
    const catalogue = buildCatalogue();
    for (const locale of ["ru", "en"] as const) {
      for (const [key, value] of Object.entries(catalogue[locale])) {
        expect(value.trim(), `${locale}:${key}`).not.toBe("");
      }
    }
  });

  it("namespaces each unit by its file name", () => {
    const catalogue = buildCatalogue();
    expect(catalogue.ru["admin.tabCompanies"]).toBe("Компании");
    expect(catalogue.ru["login.title"]).toBe("Вход");
    expect(catalogue.en["login.title"]).toBe("Sign in");
  });

  it("rejects a unit whose locales drift apart", () => {
    // A missing translation must fail at load, not render an English word in a
    // Russian screen.
    expect(() => defineStrings({ ru: { a: "А", b: "Б" }, en: { a: "A" } })).toThrow(/diverge/);
  });

  it("merges an area under its own namespace", () => {
    const catalogue = buildCatalogue({
      "./units/stray.ts": { default: { ru: { only: "Только" }, en: { only: "Only" } } },
    });
    expect(catalogue.ru["stray.only"]).toBe("Только");
    expect(catalogue.en["stray.only"]).toBe("Only");
  });
});

describe("translator", () => {
  const catalogue = buildCatalogue();

  it("substitutes named placeholders", () => {
    const ru = createTranslator("ru", catalogue);
    expect(ru("admin.companyCreated", { name: "Acme" })).toBe("Компания «Acme» создана");
  });

  it("leaves an unknown placeholder alone rather than printing undefined", () => {
    expect(format("Hello {name}, you have {count}", { name: "Ann" })).toBe("Hello Ann, you have {count}");
  });

  it("falls back to readable words, never to a key path", () => {
    const ru = createTranslator("ru", catalogue);
    const fallback = ru("nowhere.newCompany");
    expect(fallback).toBe("New company");
    expect(fallback).not.toContain(".");
    expect(fallback).not.toContain("nowhere");
  });
});

describe("plural forms", () => {
  it("picks the form Russian needs", () => {
    expect([1, 2, 5, 11, 21, 22].map((count) => pluralForm("ru", count))).toEqual([
      "one", "few", "many", "many", "one", "few",
    ]);
  });

  it("picks the form English needs", () => {
    // English has two categories; `few` is the slot for "not singular", which
    // is what an English unit writes its plural into.
    expect([1, 2, 5].map((count) => pluralForm("en", count))).toEqual(["one", "few", "few"]);
  });
});
