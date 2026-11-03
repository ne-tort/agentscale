import { describe, expect, it } from "vitest";
import { display, filterPage } from "./view-model";

type Row = { id: string; name: string; status: string };
const rows: Row[] = Array.from({ length: 23 }, (_, index) => ({ id: `row-${index + 1}`, name: `Company ${index + 1}`, status: index % 2 ? "paused" : "active" }));

describe("contour view model", () => {
  it("filters by the requested columns and clamps pagination", () => {
    expect(filterPage(rows, "company 2", 1, 10, ["name"]).items.map((row) => row.id)).toEqual(["row-2", "row-20", "row-21", "row-22", "row-23"]);
    expect(filterPage(rows, "paused", 99, 10, ["status"]).page).toBe(2);
    expect(filterPage(rows, "missing", 1, 10, ["name"]).total).toBe(0);
  });

  it("keeps display output safe for empty and structured values", () => {
    expect(display(undefined)).toBe("—");
    expect(display(false)).toBe("No");
    expect(display({ provider: "openai" })).toBe('{"provider":"openai"}');
  });
});
