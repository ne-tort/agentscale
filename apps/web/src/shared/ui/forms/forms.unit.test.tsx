import { describe, expect, it } from "vitest";
import { filterComboboxOptions, type SearchableOption } from "./searchable-combobox";

describe("filterComboboxOptions", () => {
  const options: SearchableOption[] = [
    { value: "north", label: "Northwind" },
    { value: "signal", label: "Signal Lab", keywords: ["observability", "monitoring"] },
    { value: "south", label: "Southstar", disabled: true },
  ];

  it("matches labels and keywords case-insensitively with trimmed queries", () => {
    expect(filterComboboxOptions(options, "  NORTH ")).toEqual([options[0]]);
    expect(filterComboboxOptions(options, "MONITORING")).toEqual([options[1]]);
  });

  it("returns all options for blank input and an empty list for misses", () => {
    expect(filterComboboxOptions(options, "  ")).toEqual(options);
    expect(filterComboboxOptions(options, "unknown")).toEqual([]);
  });
});