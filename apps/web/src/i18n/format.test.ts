import { describe, expect, it } from "vitest";

import { formatDate, formatDateTime, formatNumber, formatTime, toDate } from "./format";

/**
 * These assertions are about which locale is used, not about the exact ICU
 * output — Node and browsers disagree on spacing and the "г." suffix. What must
 * hold is that the chosen locale decides, never the host environment.
 */
describe("dates follow the chosen locale", () => {
  const moment = "2026-11-12T14:05:00Z";

  it("renders Russian and English differently for the same instant", () => {
    const ru = formatDateTime(moment, "ru");
    const en = formatDateTime(moment, "en");
    expect(ru).not.toBe(en);
    expect(ru).toMatch(/\d{1,2}/);
    expect(en).toMatch(/\d{1,2}/);
  });

  it("uses the Russian month name for a Russian locale", () => {
    expect(formatDate(moment, "ru").toLowerCase()).toMatch(/ноя/);
    expect(formatDate(moment, "en").toLowerCase()).toMatch(/nov/);
  });

  it("formats a time without a day", () => {
    // Russian uses a 24-hour clock, English conventionally a 12-hour one; both
    // must carry the clock time and nothing about the day.
    expect(formatTime(moment, "ru")).toMatch(/^\d{2}:\d{2}$/);
    expect(formatTime(moment, "en")).toMatch(/^\d{1,2}:\d{2}(\s?[AP]M)?$/i);
    expect(formatTime(moment, "ru")).not.toMatch(/2026|ноя/);
  });

  it("returns an empty string rather than 'Invalid Date'", () => {
    expect(formatDateTime(undefined, "ru")).toBe("");
    expect(formatDateTime(null, "ru")).toBe("");
    expect(formatDateTime("", "ru")).toBe("");
    expect(formatDateTime("not a date", "ru")).toBe("");
    expect(formatDate("not a date", "en")).toBe("");
    expect(formatTime("not a date", "en")).toBe("");
  });

  it("accepts a Date, a number and an ISO string alike", () => {
    const iso = "2026-11-12T14:05:00Z";
    const fromDate = formatDateTime(new Date(iso), "ru");
    const fromNumber = formatDateTime(Date.parse(iso), "ru");
    const fromString = formatDateTime(iso, "ru");
    expect(fromDate).toBe(fromString);
    expect(fromNumber).toBe(fromString);
  });

  it("formats numbers with the locale's separators", () => {
    expect(formatNumber(1234.5, "ru")).toMatch(/1\s?234,5/);
    expect(formatNumber(1234.5, "en")).toBe("1,234.5");
    expect(formatNumber(undefined, "ru")).toBe("");
    expect(formatNumber(Number.NaN, "en")).toBe("");
  });

  it("toDate rejects what Date cannot read", () => {
    expect(toDate("2026-11-12T14:05:00Z")).toBeInstanceOf(Date);
    expect(toDate("nope")).toBeUndefined();
    expect(toDate(null)).toBeUndefined();
  });
});
