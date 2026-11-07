import { describe, expect, it } from "vitest";
import { moduleEvidence, templateRows } from "./view-model";

describe("module admin evidence", () => {
  it("uses only effective source, version and rights returned by the company catalog API", () => {
    expect(moduleEvidence({
      id: "mod-1",
      source: "platform_assigned",
      version: "2.4.1",
      writable: false,
      bind_kind: "global",
      child_may_edit: false,
    }, "company")).toEqual({
      source: "platform_assigned",
      version: "2.4.1",
      rights: "readOnly",
      bindKind: "global",
      childMayEdit: false,
      gaps: [],
    });
  });

  it("names the missing values as keys instead of carrying developer sentences", () => {
    expect(moduleEvidence({ id: "mod-1", name: "Catalog row" }, "admin").gaps).toEqual([
      "source",
      "version",
      "rights",
    ]);
  });

  it("shows admin project rights only when the binding response supplies them", () => {
    expect(moduleEvidence({ id: "mod-1", child_may_edit: false }, "admin")).toMatchObject({
      rights: "childReadOnly",
      childMayEdit: false,
    });
  });

  it("includes only active available template rows", () => {
    expect(templateRows([
      { id: "t1", body: { name: "Quote", active: true } },
      { id: "t2", body: { name: "Archived", active: false } },
      { id: "t3", body: "invalid" },
    ])).toEqual([{ id: "t1", body: { name: "Quote", active: true } }]);
  });
});
