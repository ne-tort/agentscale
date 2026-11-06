import { describe, expect, it } from "vitest";
import { filterSearchItems } from "./data";

describe("filterSearchItems", () => {
  const items = [
    { id: "a", name: "Northwind" },
    { id: "b", name: "Signal Lab" },
    { id: "c", name: "Northstar" },
  ];

  it("matches case-insensitively and trims query whitespace", () => {
    expect(filterSearchItems(items, "  NORTH ", (item) => item.name)).toEqual([items[0], items[2]]);
  });

  it("returns all items for a blank query and an empty array for no matches", () => {
    expect(filterSearchItems(items, "  ", (item) => item.name)).toEqual(items);
    expect(filterSearchItems(items, "missing", (item) => item.name)).toEqual([]);
  });
});